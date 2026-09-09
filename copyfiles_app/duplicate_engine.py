"""
Motor de búsqueda de duplicados v3 — ProcessPoolExecutor para hashing.

Arquitectura de concurrencia:
  - Indexación y pre-filtro: monohilo (I/O secuencial, barato).
  - Hashing MD5: ProcessPoolExecutor con N workers reales. Cada proceso
    hijo tiene su propia conexión SQLite al HashCache y calcula hashes
    sin interferencia del GIL. El proceso principal recoge los resultados
    y actualiza _mapa_resultados bajo lock.
  - Threshold híbrido: si los candidatos son < MIN_PROC_THRESHOLD o solo
    hay 1 CPU disponible, cae a ThreadPoolExecutor para evitar el overhead
    de spawn de procesos en lotes pequeños (~150ms/proceso en Windows).
  - Cancelación: se comprueba entre futuros; los workers en vuelo terminan
    su lote actual (máximo BATCH_SIZE archivos).
  - phash perceptual: se sigue calculando en el proceso principal tras
    recoger los hashes, para no depender de Pillow en los workers.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import multiprocessing
import os
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .constants import CHK_DUP, DICCIONARIO_EXTENSIONES
from .persistence import HashCache, HASH_DB_FILE

logger = logging.getLogger(__name__)

_EXTS_IMAGEN = frozenset(DICCIONARIO_EXTENSIONES["fotos"])

# Usar procesos solo si hay suficientes candidatos — por debajo de este umbral
# el overhead de spawn supera la ganancia de paralelismo CPU real.
MIN_PROC_THRESHOLD = 80
# Archivos por lote enviado a cada proceso hijo.
BATCH_SIZE = 60


# ──────────────────────────────────────────────────────────────────────────────
# Función worker — DEBE estar al nivel de módulo para ser picklable en Windows
# (spawn start method). No puede ser un método de clase ni un closure.
# ──────────────────────────────────────────────────────────────────────────────

def _hash_batch(
    lote: list[tuple[str, int, float, str | None]],
    # cada elemento: (ruta_abs, tamano, mtime, hash_en_checkpoint_o_None)
    db_path_str: str,
) -> list[tuple[str, int, float, str | None]]:
    """
    Proceso hijo: calcula hashes MD5 del lote usando SQLite propio.
    Retorna lista de (ruta, tamano, mtime, digest|None).
    Solo usa stdlib — sin imports relativos, sin Pillow, sin imagehash.
    """
    conn: sqlite3.Connection | None = None
    resultados: list[tuple[str, int, float, str | None]] = []
    try:
        conn = sqlite3.connect(db_path_str, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")

        for ruta, tamano, mtime, hash_cached in lote:
            digest = hash_cached  # ya teníamos del checkpoint

            if not digest:
                # 1. Buscar en SQLite
                row = conn.execute(
                    "SELECT mtime, size, hash FROM cache_hashes WHERE path=?",
                    (ruta,),
                ).fetchone()
                if row and abs(row[0] - mtime) < 0.01 and row[1] == tamano:
                    digest = row[2]
                else:
                    # 2. Calcular
                    try:
                        hasher = hashlib.md5()
                        with open(ruta, "rb") as f:
                            while True:
                                chunk = f.read(65536)
                                if not chunk:
                                    break
                                hasher.update(chunk)
                        digest = hasher.hexdigest()
                        # Guardar en caché
                        conn.execute(
                            "REPLACE INTO cache_hashes (path, mtime, size, hash)"
                            " VALUES (?, ?, ?, ?)",
                            (ruta, mtime, tamano, digest),
                        )
                        conn.commit()
                    except OSError:
                        digest = None

            resultados.append((ruta, tamano, mtime, digest))
    except Exception as exc:
        logger.debug("_hash_batch error: %s", exc)
    finally:
        if conn:
            conn.close()
    return resultados


# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses públicos
# ──────────────────────────────────────────────────────────────────────────────

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
    modo_similitud: str = "exact"   # "exact" | "similar"
    umbral_similitud: int = 10      # Hamming distance threshold


@dataclass
class FileNode:
    path: str
    size: int
    phash: str | None = None


ThreadStatusCallback = Callable[[str, str], None]
ConsoleCallback = Callable[[str], None]


def _compute_phash(ruta: Path) -> str | None:
    try:
        import imagehash
        from PIL import Image
        return str(imagehash.phash(Image.open(ruta)))
    except Exception as exc:
        logger.debug("phash fallido en %s: %s", ruta, exc)
        return None


def _phash_distance(h1: str, h2: str) -> int:
    try:
        import imagehash
        return imagehash.hex_to_hash(h1) - imagehash.hex_to_hash(h2)
    except Exception:
        return 64


# ──────────────────────────────────────────────────────────────────────────────
# Motor principal
# ──────────────────────────────────────────────────────────────────────────────

class DuplicateEngine:
    def __init__(self, hash_cache: HashCache | None = None,
                 checkpoint_path: Path = CHK_DUP):
        self.hash_cache = hash_cache or HashCache()
        self.checkpoint_path = checkpoint_path
        self.db_path_str = str(self.hash_cache.db_path)

        self._cancelado = threading.Event()
        self._lock = threading.Lock()
        self._mapa_resultados: dict[str, list[FileNode]] = {}
        self._checkpoint_data: dict[str, dict] = {}
        self._stats: dict[str, int] = defaultdict(int)

    def cancelar(self) -> None:
        self._cancelado.set()

    def _esta_cancelado(self) -> bool:
        return self._cancelado.is_set()

    def _clasificar_extension(self, ext: str, opts: DuplicateOptions) -> bool:
        if ext in DICCIONARIO_EXTENSIONES["fotos"]:    return opts.incluir_fotos
        if ext in DICCIONARIO_EXTENSIONES["videos"]:   return opts.incluir_videos
        if ext in DICCIONARIO_EXTENSIONES["documentos"]: return opts.incluir_docs
        return opts.incluir_otros

    # ------------------------------------------------------------------
    # Indexación → lista plana de (ruta_abs, tamano)
    # ------------------------------------------------------------------
    def _indexar_archivos(self, rutas_origen: list[str],
                           opts: DuplicateOptions) -> list[tuple[str, int]]:
        resultado: list[tuple[str, int]] = []
        exclusiones = [e.lower() for e in opts.exclusiones if e]
        for ruta_base in rutas_origen:
            if not os.path.exists(ruta_base):
                continue
            for raiz, _, ficheros in os.walk(ruta_base):
                if self._esta_cancelado():
                    return []
                if any(exc in raiz.lower() for exc in exclusiones):
                    continue
                for nombre in ficheros:
                    ruta_completa = os.path.join(raiz, nombre)
                    try:
                        ext = Path(ruta_completa).suffix.lower()
                        if not self._clasificar_extension(ext, opts):
                            continue
                        sz = os.stat(ruta_completa).st_size
                        if sz <= 0:
                            continue
                        resultado.append((ruta_completa, sz))
                        with self._lock:
                            self._stats[ext] += 1
                    except OSError:
                        pass
        return resultado

    @staticmethod
    def _filtrar_candidatos(archivos: list[tuple[str, int]]) -> list[tuple[str, int]]:
        conteo: dict[int, int] = defaultdict(int)
        for _, sz in archivos:
            conteo[sz] += 1
        return [(p, sz) for p, sz in archivos if conteo[sz] >= 2]

    # ------------------------------------------------------------------
    # Registro de resultados (hilo principal, bajo lock)
    # ------------------------------------------------------------------
    def _registrar(self, ruta: str, tamano: int, digest: str,
                    opts: DuplicateOptions, phash: str | None = None) -> None:
        llave = digest
        if opts.filtrar_por_nombre:
            llave += f"_N_{os.path.basename(ruta)}"
        if opts.filtrar_por_tamano:
            llave += f"_T_{tamano}"
        self._mapa_resultados.setdefault(llave, []).append(
            FileNode(path=ruta, size=tamano, phash=phash))

    # ------------------------------------------------------------------
    # Hashing con ProcessPoolExecutor (o fallback a threads)
    # ------------------------------------------------------------------
    def _ejecutar_hashing(
        self,
        candidatos: list[tuple[str, int]],
        opts: DuplicateOptions,
        on_console: ConsoleCallback,
        on_thread_status: ThreadStatusCallback,
    ) -> None:
        """
        Despacha los candidatos a workers (procesos o hilos según umbral),
        recoge resultados en el hilo principal y actualiza _mapa_resultados.
        """
        # Preparar entradas enriquecidas con mtime + hash del checkpoint
        entradas: list[tuple[str, int, float, str | None]] = []
        for ruta, tamano in candidatos:
            try:
                mtime = Path(ruta).stat().st_mtime
            except OSError:
                mtime = 0.0
            entrada_chk = self._checkpoint_data.get(ruta)
            hash_chk = None
            if (entrada_chk
                    and entrada_chk.get("size") == tamano
                    and abs(entrada_chk.get("mtime", 0) - mtime) < 0.01):
                hash_chk = entrada_chk.get("hash")
            entradas.append((ruta, tamano, mtime, hash_chk))

        n_cpu = max(1, multiprocessing.cpu_count())
        n_workers = max(1, min(opts.max_hilos, n_cpu, len(candidatos)))
        usar_procesos = (
            len(candidatos) >= MIN_PROC_THRESHOLD
            and n_cpu > 1
        )

        # Partir en lotes de BATCH_SIZE archivos
        lotes: list[list[tuple[str, int, float, str | None]]] = [
            entradas[i:i + BATCH_SIZE]
            for i in range(0, len(entradas), BATCH_SIZE)
        ]

        modo_str = "procesos" if usar_procesos else "hilos"
        on_console(
            f"⚡ Hashing con {n_workers} {modo_str} reales "
            f"({len(lotes)} lotes × {BATCH_SIZE} archivos, "
            f"GIL {'bypassado' if usar_procesos else 'activo'})...\n"
        )

        Executor = (
            concurrent.futures.ProcessPoolExecutor
            if usar_procesos
            else concurrent.futures.ThreadPoolExecutor
        )

        db = self.db_path_str
        procesados = 0
        total = len(entradas)

        with Executor(max_workers=n_workers) as executor:
            futuros = {
                executor.submit(_hash_batch, lote, db): i
                for i, lote in enumerate(lotes)
            }
            for futuro in concurrent.futures.as_completed(futuros):
                if self._esta_cancelado():
                    # Cancelar futuros aún no iniciados
                    for f in futuros:
                        f.cancel()
                    on_console("🛑 Hashing cancelado.\n")
                    return

                num_lote = futuros[futuro]
                try:
                    resultados: list[tuple[str, int, float, str | None]] = futuro.result()
                except Exception as exc:
                    logger.warning("Lote %d falló: %s", num_lote, exc)
                    continue

                # Recoger resultados en el hilo principal (sin GIL aquí)
                for ruta, tamano, mtime, digest in resultados:
                    procesados += 1
                    if not digest:
                        continue

                    # Calcular phash en el proceso principal (Pillow no en workers)
                    phash: str | None = None
                    ext = Path(ruta).suffix.lower()
                    if opts.modo_similitud == "similar" and ext in _EXTS_IMAGEN:
                        phash = _compute_phash(Path(ruta))

                    with self._lock:
                        self._checkpoint_data[ruta] = {
                            "size": tamano, "mtime": mtime,
                            "hash": digest, "phash": phash,
                        }
                        self._registrar(ruta, tamano, digest, opts, phash)

                pct = int(procesados / total * 100) if total else 100
                on_thread_status(
                    f"Pool ({modo_str})",
                    f"[{pct}%] {procesados}/{total} archivos hasheados",
                )

    # ------------------------------------------------------------------
    # Similitud perceptual (post-proceso, monohilo)
    # ------------------------------------------------------------------
    def _agrupar_similares(self, duplicados_exactos: dict[str, list[FileNode]],
                            umbral: int) -> dict[str, list[FileNode]]:
        nodos_con_phash: list[FileNode] = []
        for nodos in duplicados_exactos.values():
            nodos_con_phash.extend(n for n in nodos if n.phash)
        for nodos in self._mapa_resultados.values():
            if len(nodos) == 1 and nodos[0].phash:
                nodos_con_phash.append(nodos[0])

        parent: dict[str, str] = {n.path: n.path for n in nodos_con_phash}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        phash_map = {n.path: n.phash for n in nodos_con_phash if n.phash}
        paths = list(phash_map.keys())
        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                if _phash_distance(phash_map[paths[i]], phash_map[paths[j]]) <= umbral:
                    ra, rb = find(paths[i]), find(paths[j])
                    if ra != rb:
                        parent[rb] = ra

        grupos: dict[str, list[FileNode]] = defaultdict(list)
        path_map = {n.path: n for n in nodos_con_phash}
        for p in paths:
            grupos[find(p)].append(path_map[p])

        resultado = dict(duplicados_exactos)
        for rep, nodos in grupos.items():
            if len(nodos) >= 2:
                llave = f"SIMILAR_{rep}"
                if llave not in resultado:
                    resultado[llave] = nodos
        return resultado

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def buscar_duplicados(
        self,
        rutas_origen: list[str],
        opts: DuplicateOptions,
        on_console: ConsoleCallback,
        on_thread_status: ThreadStatusCallback,
    ) -> tuple[dict[str, list[FileNode]], dict]:
        self._cancelado.clear()
        self._mapa_resultados = {}
        self._stats = defaultdict(int)
        self._checkpoint_data = self._cargar_checkpoint()

        t_inicio = time.time()
        on_console("🔍 Indexando archivos...\n")

        todos = self._indexar_archivos(rutas_origen, opts)
        total_indexados = len(todos)

        on_console(f"📊 {total_indexados} archivos. Pre-filtro por tamaño...\n")
        candidatos = self._filtrar_candidatos(todos)
        descartados = total_indexados - len(candidatos)
        on_console(
            f"✅ {len(candidatos)} candidatos "
            f"({descartados} descartados — tamaño único, sin hash).\n"
        )

        if not candidatos:
            on_console("❌ Sin candidatos tras el pre-filtro.\n")
            return {}, {
                "ficheros": total_indexados, "directorios": len(rutas_origen),
                "tiempo_ms": 0.0, "stats": dict(self._stats),
            }

        self._ejecutar_hashing(candidatos, opts, on_console, on_thread_status)

        if not self._esta_cancelado():
            self._guardar_checkpoint()

        duplicados_exactos = {
            k: v for k, v in self._mapa_resultados.items() if len(v) > 1
        }

        if opts.modo_similitud == "similar":
            on_console("🖼️ Agrupando similares (Union-Find)...\n")
            duplicados_final = self._agrupar_similares(
                duplicados_exactos, opts.umbral_similitud)
        else:
            duplicados_final = duplicados_exactos

        metricas = {
            "ficheros": total_indexados,
            "candidatos": len(candidatos),
            "directorios": len(rutas_origen),
            "tiempo_ms": (time.time() - t_inicio) * 1000,
            "stats": dict(self._stats),
        }
        return duplicados_final, metricas

    def eliminar(self, ruta_abs: str, metodo: str) -> int:
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
            logger.error("send2trash no instalado: %s", ruta_abs)
            return 0

    def exportar_csv(self, duplicados: dict[str, list[FileNode]], ruta_csv: str) -> bool:
        import csv
        try:
            with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["Grupo", "Archivo", "Ruta", "Tamaño_MB", "Tipo", "Hash_Perceptual"])
                for idx, (llave, nodos) in enumerate(duplicados.items(), 1):
                    tipo = "SIMILAR" if llave.startswith("SIMILAR_") else "EXACTO"
                    for nodo in nodos:
                        p = Path(nodo.path)
                        w.writerow([
                            idx, p.name, str(p.parent),
                            f"{nodo.size/(1024*1024):.3f}",
                            tipo, nodo.phash or "",
                        ])
            return True
        except OSError as exc:
            logger.error("No se pudo exportar CSV: %s", exc)
            return False

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
            logger.warning("Checkpoint ilegible: %s", exc)
            return {}

    def _guardar_checkpoint(self) -> None:
        tmp = self.checkpoint_path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._checkpoint_data, f, indent=2)
            os.replace(tmp, self.checkpoint_path)
        except OSError as exc:
            logger.warning("No se pudo guardar checkpoint: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
