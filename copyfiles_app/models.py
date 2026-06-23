"""
Modelo de configuración de la aplicación.

Sustituye al antiguo diccionario "config_defecto" suelto por un
``dataclass`` tipado con validación básica, serialización a/desde JSON
robusta frente a archivos corruptos o incompletos, y un único punto de
verdad para los valores por defecto.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .constants import CONFIG_FILE

logger = logging.getLogger(__name__)


@dataclass
class AppConfig:
    idioma: str = "es"
    origen: str = ""
    destino: str = ""

    chk_fotos: bool = True
    chk_videos: bool = False
    chk_docs: bool = False
    chk_otros: bool = False

    modo_mover: bool = False
    dry_run: bool = False
    copia_atomica: bool = True
    colision: str = "renombrar"  # "renombrar" | "omitir"

    regex_excluir: str = r"(?i)\.(tmp|bak|ds_store|thumbs\.db)$"
    patron_renombrado: str = "{año}/{mes}/{dia}/ARCHIVO_{secuencial}"
    ren_regex_busca: str = ""
    ren_regex_reemplaza: str = ""

    accion_duplicados: str = "Enviar a la Papelera (Seguro)"
    filtro_tamano_min: str = ""
    filtro_tamano_max: str = ""

    modo_apariencia: str = "System"
    color_acento_personalizado: str = "#1f6aa5"
    color_fondo_paneles: str = "#2b2b2b"

    modo_similitud_dup: str = "exact"          # "exact" | "similar"
    umbral_similitud_dup: int = 10              # Hamming distance threshold

    # Campos heredados de versiones anteriores que ya no se usan pero se
    # toleran para no romper la carga de configuraciones antiguas.
    extra: dict = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> "AppConfig":
        """Carga la configuración desde disco, tolerando archivos
        ausentes, corruptos o con campos desconocidos."""
        defaults = cls()
        if not path.exists():
            return defaults

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw: dict[str, Any] = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("No se pudo leer la configuración (%s): %s", path, exc)
            return defaults

        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        clean_kwargs = {k: v for k, v in raw.items() if k in known_fields and k != "extra"}
        unknown = {k: v for k, v in raw.items() if k not in known_fields}

        merged = asdict(defaults)
        merged.update(clean_kwargs)
        merged["extra"] = unknown
        try:
            return cls(**merged)
        except TypeError as exc:
            logger.warning("Configuración inválida, usando valores por defecto: %s", exc)
            return defaults

    def save(self, path: Path = CONFIG_FILE) -> bool:
        """Persiste la configuración a disco. Devuelve True si tuvo éxito."""
        try:
            data = asdict(self)
            data.pop("extra", None)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            return True
        except OSError as exc:
            logger.error("No se pudo guardar la configuración: %s", exc)
            return False

    def size_filter_mb(self) -> tuple[float | None, float | None]:
        """Devuelve (min_mb, max_mb) parseados de forma segura, o None
        si el campo está vacío o no es numérico."""

        def _parse(value: str) -> float | None:
            value = (value or "").strip()
            if not value:
                return None
            try:
                return float(value)
            except ValueError:
                return None

        return _parse(self.filtro_tamano_min), _parse(self.filtro_tamano_max)