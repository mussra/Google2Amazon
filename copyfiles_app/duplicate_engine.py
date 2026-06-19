"""
Motor de búsqueda de duplicados: indexa archivos por unidad física,
calcula hashes en paralelo (un hilo por unidad de disco) y agrupa los
resultados por hash (+ opcionalmente nombre/tamaño).

Desacoplado de la interfaz gráfica: se comunica exclusivamente a
través de callbacks, lo que permite probarlo o reutilizarlo sin Tk.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .constants import CHK_DUP, DICCIONARIO_EXTENSIONES
from .persistence import HashCache

logger = logging.getLogger(__name__)


@dataclass
class DuplicateOptions:
    incluir_fotos: bool = True
    incluir_videos: bool = False
    incluir_docs: bool = False
    incluir_otros: bool = False
    filtrar_por_nombre: bool = False
    filtrar_por_tamano: bool = True
    max_hilos: int = 4
    exclusiones: list[str] = field(default_factory=list)


@dataclass
class FileNode:
    path: str
    size: int


ThreadStatusCallback = Callable[[str, str], None]
ConsoleCallback = Callable[[str], None]


class DuplicateEngine:
    """Encapsula el escaneo concurrente y la detección de duplicados."""

    def __init__(self, hash_cache: HashCache | None = None,
                 checkpoint_path: Path = CHK_DUP):
        self.hash_cache = hash_cache or HashCache()
        self.checkpoint_path = checkpoint_path

        self._cancelado = threading.Event()
        self._lock = threading.Lock()
        self._mapa_resultados: dict[str, list[FileNode]] = {}
        self._checkpoint_data: dict[str, dict] = {}

    def cancelar(self) -> None:
        self._cancelado.set()

    def _esta_cancelado(self) -> bool:
        return self._cancelado.is_set()

    # ------------------------------------------------------------------
    # Indexación
    # ------------------------------------------------------------------
    def _clasificar_extension(self, ext: str, opts: DuplicateOptions) -> bool:
        if ext in DICCIONARIO_EXTENSIONES["fotos"]:
            return opts.incluir_fotos
        if ext in DICCIONARIO_EXTENSIONES["videos"]:
            return opts.incluir_videos
        if ext in DICCIONARIO_EXTENSIONES["documentos"]:
            return opts.incluir_docs
        return opts.incluir_otros

    def agrupar_archivos_por_unidad(self, rutas_origen: list[str],
                                     opts: DuplicateOptions) -> dict[str, list[str]]:
        archivos_por_unidad: dict[str, list[str]] = {}
        exclusiones = [e.lower() for e in opts.exclusiones if e]

        for ruta_base in rutas_origen:
            if not os.path.exists(ruta_base):
                continue
            for raiz, _, ficheros in os.walk(ruta_base):
                if self._esta_cancelado():
                    return {}
                if any(exc in raiz.lower() for exc in exclusiones):
                    continue
                for nombre in ficheros:
                    ruta_completa = os.path.join(raiz, nombre)
                    try:
                        ext = Path(ruta_completa).suffix.lower()
                        if not self._clasificar_extension(ext, opts):
                            continue
                        stat = os.stat(ruta_completa)
                        if stat.st_size <= 0:
                            continue
                        unidad = os.path.splitdrive(ruta_completa)[0] or "/"
                        archivos_por_unidad.setdefault(unidad, []).append(ruta_completa)
                    except OSError as exc:
                        logger.debug("No se pudo procesar %s: %s", ruta_completa, exc)
        return archivos_por_unidad

    # ------------------------------------------------------------------
    # Cálculo de hashes
    # ------------------------------------------------------------------
    def _registrar_en_resultados(self, ruta: str, tamano: int, digest: str,
                                  opts: DuplicateOptions) -> None:
        llave = digest
        if opts.filtrar_por_nombre:
            llave += f"_N_{os.path.basename(ruta)}"
        if opts.filtrar_por_tamano:
            llave += f"_T_{tamano}"
        self._mapa_resultados.setdefault(llave, []).append(FileNode(path=ruta, size=tamano))

    def _trabajador_de_unidad(self, nombre_hilo: str, lista_archivos: list[str],
                               opts: DuplicateOptions, on_thread_status: ThreadStatusCallback) -> None:
        total = len(lista_archivos)
        for idx, ruta in enumerate(lista_archivos):
            if self._esta_cancelado():
                on_thread_status(nombre_hilo, "🛑 Operación detenida por usuario.")
                return

            porcentaje = int((idx / total) * 100) if total else 100
            on_thread_status(nombre_hilo, f"[{porcentaje}%] Analizando: {os.path.basename(ruta)}")

            try:
                path_obj = Path(ruta)
                stat = path_obj.stat()
                tamano, mtime = stat.st_size, stat.st_mtime

                with self._lock:
                    nodo_cache = self._checkpoint_data.get(ruta)
                    hash_existente = None
                    if nodo_cache and nodo_cache.get("size") == tamano and nodo_cache.get("mtime") == mtime:
                        hash_existente = nodo_cache.get("hash")

                if hash_existente:
                    with self._lock:
                        self._registrar_en_resultados(ruta, tamano, hash_existente, opts)
                    continue

                digest = self.hash_cache._compute_hash(path_obj, 65536, self._esta_cancelado)
                if digest:
                    with self._lock:
                        self._checkpoint_data[ruta] = {"size": tamano, "mtime": mtime, "hash": digest}
                        self._registrar_en_resultados(ruta, tamano, digest, opts)
            except OSError as exc:
                logger.debug("Error analizando %s: %s", ruta, exc)
                continue

        on_thread_status(nombre_hilo, "✅ Finalizado.")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def buscar_duplicados(self, rutas_origen: list[str], opts: DuplicateOptions,
                           on_console: ConsoleCallback, on_thread_status: ThreadStatusCallback
                           ) -> tuple[dict[str, list[FileNode]], dict]:
        """Ejecuta el escaneo de forma síncrona (pensado para llamarse
        desde un hilo de fondo) y devuelve (duplicados, métricas)."""
        self._cancelado.clear()
        self._mapa_resultados = {}
        self._checkpoint_data = self._cargar_checkpoint()

        t_inicio = time.time()
        on_console("🔍 Analizando estructura de directorios y mapeando topología de discos...\n")

        archivos_por_unidad = self.agrupar_archivos_por_unidad(rutas_origen, opts)
        unidades = list(archivos_por_unidad.keys())
        hilos_reales = min(len(unidades), max(1, opts.max_hilos))

        if hilos_reales == 0 or not archivos_por_unidad:
            on_console("❌ No se detectaron archivos legibles en los directorios indicados respetando los filtros activos.\n")
            return {}, {"ficheros": 0, "directorios": 0, "tiempo_ms": 0.0}

        with concurrent.futures.ThreadPoolExecutor(max_workers=hilos_reales) as executor:
            futuros = [
                executor.submit(
                    self._trabajador_de_unidad, f"Hilo {i + 1} ({unidad})",
                    archivos_por_unidad[unidad], opts, on_thread_status,
                )
                for i, unidad in enumerate(unidades)
            ]
            concurrent.futures.wait(futuros)

        if not self._esta_cancelado():
            self._guardar_checkpoint()

        duplicados = {k: v for k, v in self._mapa_resultados.items() if len(v) > 1}
        metricas = {
            "ficheros": sum(len(x) for x in archivos_por_unidad.values()),
            "directorios": len(unidades),
            "tiempo_ms": (time.time() - t_inicio) * 1000,
        }
        return duplicados, metricas

    def eliminar(self, ruta_abs: str, metodo: str) -> int:
        """Elimina (o envía a la papelera) un archivo. Devuelve el
        tamaño en bytes liberado, o 0 si falló."""
        try:
            tamano = os.path.getsize(ruta_abs) if os.path.exists(ruta_abs) else 0
            if "Papelera" in metodo or "Trash" in metodo:
                from send2trash import send2trash
                send2trash(str(Path(ruta_abs).resolve()))
            else:
                os.unlink(ruta_abs)
            return tamano
        except OSError as exc:
            logger.warning("No se pudo eliminar %s: %s", ruta_abs, exc)
            return 0
        except ImportError:
            logger.error("La librería send2trash no está instalada; no se pudo enviar a la papelera %s", ruta_abs)
            return 0

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------
    def _cargar_checkpoint(self) -> dict[str, dict]:
        if not self.checkpoint_path.exists():
            return {}
        try:
            with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Checkpoint de duplicados ilegible, se ignora: %s", exc)
            return {}

    def _guardar_checkpoint(self) -> None:
        try:
            with open(self.checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(self._checkpoint_data, f, indent=4)
        except OSError as exc:
            logger.warning("No se pudo guardar el checkpoint de duplicados: %s", exc)
