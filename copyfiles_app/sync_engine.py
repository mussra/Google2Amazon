"""
Motor de sincronización: escanea un directorio origen, aplica filtros
y copia/mueve los archivos al destino aplicando un patrón de
renombrado dinámico, con checkpoint para poder reanudar tras un corte.

Mejoras v3:
  - Verificación post-copia: compara st_size origen vs destino antes
    de marcar el archivo como completado. Un disco lleno puede producir
    un truncado sin lanzar OSError en copy2.
  - Escritura de checkpoint batched (cada 25 archivos) con escritura
    atómica (.tmp + os.replace) para evitar corrupción si el proceso
    muere a mitad de escritura del JSON.
  - Modo mover: el origen solo se elimina DESPUÉS de verificar que el
    tamaño en destino es correcto.
  - Acumulador de informe: lista de entradas que el llamador puede
    recoger via get_report() para generar el PDF/CSV final.
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import re
import shutil
import socket
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .constants import CHK_SINCRO, DICCIONARIO_EXTENSIONES
from .models import AppConfig
from .persistence import HistoryManager

logger = logging.getLogger(__name__)

LogCallback = Callable[[str, str], None]
ProgressCallback = Callable[[int], None]

_INVALID_NAME_CHARS_RE = re.compile(r'[\\*?:"<>|]')
_CHECKPOINT_BATCH = 25   # escribir checkpoint cada N archivos


@dataclass
class ReportEntry:
    timestamp: str
    accion: str          # COPIADO | MOVIDO | OMITIDO | ERROR
    origen: str
    destino: str
    bytes_size: int
    ok: bool


class SyncEngine:
    """Motor de sincronización de archivos entre carpeta origen y destino."""

    def __init__(self, config: AppConfig, on_log: LogCallback,
                 on_progress: ProgressCallback, history: HistoryManager | None = None,
                 checkpoint_path: Path | None = None,
                 on_finished: Callable[[], None] | None = None):
        self.cfg = config
        self.origen = Path(config.origen).resolve()
        self.destino = Path(config.destino).resolve()
        self.on_log = on_log
        self.on_progress = on_progress
        self.on_finished = on_finished
        self.history = history or HistoryManager()

        if checkpoint_path is None:
            import hashlib as _hl
            slug = _hl.md5(str(self.destino).encode()).hexdigest()[:10]
            checkpoint_path = CHK_SINCRO.parent / f"CopyFiles_checkpoint_sincro_{slug}.json"
        self.checkpoint_path = checkpoint_path

        self.activo = False
        self._completados: set[str] = set()
        self._seq_lock = threading.Lock()
        self._checkpoint_dirty = 0   # contador de modificaciones sin flush
        self._report: list[ReportEntry] = []
        self._cargar_checkpoint()

    def get_report(self) -> list[ReportEntry]:
        return list(self._report)

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------
    def _cargar_checkpoint(self) -> None:
        if not self.checkpoint_path.exists():
            return
        try:
            with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("origen") == str(self.origen) and data.get("destino") == str(self.destino):
                self._completados = set(data.get("completados", []))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Checkpoint de sincronización ilegible, se ignora: %s", exc)

    def _registrar_checkpoint(self, ruta_str: str, force: bool = False) -> None:
        self._completados.add(ruta_str)
        self._checkpoint_dirty += 1
        if not force and self._checkpoint_dirty < _CHECKPOINT_BATCH:
            return
        self._flush_checkpoint()

    def _flush_checkpoint(self) -> None:
        """Escritura atómica del checkpoint (.tmp + os.replace)."""
        tmp = self.checkpoint_path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "origen": str(self.origen),
                        "destino": str(self.destino),
                        "completados": list(self._completados),
                    },
                    f,
                )
            os.replace(tmp, self.checkpoint_path)
            self._checkpoint_dirty = 0
        except OSError as exc:
            logger.warning("No se pudo guardar el checkpoint: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def limpiar_checkpoint(self) -> None:
        self._flush_checkpoint()   # asegurar que el último batch queda escrito
        try:
            if self.checkpoint_path.exists():
                self.checkpoint_path.unlink()
        except OSError as exc:
            logger.warning("No se pudo eliminar el checkpoint: %s", exc)

    # ------------------------------------------------------------------
    # Filtros
    # ------------------------------------------------------------------
    def _pasa_filtros(self, archivo: Path) -> bool:
        if self.cfg.regex_excluir:
            try:
                if re.search(self.cfg.regex_excluir, archivo.name):
                    return False
            except re.error as exc:
                logger.debug("RegEx de exclusión inválida (%s): %s", self.cfg.regex_excluir, exc)

        try:
            tamano_mb = archivo.stat().st_size / (1024 * 1024)
            min_mb, max_mb = self.cfg.size_filter_mb()
            if min_mb is not None and tamano_mb < min_mb:
                return False
            if max_mb is not None and tamano_mb > max_mb:
                return False
        except OSError as exc:
            logger.debug("No se pudo leer tamaño de %s: %s", archivo, exc)

        ext = archivo.suffix.lower()
        if ext in DICCIONARIO_EXTENSIONES["fotos"]:
            return self.cfg.chk_fotos
        if ext in DICCIONARIO_EXTENSIONES["videos"]:
            return self.cfg.chk_videos
        if ext in DICCIONARIO_EXTENSIONES["documentos"]:
            return self.cfg.chk_docs
        return self.cfg.chk_otros

    # ------------------------------------------------------------------
    # Resolución de nombres dinámicos
    # ------------------------------------------------------------------
    @staticmethod
    def resolver_nombre_dinamico(patron: str, archivo_path, secuencial: int,
                                  regex_busca: str = "", regex_reemplaza: str = "",
                                  origen_base: Path | None = None) -> str:
        archivo = Path(archivo_path)
        try:
            stat = archivo.stat() if archivo.exists() else None
        except OSError:
            stat = None
        f_mtime = datetime.fromtimestamp(stat.st_mtime) if stat else datetime.now()
        f_actual = datetime.now()

        nombre_base = archivo.stem
        if regex_busca:
            try:
                nombre_base = re.sub(regex_busca, regex_reemplaza, nombre_base)
            except re.error as exc:
                logger.debug("RegEx de renombrado inválida: %s", exc)

        # {ruta_relativa}: sub-ruta desde el origen hasta la carpeta del archivo
        # Ej: origen=C:\temp\folder1, archivo=C:\temp\folder1\folder2\img.jpg → "folder1/folder2"
        ruta_relativa = ""
        if origen_base is not None:
            try:
                rel = archivo.parent.relative_to(origen_base.parent)
                ruta_relativa = str(rel).replace("\\", "/")
            except ValueError:
                ruta_relativa = archivo.parent.name

        tokens = {
            "{nombre_origen}": nombre_base, "{secuencial}": f"{secuencial:04d}",
            "{fecha_fichero}": f_mtime.strftime("%Y_%m_%d"), "{año}": f_mtime.strftime("%Y"),
            "{mes}": f_mtime.strftime("%m"), "{dia}": f_mtime.strftime("%d"),
            "{fecha_actual}": f_actual.strftime("%Y_%m_%d"), "{directorio}": archivo.parent.name,
            "{usuario}": getpass.getuser(), "{equipo}": socket.gethostname(),
            "{source_name}": nombre_base, "{sequential}": f"{secuencial:04d}",
            "{file_date}": f_mtime.strftime("%Y_%m_%d"), "{year}": f_mtime.strftime("%Y"),
            "{month}": f_mtime.strftime("%m"), "{day}": f_mtime.strftime("%d"),
            "{current_date}": f_actual.strftime("%Y_%m_%d"), "{root_folder}": archivo.parent.name,
            "{ruta_relativa}": ruta_relativa, "{relative_path}": ruta_relativa,
        }

        resultado = patron
        for token, valor in tokens.items():
            resultado = resultado.replace(token, str(valor))
        return _INVALID_NAME_CHARS_RE.sub("", resultado).strip()

    def _siguiente_secuencial(self, carpeta_destino: Path, patron_base: str,
                               ext: str, archivo_origen: Path) -> int:
        """Calcula el siguiente número de secuencia disponible en la
        carpeta destino. Protegido con lock porque varias copias
        concurrentes podrían pisarse el contador (defensivo; el motor
        actual escanea en un único hilo, pero el lock deja la API
        lista para paralelizar sin introducir una regresión sutil)."""
        with self._seq_lock:
            if not carpeta_destino.exists():
                return 1
            patron_fichero = patron_base.split("/")[-1] if "/" in patron_base else patron_base
            test_nombre = self.resolver_nombre_dinamico(
                patron_fichero, archivo_origen, 0,
                self.cfg.ren_regex_busca, self.cfg.ren_regex_reemplaza,
                origen_base=self.origen,
            )
            token_sec = "{sequential}" if "{sequential}" in patron_fichero else "{secuencial}"
            prefix_test = test_nombre.split(token_sec)[0] if token_sec in patron_fichero else test_nombre

            max_secuencial = 0
            try:
                for archivo in carpeta_destino.iterdir():
                    if archivo.is_file() and archivo.suffix.lower() == ext:
                        if not prefix_test or archivo.name.startswith(prefix_test):
                            max_secuencial += 1
            except OSError as exc:
                logger.debug("No se pudo listar %s: %s", carpeta_destino, exc)
            return max_secuencial + 1

    def _copiar_con_seguridad(self, origen_path: Path, destino_path: Path) -> int:
        """
        Copia origen → destino de forma atómica y verifica el tamaño post-copia.
        Retorna el tamaño en bytes del archivo copiado.
        Lanza OSError si la verificación falla o si la copia está truncada.
        """
        if self.cfg.dry_run:
            try:
                return origen_path.stat().st_size
            except OSError:
                return 0

        src_size = origen_path.stat().st_size   # leer ANTES de copiar

        if self.cfg.copia_atomica:
            destino_tmp = destino_path.parent / (destino_path.name + ".tmp")
            try:
                shutil.copy2(origen_path, destino_tmp)
                # Verificación: el .tmp debe pesar exactamente lo mismo que el origen
                dst_size = destino_tmp.stat().st_size
                if dst_size != src_size:
                    destino_tmp.unlink(missing_ok=True)
                    raise OSError(
                        f"Verificación fallida: origen {src_size} B, "
                        f"destino {dst_size} B — posible disco lleno o error de I/O"
                    )
                os.replace(destino_tmp, destino_path)
            except Exception:
                try:
                    destino_tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        else:
            shutil.copy2(origen_path, destino_path)
            dst_size = destino_path.stat().st_size
            if dst_size != src_size:
                raise OSError(
                    f"Verificación fallida: origen {src_size} B, destino {dst_size} B"
                )

        return src_size

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    def iniciar(self) -> None:
        try:
            if self._completados:
                self.on_log(
                    f"🔄 Reanudando sesión activa precargada "
                    f"({len(self._completados)} procesados históricos)",
                    "info",
                )
            self._escanear_origen()
            if self.activo:
                self.limpiar_checkpoint()
        except Exception as exc:
            logger.exception("Error crítico en el motor de sincronización")
            self.on_log(f"Critical error: {exc}", "error")
        finally:
            self.activo = False
            self._flush_checkpoint()   # volcar lo que quede en el buffer
            self.on_log("⏹️ Motor de sincronización finalizado.", "info")
            if self.on_finished:
                self.on_finished()

    def detener(self) -> None:
        self.activo = False

    def _escanear_origen(self) -> None:
        if not self.origen.exists():
            self.on_log(f"❌ La carpeta origen no existe: {self.origen}", "error")
            return
        for raiz, _, archivos in os.walk(self.origen):
            if not self.activo:
                return
            for nombre_archivo in archivos:
                if not self.activo:
                    return
                self._procesar_archivo(Path(raiz) / nombre_archivo)

    def _procesar_archivo(self, ruta_archivo: Path) -> None:
        try:
            archivo = ruta_archivo.resolve()
        except OSError as exc:
            self.on_log(f"❌ ERROR resolviendo ruta {ruta_archivo}: {exc}", "error")
            return

        abs_path_str = str(archivo)
        if abs_path_str in self._completados:
            return
        if not archivo.is_file() or self.destino in archivo.parents or archivo == self.destino:
            return
        if not self._pasa_filtros(archivo):
            return

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            ext = archivo.suffix.lower()
            patron_global = self.cfg.patron_renombrado
            nombre_evaluado = self.resolver_nombre_dinamico(
                patron_global, archivo, 1,
                self.cfg.ren_regex_busca, self.cfg.ren_regex_reemplaza,
                origen_base=self.origen,
            )

            if "/" in nombre_evaluado:
                partes = nombre_evaluado.split("/")
                carpeta_destino = self.destino / "/".join(partes[:-1])
                patron_solo_archivo = partes[-1]
            else:
                carpeta_destino = self.destino
                patron_solo_archivo = nombre_evaluado

            if not self.cfg.dry_run:
                carpeta_destino.mkdir(parents=True, exist_ok=True)

            num_secuencial = self._siguiente_secuencial(
                carpeta_destino, patron_global, ext, archivo)
            nuevo_nombre_stem = self.resolver_nombre_dinamico(
                patron_solo_archivo, archivo, num_secuencial,
                self.cfg.ren_regex_busca, self.cfg.ren_regex_reemplaza,
                origen_base=self.origen,
            )

            ruta_destino = carpeta_destino / f"{nuevo_nombre_stem}{ext}"
            if ruta_destino.exists() and not self.cfg.dry_run:
                if self.cfg.colision == "omitir":
                    self.on_log(f"⏭️ OMITIDO: {archivo.name}", "info")
                    self._registrar_checkpoint(abs_path_str)
                    self._report.append(ReportEntry(
                        ts, "OMITIDO", str(archivo), str(ruta_destino), 0, True))
                    return
                ruta_destino = carpeta_destino / f"{nuevo_nombre_stem}_{int(time.time())}{ext}"

            # _copiar_con_seguridad retorna el tamaño verificado o lanza OSError
            tamano = self._copiar_con_seguridad(archivo, ruta_destino)

            # Modo mover: solo eliminar el origen DESPUÉS de verificar el destino
            if self.cfg.modo_mover and not self.cfg.dry_run:
                os.unlink(archivo)

            self._registrar_checkpoint(abs_path_str)
            accion_str = ("MOVED" if self.cfg.idioma == "en"
                          else ("MOVIDO" if self.cfg.modo_mover else "COPIADO"))

            if not self.cfg.dry_run:
                self.history.append(
                    "Sincronización", accion_str, archivo, ruta_destino, tamano)

            self._report.append(ReportEntry(
                ts, accion_str, str(archivo), str(ruta_destino), tamano, True))
            self.on_log(f"✅ {accion_str}: {archivo.name} -> {ruta_destino.name}", "ok")
            self.on_progress(tamano)

        except OSError as exc:
            self.on_log(f"❌ ERROR: {archivo.name} -> {exc}", "error")
            self._report.append(ReportEntry(
                ts, "ERROR", str(archivo), "", 0, False))
        except Exception:
            logger.exception("Error inesperado procesando %s", archivo)
            self.on_log(f"❌ ERROR inesperado procesando {archivo.name}", "error")
            self._report.append(ReportEntry(
                ts, "ERROR", str(archivo), "", 0, False))
