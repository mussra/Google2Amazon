"""
Motor de sincronización bidireccional (espejo bajo demanda).

Compara N directorios entre sí y copia los archivos que faltan
en alguno de ellos al resto. NUNCA elimina archivos.

Estrategia de comparación:
  - Agrupa archivos por ruta relativa respecto a su directorio raíz.
  - Un archivo se considera «presente» en un directorio si existe un
    archivo con la misma ruta relativa (nombre + subdirectorios).
  - Opcionalmente se puede activar comparación por hash MD5 para
    detectar archivos con la misma ruta pero contenido distinto.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .constants import DICCIONARIO_EXTENSIONES
from .persistence import HashCache

logger = logging.getLogger(__name__)

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int], None]   # (copiados, total_pendientes)


@dataclass
class MirrorOptions:
    incluir_fotos: bool = True
    incluir_videos: bool = True
    incluir_docs: bool = True
    incluir_otros: bool = True
    # Verifica que archivos con misma ruta tengan el mismo contenido
    comparar_por_hash: bool = False
    # Detecta archivos con MISMO CONTENIDO aunque tengan distinto nombre/ruta
    # (útil cuando las fotos están renombradas entre directorios)
    comparar_por_contenido: bool = False
    copia_atomica: bool = True
    exclusiones: list[str] = field(default_factory=list)


@dataclass
class MirrorDiff:
    """Un archivo que falta en ``destino`` y debe copiarse desde ``origen``."""
    origen: Path
    destino: Path
    ruta_relativa: str
    size_bytes: int


class MirrorEngine:
    def __init__(self, hash_cache: HashCache | None = None):
        self.hash_cache = hash_cache or HashCache()
        self._cancelado = threading.Event()

    def cancelar(self) -> None:
        self._cancelado.set()

    def _cancelado_check(self) -> bool:
        return self._cancelado.is_set()

    def _acepta_extension(self, ext: str, opts: MirrorOptions) -> bool:
        if ext in DICCIONARIO_EXTENSIONES["fotos"]:
            return opts.incluir_fotos
        if ext in DICCIONARIO_EXTENSIONES["videos"]:
            return opts.incluir_videos
        if ext in DICCIONARIO_EXTENSIONES["documentos"]:
            return opts.incluir_docs
        return opts.incluir_otros

    # ------------------------------------------------------------------
    # Indexación de un directorio → {ruta_relativa: (Path, hash|None)}
    # ------------------------------------------------------------------
    def _indexar(self, raiz: Path, opts: MirrorOptions,
                 on_log: LogCallback) -> dict[str, tuple[Path, str | None]]:
        exclusiones = [e.lower() for e in opts.exclusiones if e]
        # indice por ruta relativa → (Path, hash|None)
        indice: dict[str, tuple[Path, str | None]] = {}
        # indice por hash → Path  (solo cuando comparar_por_contenido)
        self._hash_to_path: dict[str, Path] = getattr(self, "_hash_to_path", {})

        for dirpath, _, ficheros in os.walk(raiz):
            if self._cancelado_check():
                return {}
            if any(exc in dirpath.lower() for exc in exclusiones):
                continue
            for nombre in ficheros:
                abs_path = Path(dirpath) / nombre
                ext = abs_path.suffix.lower()
                if not self._acepta_extension(ext, opts):
                    continue
                try:
                    rel = str(abs_path.relative_to(raiz)).replace("\\", "/")
                    digest = None
                    if opts.comparar_por_hash or opts.comparar_por_contenido:
                        digest = self.hash_cache.get_or_compute(
                            abs_path, cancel_check=self._cancelado_check)
                    indice[rel] = (abs_path, digest)
                except (OSError, ValueError) as exc:
                    logger.debug("Mirror index skip %s: %s", abs_path, exc)
        return indice

    def _build_content_index(
        self, indice: dict[str, tuple[Path, str | None]]
    ) -> dict[str, str]:
        """Retorna {hash: ruta_relativa} para comparación por contenido."""
        return {digest: rel for rel, (_, digest) in indice.items() if digest}
        return indice

    # ------------------------------------------------------------------
    # Comparación entre dos directorios
    # ------------------------------------------------------------------
    def _calcular_diff(
        self,
        raiz_origen: Path,
        indice_origen: dict[str, tuple[Path, str | None]],
        raiz_destino: Path,
        indice_destino: dict[str, tuple[Path, str | None]],
        opts: MirrorOptions,
    ) -> list[MirrorDiff]:
        diffs: list[MirrorDiff] = []

        # En modo contenido construimos un índice hash→rel del destino
        # para detectar si el archivo ya existe con otro nombre
        contenido_destino: dict[str, str] = {}
        if opts.comparar_por_contenido:
            contenido_destino = self._build_content_index(indice_destino)

        for rel, (src_path, src_hash) in indice_origen.items():
            if self._cancelado_check():
                return []

            # ── Modo contenido: ignorar nombre, buscar por hash ──────
            if opts.comparar_por_contenido and src_hash:
                if src_hash in contenido_destino:
                    # Mismo contenido ya existe en destino (aunque renombrado)
                    continue
                # No existe por contenido → copiar manteniendo ruta relativa origen
                try:
                    size = src_path.stat().st_size
                except OSError:
                    size = 0
                diffs.append(MirrorDiff(
                    origen=src_path,
                    destino=raiz_destino / rel,
                    ruta_relativa=rel,
                    size_bytes=size,
                ))
                continue

            # ── Modo ruta: comparación por ruta relativa (defecto) ───
            if rel not in indice_destino:
                try:
                    size = src_path.stat().st_size
                except OSError:
                    size = 0
                diffs.append(MirrorDiff(
                    origen=src_path,
                    destino=raiz_destino / rel,
                    ruta_relativa=rel,
                    size_bytes=size,
                ))
            elif opts.comparar_por_hash and src_hash:
                _, dst_hash = indice_destino[rel]
                if dst_hash and dst_hash != src_hash:
                    dst_path = indice_destino[rel][0]
                    conflict_path = dst_path.parent / (
                        f"{dst_path.stem}_CONFLICT_{int(time.time())}{dst_path.suffix}"
                    )
                    diffs.append(MirrorDiff(
                        origen=src_path,
                        destino=conflict_path,
                        ruta_relativa=rel + "_CONFLICT",
                        size_bytes=src_path.stat().st_size if src_path.exists() else 0,
                    ))
        return diffs

    # ------------------------------------------------------------------
    # Copia atómica
    # ------------------------------------------------------------------
    def _copiar(self, diff: MirrorDiff) -> bool:
        try:
            diff.destino.parent.mkdir(parents=True, exist_ok=True)
            tmp = diff.destino.parent / (diff.destino.name + ".tmp")
            shutil.copy2(diff.origen, tmp)
            os.replace(tmp, diff.destino)
            return True
        except Exception as exc:
            try:
                tmp = diff.destino.parent / (diff.destino.name + ".tmp")
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            logger.warning("Mirror copy failed %s → %s: %s", diff.origen, diff.destino, exc)
            return False

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def analizar(
        self,
        directorios: list[str],
        opts: MirrorOptions,
        on_log: LogCallback,
    ) -> list[MirrorDiff]:
        """
        Calcula todos los archivos faltantes entre los directorios.
        No copia nada. Retorna lista de diffs.
        """
        self._cancelado.clear()
        raices = [Path(d).resolve() for d in directorios if Path(d).exists()]
        if len(raices) < 2:
            on_log("❌ Se necesitan al menos 2 directorios existentes para el espejo.")
            return []

        on_log(f"🔍 Indexando {len(raices)} directorios...")
        indices: list[dict[str, tuple[Path, str | None]]] = []
        for raiz in raices:
            on_log(f"  📂 {raiz} ...")
            idx = self._indexar(raiz, opts, on_log)
            indices.append(idx)
            on_log(f"     → {len(idx)} archivos indexados")
            if self._cancelado_check():
                return []

        todos_los_diffs: list[MirrorDiff] = []
        for i, (raiz_i, idx_i) in enumerate(zip(raices, indices)):
            for j, (raiz_j, idx_j) in enumerate(zip(raices, indices)):
                if i == j:
                    continue
                if self._cancelado_check():
                    return []
                diffs = self._calcular_diff(raiz_i, idx_i, raiz_j, idx_j, opts)
                if diffs:
                    on_log(f"  ⚠️  {len(diffs)} archivos de «{raiz_i.name}» faltan en «{raiz_j.name}»")
                todos_los_diffs.extend(diffs)

        on_log(f"✅ Análisis completo: {len(todos_los_diffs)} copias necesarias.")
        return todos_los_diffs

    def sincronizar(
        self,
        diffs: list[MirrorDiff],
        on_log: LogCallback,
        on_progress: ProgressCallback,
    ) -> tuple[int, int]:
        """Ejecuta las copias calculadas por analizar(). Retorna (ok, errores)."""
        ok = errores = 0
        total = len(diffs)
        for diff in diffs:
            if self._cancelado_check():
                on_log("🛑 Espejo cancelado por el usuario.")
                break
            if self._copiar(diff):
                ok += 1
                on_log(f"✅ {diff.ruta_relativa} → {diff.destino.parent.name}")
            else:
                errores += 1
                on_log(f"❌ Error copiando {diff.ruta_relativa}")
            on_progress(ok + errores, total)
        on_log(f"🏁 Espejo finalizado: {ok} copiados, {errores} errores.")
        return ok, errores
