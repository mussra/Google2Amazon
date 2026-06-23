"""
Motor de búsqueda de duplicados: indexa archivos, calcula hashes en paralelo
usando un pool de hilos real (no limitado a unidades físicas), agrupa por hash
y opcionalmente por similitud perceptual de imágenes.

CAMBIOS v2:
  - Particionado por lotes: los hilos se distribuyen sobre el conjunto completo
    de archivos, no solo por unidad de disco. Siempre se usan max_hilos hilos.
  - Pre-filtro por tamaño: SOLO se calcula el hash de archivos cuyo tamaño
    aparece en al menos otro archivo (candidatos reales a duplicado).
  - Similitud perceptual de imágenes: modo "similar" con umbral de distancia
    Hamming configurable (requiere Pillow + imagehash).
  - Estadísticas por tipo de archivo.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .constants import CHK_DUP, DICCIONARIO_EXTENSIONES
from .persistence import HashCache

logger = logging.getLogger(__name__)

_EXTS_IMAGEN = DICCIONARIO_EXTENSIONES["fotos"]


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
    # Similitud perceptual: "exact" | "similar"
    modo_similitud: str = "exact"
    # Umbral distancia Hamming para modo "similar" (0-64); recomendado 8-12
    umbral_similitud: int = 10


@dataclass
class FileNode:
    path: str
    size: int
    phash: str | None = None   # hash perceptual (solo imágenes, modo similar)


ThreadStatusCallback = Callable[[str, str], None]
ConsoleCallback = Callable[[str], None]


def _compute_phash(ruta: Path) -> str | None:
    """Hash perceptual MD5-like usando imagehash. Retorna hex string o None."""
    try:
        import imagehash
        from PIL import Image
        img = Image.open(ruta)
        return str(imagehash.phash(img))
    except Exception as exc:
        logger.debug("phash fallido en %s: %s", ruta, exc)
        return None


def _phash_distance(h1: str, h2: str) -> int:
    """Distancia Hamming entre dos strings hex de phash."""
    try:
        import imagehash
        return imagehash.hex_to_hash(h1) - imagehash.hex_to_hash(h2)
    except Exception:
        return 64  # máximo = totalmente distintos


class DuplicateEngine:
    """Escaneo concurrente y detección de duplicados exactos y similares."""

    def __init__(self, hash_cache: HashCache | None = None,
                 checkpoint_path: Path = CHK_DUP):
        self.hash_cache = hash_cache or HashCache()
        self.checkpoint_path = checkpoint_path

        self._cancelado = threading.Event()
        self._lock = threading.Lock()
        self._mapa_resultados: dict[str, list[FileNode]] = {}
        self._checkpoint_data: dict[str, dict] = {}
        self._stats: dict[str, int] = defaultdict(int)

    def cancelar(self) -> None:
        self._cancelado.set()

    def _esta_cancelado(self) -> bool:
        return self._cancelado.is_set()

    # ------------------------------------------------------------------
    # Clasificación de extensiones
    # ------------------------------------------------------------------
    def _clasificar_extension(self, ext: str, opts: DuplicateOptions) -> bool:
        if ext in DICCIONARIO_EXTENSIONES["fotos"]:
            return opts.incluir_fotos
        if ext in DICCIONARIO_EXTENSIONES["videos"]:
            return opts.incluir_videos
        if ext in DICCIONARIO_EXTENSIONES["documentos"]:
            return opts.incluir_docs
        return opts.incluir_otros

    # ------------------------------------------------------------------
    # Indexación completa → lista plana de (ruta, tamaño)
    # ------------------------------------------------------------------
    def _indexar_archivos(self, rutas_origen: list[str],
                           opts: DuplicateOptions) -> list[tuple[str, int]]:
        """Recorre el árbol y devuelve lista plana de (path, size)."""
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
                        stat = os.stat(ruta_completa)
                        if stat.st_size <= 0:
                            continue
                        resultado.append((ruta_completa, stat.st_size))
                        with self._lock:
                            self._stats[ext] += 1
                    except OSError as exc:
                        logger.debug("No se pudo procesar %s: %s", ruta_completa, exc)
        return resultado

    # ------------------------------------------------------------------
    # Pre-filtro por tamaño: descartar únicos antes de hashear
    # ------------------------------------------------------------------
    @staticmethod
    def _filtrar_candidatos(archivos: list[tuple[str, int]]) -> list[tuple[str, int]]:
        """Retorna solo archivos cuyo tamaño aparece en ≥2 ficheros."""
        conteo: dict[int, int] = defaultdict(int)
        for _, sz in archivos:
            conteo[sz] += 1
        return [(p, sz) for p, sz in archivos if conteo[sz] >= 2]

    # ------------------------------------------------------------------
    # Registro en resultados (con llave compuesta opcional)
    # ------------------------------------------------------------------
    def _registrar(self, ruta: str, tamano: int, digest: str,
                    opts: DuplicateOptions, phash: str | None = None) -> None:
        llave = digest
        if opts.filtrar_por_nombre:
            llave += f"_N_{os.path.basename(ruta)}"
        if opts.filtrar_por_tamano:
            llave += f"_T_{tamano}"
        nodo = FileNode(path=ruta, size=tamano, phash=phash)
        self._mapa_resultados.setdefault(llave, []).append(nodo)

    # ------------------------------------------------------------------
    # Trabajador de lote (hilo individual)
    # ------------------------------------------------------------------
    def _trabajador_lote(self, nombre_hilo: str, lote: list[tuple[str, int]],
                          opts: DuplicateOptions,
                          on_thread_status: ThreadStatusCallback) -> None:
        total = len(lote)
        for idx, (ruta, tamano) in enumerate(lote):
            if self._esta_cancelado():
                on_thread_status(nombre_hilo, "🛑 Cancelado.")
                return

            porcentaje = int((idx / total) * 100) if total else 100
            on_thread_status(nombre_hilo, f"[{porcentaje}%] {os.path.basename(ruta)}")

            try:
                path_obj = Path(ruta)
                stat = path_obj.stat()
                mtime = stat.st_mtime

                # Intentar usar checkpoint
                with self._lock:
                    entrada_cache = self._checkpoint_data.get(ruta)
                hash_existente = None
                if (entrada_cache
                        and entrada_cache.get("size") == tamano
                        and entrada_cache.get("mtime") == mtime):
                    hash_existente = entrada_cache.get("hash")

                if hash_existente:
                    phash = entrada_cache.get("phash") if entrada_cache else None
                    with self._lock:
                        self._registrar(ruta, tamano, hash_existente, opts, phash)
                    continue

                # Calcular hash MD5
                digest = self.hash_cache._compute_hash(
                    path_obj, 65536, self._esta_cancelado)
                if not digest:
                    continue

                # Hash perceptual para imágenes en modo similar
                phash: str | None = None
                ext = path_obj.suffix.lower()
                if opts.modo_similitud == "similar" and ext in _EXTS_IMAGEN:
                    phash = _compute_phash(path_obj)

                with self._lock:
                    self._checkpoint_data[ruta] = {
                        "size": tamano, "mtime": mtime,
                        "hash": digest, "phash": phash,
                    }
                    self._registrar(ruta, tamano, digest, opts, phash)

            except OSError as exc:
                logger.debug("Error analizando %s: %s", ruta, exc)

        on_thread_status(nombre_hilo, "✅ Finalizado.")

    # ------------------------------------------------------------------
    # Agrupación por similitud perceptual (post-proceso)
    # ------------------------------------------------------------------
    def _agrupar_similares(self, duplicados_exactos: dict[str, list[FileNode]],
                            umbral: int) -> dict[str, list[FileNode]]:
        """Fusiona grupos de imágenes cuya distancia Hamming ≤ umbral."""
        # Recopilar todos los nodos con phash que NO ya son duplicados exactos
        nodos_con_phash: list[FileNode] = []
        for nodos in duplicados_exactos.values():
            for n in nodos:
                if n.phash:
                    nodos_con_phash.append(n)

        # También incluir singletons que podrían ser similares a otros
        for nodos in self._mapa_resultados.values():
            if len(nodos) == 1 and nodos[0].phash:
                nodos_con_phash.append(nodos[0])

        # Union-Find simple para agrupar similares
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

        grupos_similares: dict[str, list[FileNode]] = defaultdict(list)
        path_map = {n.path: n for n in nodos_con_phash}
        for p in paths:
            grupos_similares[find(p)].append(path_map[p])

        resultado = dict(duplicados_exactos)
        for rep, nodos in grupos_similares.items():
            if len(nodos) >= 2:
                llave_sim = f"SIMILAR_{rep}"
                if llave_sim not in resultado:
                    resultado[llave_sim] = nodos
        return resultado

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def buscar_duplicados(self, rutas_origen: list[str], opts: DuplicateOptions,
                           on_console: ConsoleCallback,
                           on_thread_status: ThreadStatusCallback
                           ) -> tuple[dict[str, list[FileNode]], dict]:
        """Ejecuta escaneo (llamar desde hilo de fondo). Retorna (duplicados, métricas)."""
        self._cancelado.clear()
        self._mapa_resultados = {}
        self._stats = defaultdict(int)
        self._checkpoint_data = self._cargar_checkpoint()

        t_inicio = time.time()
        on_console("🔍 Indexando archivos...\n")

        todos_los_archivos = self._indexar_archivos(rutas_origen, opts)
        total_indexados = len(todos_los_archivos)

        on_console(f"📊 Indexados: {total_indexados} archivos. Aplicando pre-filtro por tamaño...\n")
        candidatos = self._filtrar_candidatos(todos_los_archivos)
        descartados = total_indexados - len(candidatos)
        on_console(
            f"✅ Pre-filtro: {len(candidatos)} candidatos a duplicado "
            f"({descartados} descartados por tamaño único — sin hash calculado).\n"
        )

        if not candidatos:
            on_console("❌ No hay candidatos a duplicado tras el pre-filtro.\n")
            return {}, {"ficheros": total_indexados, "directorios": len(rutas_origen),
                        "tiempo_ms": 0.0, "stats": dict(self._stats)}

        # Particionado por lotes para garantizar N hilos activos
        n_hilos = max(1, min(opts.max_hilos, len(candidatos)))
        lote_size = max(1, len(candidatos) // n_hilos)
        lotes = [candidatos[i:i + lote_size] for i in range(0, len(candidatos), lote_size)]
        # Si el particionado generó más lotes que hilos (resto), fusionar el último
        while len(lotes) > n_hilos:
            lotes[-2].extend(lotes[-1])
            lotes.pop()

        on_console(
            f"⚡ Lanzando {len(lotes)} hilos sobre {len(candidatos)} candidatos "
            f"({lote_size} archivos/hilo aprox.)...\n"
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_hilos) as executor:
            futuros = [
                executor.submit(
                    self._trabajador_lote,
                    f"Hilo {i + 1}",
                    lote,
                    opts,
                    on_thread_status,
                )
                for i, lote in enumerate(lotes)
            ]
            concurrent.futures.wait(futuros)

        if not self._esta_cancelado():
            self._guardar_checkpoint()

        duplicados_exactos = {
            k: v for k, v in self._mapa_resultados.items() if len(v) > 1
        }

        # Similitud perceptual (post-proceso, monohilo)
        if opts.modo_similitud == "similar":
            on_console("🖼️ Calculando similitud perceptual de imágenes...\n")
            duplicados_final = self._agrupar_similares(duplicados_exactos, opts.umbral_similitud)
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
            logger.error("send2trash no instalado; no se pudo enviar a papelera %s", ruta_abs)
            return 0

    def exportar_csv(self, duplicados: dict[str, list[FileNode]], ruta_csv: str) -> bool:
        """Exporta el informe de duplicados a CSV. Mejora #3."""
        import csv
        try:
            with open(ruta_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Grupo", "Archivo", "Ruta", "Tamaño_MB", "Tipo", "Hash_Perceptual"])
                for idx, (llave, nodos) in enumerate(duplicados.items(), 1):
                    tipo = "SIMILAR" if llave.startswith("SIMILAR_") else "EXACTO"
                    for nodo in nodos:
                        p = Path(nodo.path)
                        writer.writerow([
                            idx, p.name, str(p.parent),
                            f"{nodo.size / (1024*1024):.3f}",
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
            logger.warning("Checkpoint de duplicados ilegible: %s", exc)
            return {}

    def _guardar_checkpoint(self) -> None:
        try:
            with open(self.checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(self._checkpoint_data, f, indent=4)
        except OSError as exc:
            logger.warning("No se pudo guardar checkpoint: %s", exc)
