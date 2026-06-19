"""
Constantes globales de la aplicación: rutas de persistencia, mapas de
extensiones y paletas de color predefinidas.

Centralizar estos valores aquí evita "magic strings/numbers" repetidos
por todo el código y facilita cambiarlos en un único punto.
"""
from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Rutas de persistencia local de la Suite
# --------------------------------------------------------------------------
APP_HOME = Path(os.path.expanduser("~"))

CONFIG_FILE = APP_HOME / "CopyFiles_config.json"
CHK_SINCRO = APP_HOME / "CopyFiles_checkpoint_sincro.json"
CHK_DUP = APP_HOME / "CopyFiles_checkpoint_dup.json"
HISTORY_FILE = APP_HOME / "CopyFiles_history.json"
HASH_DB_FILE = APP_HOME / "CopyFiles_hashes.db"

# --------------------------------------------------------------------------
# Clasificación de archivos por extensión
# --------------------------------------------------------------------------
DICCIONARIO_EXTENSIONES: dict[str, set[str]] = {
    "fotos": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".heic", ".tiff", ".webp"},
    "videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    "documentos": {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".txt", ".odt", ".csv"},
}

# --------------------------------------------------------------------------
# Paletas de color predefinidas para la interfaz
# --------------------------------------------------------------------------
PALETAS_PREDEFINIDAS: dict[str, dict] = {
    "System": {"acento": "#1f6aa5", "marco": "#2b2b2b", "es_claro": False},
    "Dark": {"acento": "#1f6aa5", "marco": "#2b2b2b", "es_claro": False},
    "Light": {"acento": "#0d6efd", "marco": "#eaeaea", "es_claro": True},
    "Rosa 🌸": {"acento": "#d81b60", "marco": "#fce4ec", "es_claro": True},
    "Azul 🔷": {"acento": "#0052cc", "marco": "#e6f0ff", "es_claro": True},
    "Esmeralda 🌿": {"acento": "#2e7d32", "marco": "#e8f5e9", "es_claro": True},
    "Ámbar 🍯": {"acento": "#ff8f00", "marco": "#fff8e1", "es_claro": True},
    "Lavanda 🪻": {"acento": "#673ab7", "marco": "#f3e5f5", "es_claro": True},
    "Océano 🌊": {"acento": "#00838f", "marco": "#e0f7fa", "es_claro": True},
    "Flamingo 🦩": {"acento": "#e91e63", "marco": "#fbe9e7", "es_claro": True},
    "Cyberpunk 🔮": {"acento": "#00f0ff", "marco": "#1a0826", "es_claro": False},
    "Noche Nórdica 🌌": {"acento": "#88c0d0", "marco": "#2e3440", "es_claro": False},
    "Lava 🔥": {"acento": "#ff3d00", "marco": "#1e1e1e", "es_claro": False},
    "Bosque Oscuro 🌲": {"acento": "#81c784", "marco": "#1b2e24", "es_claro": False},
    "Carbono Premium 🥷": {"acento": "#ffffff", "marco": "#121212", "es_claro": False},
}

DEFAULT_RENAME_TOKENS = [
    "{año}", "{mes}", "{dia}", "{nombre_origen}", "{secuencial}",
    "{usuario}", "{equipo}", "{directorio}",
]
