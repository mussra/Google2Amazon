"""
Persistencia de bajo nivel: caché de hashes (SQLite) e histórico de
operaciones (JSON).

Mejoras respecto a la versión original:
  * La caché de hashes ya NO abre una conexión SQLite nueva por cada
    archivo procesado; usa una conexión por hilo (``threading.local``)
    que se reutiliza, lo que reduce drásticamente el overhead en
    escaneos masivos y evita "database is locked" bajo concurrencia.
  * Todas las operaciones de E/S están protegidas con manejo de
    excepciones específico y logueado, en vez de ``except: pass``
    silenciosos que ocultaban errores reales.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .constants import HASH_DB_FILE, HISTORY_FILE

logger = logging.getLogger(__name__)


class HashCache:
    """Caché persistente de hashes MD5 indexada por ruta absoluta,
    invalidada automáticamente cuando cambian ``mtime`` o ``size``.

    Es segura para uso concurrente: cada hilo obtiene su propia
    conexión SQLite (las conexiones sqlite3 no son seguras para
    compartir entre hilos).
    """

    def __init__(self, db_path: Path = HASH_DB_FILE):
        self.db_path = db_path
        self._local = threading.local()
        self._init_schema()

    def _connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL;")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        try:
            with sqlite3.connect(self.db_path, timeout=30) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cache_hashes (
                        path TEXT PRIMARY KEY,
                        mtime REAL,
                        size INTEGER,
                        hash TEXT
                    )
                    """
                )
                conn.commit()
        except sqlite3.Error as exc:
            logger.error("No se pudo inicializar la base de datos de hashes: %s", exc)

    def get_or_compute(self, ruta: Path, block_size: int = 65536,
                        cancel_check=None) -> str | None:
        """Devuelve el hash MD5 del archivo, usando la caché cuando sea
        válida o recalculándolo en caso contrario.

        ``cancel_check`` es una función opcional sin argumentos que,
        si devuelve True, aborta el cálculo (usado para cancelación
        cooperativa desde la interfaz).
        """
        try:
            stat = ruta.stat()
        except OSError as exc:
            logger.debug("No se pudo leer metadata de %s: %s", ruta, exc)
            return None

        path_str = str(ruta.resolve())
        mtime, size = stat.st_mtime, stat.st_size

        try:
            conn = self._connection()
            row = conn.execute(
                "SELECT mtime, size, hash FROM cache_hashes WHERE path=?", (path_str,)
            ).fetchone()
            if row and row[0] == mtime and row[1] == size:
                return row[2]
        except sqlite3.Error as exc:
            logger.debug("Fallo de lectura en caché de hashes: %s", exc)

        digest = self._compute_hash(ruta, block_size, cancel_check)
        if digest is None:
            return None

        try:
            conn = self._connection()
            conn.execute(
                "REPLACE INTO cache_hashes (path, mtime, size, hash) VALUES (?, ?, ?, ?)",
                (path_str, mtime, size, digest),
            )
            conn.commit()
        except sqlite3.Error as exc:
            logger.debug("Fallo de escritura en caché de hashes: %s", exc)

        return digest

    @staticmethod
    def _compute_hash(ruta: Path, block_size: int, cancel_check) -> str | None:
        try:
            hasher = hashlib.md5()
            with open(ruta, "rb") as f:
                while True:
                    if cancel_check and cancel_check():
                        return None
                    chunk = f.read(block_size)
                    if not chunk:
                        break
                    hasher.update(chunk)
            return hasher.hexdigest()
        except OSError as exc:
            logger.debug("No se pudo leer %s para calcular hash: %s", ruta, exc)
            return None

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


class HistoryManager:
    """Registro histórico de operaciones (copiar/mover/eliminar)."""

    def __init__(self, path: Path = HISTORY_FILE):
        self.path = path
        self._lock = threading.Lock()

    def append(self, servicio: str, accion: str, origen, destino="", bytes_tamano: int = 0) -> None:
        entrada = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "servicio": servicio,
            "accion": accion,
            "origen": str(origen),
            "destino": str(destino) if destino else "N/A (Eliminado)",
            "bytes": bytes_tamano,
        }
        with self._lock:
            historial = self._read()
            historial.append(entrada)
            self._write(historial)

    def load(self) -> list[dict]:
        with self._lock:
            return self._read()

    def clear(self) -> bool:
        with self._lock:
            try:
                if self.path.exists():
                    self.path.unlink()
                return True
            except OSError as exc:
                logger.error("No se pudo purgar el histórico: %s", exc)
                return False

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Histórico corrupto o ilegible, se ignora: %s", exc)
            return []

    def _write(self, historial: list[dict]) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(historial, f, indent=4, ensure_ascii=False)
        except OSError as exc:
            logger.error("No se pudo escribir el histórico: %s", exc)
