"""
Configuration module for CopyFiles application.
Handles all configuration management and validation.
"""

import os
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Optional
from enum import Enum
import logging

logger = logging.getLogger(__name__)

# Configuration file paths
CONFIG_DIR = Path(os.path.expanduser("~")) / ".copyfiles"
CONFIG_FILE = CONFIG_DIR / "config.json"
CHK_SINCRO = CONFIG_DIR / "checkpoint_sincro.json"
CHK_DUP = CONFIG_DIR / "checkpoint_dup.json"
HISTORY_FILE = CONFIG_DIR / "history.json"
HASH_DB_FILE = CONFIG_DIR / "hashes.db"
LOG_FILE = CONFIG_DIR / "copyfiles.log"

# Ensure config directory exists
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# File type extensions dictionary
FILE_EXTENSIONS = {
    "fotos": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".heic", ".tiff", ".webp"},
    "videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v"},
    "documentos": {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".txt", ".odt", ".csv"}
}


class CollisionHandling(Enum):
    """How to handle file name collisions."""
    RENAME = "renombrar"
    SKIP = "omitir"


class DeletionMethod(Enum):
    """Methods for deleting duplicate files."""
    TRASH = "Enviar a la Papelera (Seguro)"
    PERMANENT = "Eliminación Destructiva Permanente"


@dataclass
class ThemePalette:
    """Theme color palette."""
    accent: str
    frame: str
    is_light: bool


THEMES: Dict[str, ThemePalette] = {
    "System": ThemePalette(accent="#1f6aa5", frame="#2b2b2b", is_light=False),
    "Dark": ThemePalette(accent="#1f6aa5", frame="#2b2b2b", is_light=False),
    "Light": ThemePalette(accent="#0d6efd", frame="#eaeaea", is_light=True),
    "Rosa 🌸": ThemePalette(accent="#d81b60", frame="#fce4ec", is_light=True),
    "Azul 🔷": ThemePalette(accent="#0052cc", frame="#e6f0ff", is_light=True),
    "Esmeralda 🌿": ThemePalette(accent="#2e7d32", frame="#e8f5e9", is_light=True),
    "Ámbar 🍯": ThemePalette(accent="#ff8f00", frame="#fff8e1", is_light=True),
    "Lavanda 🪻": ThemePalette(accent="#673ab7", frame="#f3e5f5", is_light=True),
    "Océano 🌊": ThemePalette(accent="#00838f", frame="#e0f7fa", is_light=True),
    "Flamingo 🦩": ThemePalette(accent="#e91e63", frame="#fbe9e7", is_light=True),
    "Cyberpunk 🔮": ThemePalette(accent="#00f0ff", frame="#1a0826", is_light=False),
    "Noche Nórdica 🌌": ThemePalette(accent="#88c0d0", frame="#2e3440", is_light=False),
    "Lava 🔥": ThemePalette(accent="#ff3d00", frame="#1e1e1e", is_light=False),
    "Bosque Oscuro 🌲": ThemePalette(accent="#81c784", frame="#1b2e24", is_light=False),
    "Carbono Premium 🥷": ThemePalette(accent="#ffffff", frame="#121212", is_light=False),
}


@dataclass
class AppConfig:
    """Application configuration with validation."""
    # Core paths
    origen: str = ""
    destino: str = ""

    # File filtering
    chk_fotos: bool = True
    chk_videos: bool = False
    chk_docs: bool = False
    chk_otros: bool = False
    filtro_tamano_min: str = ""
    filtro_tamano_max: str = ""
    regex_excluir: str = r"(?i)\.(tmp|bak|ds_store|thumbs\.db)$"

    # Synchronization options
    modo_mover: bool = False
    dry_run: bool = False
    copia_atomica: bool = True
    colision: str = "renombrar"

    # File naming
    patron_renombrado: str = "{año}/{mes}/{dia}/ARCHIVO_{secuencial}"
    ren_regex_busca: str = ""
    ren_regex_reemplaza: str = ""

    # Duplicates
    accion_duplicados: str = "Enviar a la Papelera (Seguro)"

    # UI preferences
    idioma: str = "es"
    modo_apariencia: str = "System"
    color_acento_personalizado: str = "#1f6aa5"
    color_fondo_paneles: str = "#2b2b2b"

    # Cache
    usar_cache: bool = True
    procesar_existentes: bool = False

    def validate(self) -> bool:
        """Validate configuration."""
        if self.origen and not Path(self.origen).exists():
            logger.warning(f"Origin path doesn't exist: {self.origen}")
            return False

        if self.destino and not Path(self.destino).exists():
            logger.warning(f"Destination path doesn't exist: {self.destino}")
            return False

        if self.origen and self.destino:
            if Path(self.origen).resolve() == Path(self.destino).resolve():
                logger.error("Origin and destination cannot be the same")
                return False

        # Validate file size filters
        try:
            if self.filtro_tamano_min:
                float(self.filtro_tamano_min)
            if self.filtro_tamano_max:
                float(self.filtro_tamano_max)
        except ValueError:
            logger.error("Invalid size filters")
            return False

        return True

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict) -> "AppConfig":
        """Create from dictionary."""
        valid_fields = {f.name for f in AppConfig.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return AppConfig(**filtered_data)


def load_config() -> AppConfig:
    """Load configuration from file or return defaults."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                config = AppConfig.from_dict(data)
                if config.validate():
                    logger.info("Configuration loaded successfully")
                    return config
        except Exception as e:
            logger.error(f"Error loading config: {e}")

    logger.info("Using default configuration")
    return AppConfig()


def save_config(config: AppConfig) -> bool:
    """Save configuration to file."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, indent=4, ensure_ascii=False)
        logger.info("Configuration saved successfully")
        return True
    except Exception as e:
        logger.error(f"Error saving config: {e}")
        return False
