"""
Motor de sincronización bidireccional (espejo bajo demanda). v2

Mejoras respecto a v1:
  - Pre-filtro por tamaño cross-directorio: solo se hashea un archivo
    si existe al menos otro archivo de exactamente el mismo tamaño en
    algún otro directorio (modo comparar_por_contenido). Esto evita
    calcular hashes de archivos únicos por tamaño, igual que en
    duplicate_engine.
  - Eliminado dead-code (return inalcanzable en _build_content_index).
  - Verificación post-copia: compara st_size origen vs destino tras
    cada copy2; si no coinciden, borra el parcial y reporta error.
  - Reintentos con backoff exponencial (3 intentos) ante errores
    transitorios de I/O (disco lento, USB, red).
  - Log de resumen por par de directorios al finalizar analizar().
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .constants import DICCIONARIO_EXTENSIONES
from .persistence import HashCache

logger = logging.getLogger(__name__)

LogCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int], None]   # (procesados, total)

_MAX_REINTENTOS = 3
_BACKOFF_BASE = 2.0   # segundos


@dataclass
class MirrorOptions:
    incluir_fotos: bool = True
    incluir_videos: bool = True
    incluir_docs: bool = True
    incluir_otros: bool = True
    # Verifica integridad de archivos con misma ruta pero posible contenido distinto
    comparar_por_hash: bool = False
    # Detecta archivos con MISMO CONTENIDO aunque tengan distinto nombre/ruta.
    # Solo calcula hash de archivos cuyo tamaño existe en ≥1 archivo de otro dir.
    comparar_por_contenido: bool = False
    copia_atomica: bool = True
    verificar_post_copia: bool = True    # compara tamaño origen vs destino tras copiar
    exclusiones: list[str] = field(default_factory=list)


@dataclass
class MirrorDiff:
    """Un archivo que falta en ``destino`` y debe copiarse desde ``origen``."""
    origen: Path
    destino: Path
    ruta_relativa: str
    size_bytes: int
    es_conflicto: bool = False


# Tipo del índice interno
_Indice = dict[str, tuple[Path, int, str | None]]
# ruta_relativa → (Path_abs, size_bytes, hash_md5|None)


class MirrorEngine:
    def __init__(self, hash_cache: HashCache | None = None):
        self.hash_cache = hash_cache or HashCache()
        self._cancelado = threading.Event()

    def cancelar(self) -> None:
        self._cancelado.set()

    def _cancelado_check(self) -> bool:
        return self._cancelado.is_set()

    def _acepta_extension(self, ext: str, opts: MirrorOptions) -> bool:
        if ext in DICCIONARIO_EXTENSIONES["fotos"]:   return opts.incluir_fotos
        if ext in DICCIONARIO_EXTENSIONES["videos"]:  return opts.incluir_videos
        if ext in DICCIONARIO_EXTENSIONES["documentos"]: return opts.incluir_docs
        return opts.incluir_otros

    # ------------------------------------------------------------------
    # Fase 1: indexación ligera (solo stat, sin hash)
    # ------------------------------------------------------------------
    def _indexar_stat(self, raiz: Path, opts: MirrorOptions) -> _Indice:
        """Primera pasada: recorre el árbol y registra solo ruta y tamaño."""
        exclusiones = [e.lower() for e in opts.exclusiones if e]
        indice: _Indice = {}
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
                    size = abs_path.stat().st_size
                    rel = str(abs_path.relative_to(raiz)).replace("\\", "/")
                    indice[rel] = (abs_path, size, None)
                except (OSError, ValueError) as exc:
                    logger.debug("stat skip %s: %s", abs_path, exc)
        return indice

    # ------------------------------------------------------------------
    # Fase 2: calcular hashes solo donde es necesario
    # ------------------------------------------------------------------
    def _enriquecer_con_hashes(
        self,
        indices: list[_Indice],
        opts: MirrorOptions,
        on_log: LogCallback,
    ) -> None:
        """
        Calcula hashes MD5 solo para los archivos que los necesitan:

        - comparar_por_hash=True: archivos que ya existen en todos los
          índices por ruta relativa (para verificar integridad).
        - comparar_por_contenido=True: archivos cuyo tamaño en bytes
          aparece en al menos un archivo de OTRO directorio (pre-filtro).
          Los archivos con tamaño único entre todos los directorios
          no se hashean — nunca podrán ser el mismo contenido.
        """
        if not opts.comparar_por_hash and not opts.comparar_por_contenido:
            return

        necesitan_hash: set[tuple[int, str]] = set()  # (idx_indice, rel)

        if opts.comparar_por_contenido:
            # Construir mapa size → {idx_dir: [rel, ...]}
            size_map: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
            for idx, ind in enumerate(indices):
                for rel, (_, sz, _) in ind.items():
                    size_map[sz][idx].append(rel)

            # Solo los tamaños que aparecen en ≥2 directorios distintos
            for sz, dirs in size_map.items():
                if len(dirs) >= 2:
                    for idx, rels in dirs.items():
                        for rel in rels:
                            necesitan_hash.add((idx, rel))

            descartados = sum(
                1 for ind in indices for rel, (_, sz, _) in ind.items()
                if len(size_map[sz]) < 2
            )
            on_log(f"   Pre-filtro tamaño: {len(necesitan_hash)} a hashear, "
                   f"{descartados} descartados (tamaño único)")

        if opts.comparar_por_hash:
            # Archivos con misma ruta en múltiples directorios
            todas_las_rutas: set[str] = set()
            for ind in indices:
                todas_las_rutas.update(ind.keys())
            for rel in todas_las_rutas:
                presentes = [idx for idx, ind in enumerate(indices) if rel in ind]
                if len(presentes) >= 2:
                    for idx in presentes:
                        necesitan_hash.add((idx, rel))

        # Calcular hashes en paralelo (un hilo por índice para no serializar I/O)
        def _hashear_indice(idx: int, ind: _Indice) -> None:
            rels = [rel for (i, rel) in necesitan_hash if i == idx]
            for rel in rels:
                if self._cancelado_check():
                    return
                abs_path, sz, _ = ind[rel]
                digest = self.hash_cache.get_or_compute(
                    abs_path, cancel_check=self._cancelado_check)
                ind[rel] = (abs_path, sz, digest)

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(indices), 4)
        ) as ex:
            futs = [ex.submit(_hashear_indice, i, ind) for i, ind in enumerate(indices)]
            concurrent.futures.wait(futs)

    # ------------------------------------------------------------------
    # Comparación entre dos directorios
    # ------------------------------------------------------------------
    def _calcular_diff(
        self,
        raiz_origen: Path,
        indice_origen: _Indice,
        raiz_destino: Path,
        indice_destino: _Indice,
        opts: MirrorOptions,
    ) -> list[MirrorDiff]:
        diffs: list[MirrorDiff] = []

        # Índice de contenido del destino: hash → rel (para modo contenido)
        contenido_destino: dict[str, str] = {}
        if opts.comparar_por_contenido:
            for rel, (_, _, digest) in indice_destino.items():
                if digest:
                    contenido_destino[digest] = rel

        for rel, (src_path, src_size, src_hash) in indice_origen.items():
            if self._cancelado_check():
                return []

            # ── Modo contenido: buscar por hash ignorando nombre ─────
            if opts.comparar_por_contenido:
                if src_hash is None:
                    # Sin hash (tamaño único entre dirs) → no puede tener gemelo
                    # Solo copiar si tampoco existe por ruta
                    if rel not in indice_destino:
                        diffs.append(MirrorDiff(src_path, raiz_destino / rel, rel, src_size))
                    continue
                if src_hash in contenido_destino:
                    continue   # mismo contenido ya existe, aunque renombrado
                # Contenido no encontrado en destino → copiar
                diffs.append(MirrorDiff(src_path, raiz_destino / rel, rel, src_size))
                continue

            # ── Modo ruta (defecto): comparar por ruta relativa ──────
            if rel not in indice_destino:
                diffs.append(MirrorDiff(src_path, raiz_destino / rel, rel, src_size))
            elif opts.comparar_por_hash and src_hash:
                _, _, dst_hash = indice_destino[rel]
                if dst_hash and dst_hash != src_hash:
                    dst_path = indice_destino[rel][0]
                    conflict = dst_path.parent / (
                        f"{dst_path.stem}_CONFLICT_{int(time.time())}{dst_path.suffix}"
                    )
                    diffs.append(MirrorDiff(
                        src_path, conflict,
                        rel + "_CONFLICT", src_size,
                        es_conflicto=True,
                    ))
        return diffs

    # ------------------------------------------------------------------
    # Copia robusta con reintentos y verificación post-copia
    # ------------------------------------------------------------------
    def _copiar(self, diff: MirrorDiff, opts: MirrorOptions) -> bool:
        for intento in range(1, _MAX_REINTENTOS + 1):
            if self._cancelado_check():
                return False
            try:
                diff.destino.parent.mkdir(parents=True, exist_ok=True)
                tmp = diff.destino.parent / (diff.destino.name + ".tmp")
                shutil.copy2(diff.origen, tmp)

                # Verificación post-copia: tamaño del .tmp debe coincidir
                if opts.verificar_post_copia:
                    src_size = diff.origen.stat().st_size
                    dst_size = tmp.stat().st_size
                    if src_size != dst_size:
                        tmp.unlink(missing_ok=True)
                        raise OSError(
                            f"Verificación fallida: origen {src_size}B ≠ destino {dst_size}B"
                        )

                os.replace(tmp, diff.destino)
                return True

            except OSError as exc:
                logger.warning(
                    "Copia fallida (intento %d/%d) %s → %s: %s",
                    intento, _MAX_REINTENTOS, diff.origen, diff.destino, exc,
                )
                try:
                    tmp_path = diff.destino.parent / (diff.destino.name + ".tmp")
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                if intento < _MAX_REINTENTOS and not self._cancelado_check():
                    espera = _BACKOFF_BASE ** (intento - 1)
                    logger.info("Reintentando en %.1fs...", espera)
                    time.sleep(espera)

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
        """Calcula los archivos faltantes. No copia nada."""
        self._cancelado.clear()
        raices = [Path(d).resolve() for d in directorios if Path(d).exists()]
        if len(raices) < 2:
            on_log("❌ Se necesitan al menos 2 directorios existentes.")
            return []

        # Fase 1: indexación ligera (solo stat)
        on_log(f"🔍 Indexando {len(raices)} directorios (stat)...")
        indices: list[_Indice] = []
        for raiz in raices:
            on_log(f"  📂 {raiz} ...")
            idx = self._indexar_stat(raiz, opts)
            indices.append(idx)
            on_log(f"     → {len(idx)} archivos")
            if self._cancelado_check():
                return []

        # Fase 2: hashes donde son necesarios
        if opts.comparar_por_hash or opts.comparar_por_contenido:
            on_log("🔑 Calculando hashes (pre-filtro por tamaño activo)...")
            self._enriquecer_con_hashes(indices, opts, on_log)
            if self._cancelado_check():
                return []

        # Fase 3: cruce N×N
        todos_los_diffs: list[MirrorDiff] = []
        resumen: dict[str, int] = {}
        for i, (raiz_i, idx_i) in enumerate(zip(raices, indices)):
            for j, (raiz_j, idx_j) in enumerate(zip(raices, indices)):
                if i == j:
                    continue
                if self._cancelado_check():
                    return []
                diffs = self._calcular_diff(raiz_i, idx_i, raiz_j, idx_j, opts)
                clave = f"«{raiz_i.name}» → «{raiz_j.name}»"
                resumen[clave] = len(diffs)
                if diffs:
                    on_log(f"  ⚠️  {len(diffs)} archivos de {clave}")
                todos_los_diffs.extend(diffs)

        conflictos = sum(1 for d in todos_los_diffs if d.es_conflicto)
        on_log(
            f"✅ Análisis completo: {len(todos_los_diffs)} copias necesarias"
            + (f" ({conflictos} conflictos de contenido)" if conflictos else "")
        )
        return todos_los_diffs

    def sincronizar(
        self,
        diffs: list[MirrorDiff],
        opts: MirrorOptions,
        on_log: LogCallback,
        on_progress: ProgressCallback,
    ) -> tuple[int, int]:
        """Ejecuta las copias. Retorna (ok, errores)."""
        ok = errores = 0
        total = len(diffs)
        for diff in diffs:
            if self._cancelado_check():
                on_log("🛑 Espejo cancelado.")
                break
            if self._copiar(diff, opts):
                ok += 1
                etiqueta = "⚠️ CONFLICTO" if diff.es_conflicto else "✅"
                on_log(f"{etiqueta} {diff.ruta_relativa} → {diff.destino.parent.name}")
            else:
                errores += 1
                on_log(f"❌ Error (3 intentos) copiando {diff.ruta_relativa}")
            on_progress(ok + errores, total)
        on_log(f"🏁 Espejo: {ok} copiados, {errores} errores de {total} pendientes.")
        return ok, errores
