"""
Gestión de sesiones de copia con identificación de disco por ID de volumen,
no por letra de unidad. Así una sesión «recuerda» los discos aunque cambien
de letra al reconectarse (Windows) o de punto de montaje (Linux/macOS).
"""
from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .constants import APP_HOME

SESSIONS_FILE = APP_HOME / "CopyFiles_sessions.json"

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Identificación de disco por ID de volumen (cross-platform)
# ──────────────────────────────────────────────────────────────────────────────

def _volume_id_windows(path: str) -> str | None:
    """Lee el número de serie de volumen con vol.exe (rápido, sin privilegios)."""
    try:
        drive = Path(path).anchor  # "C:\\"
        result = subprocess.run(
            ["cmd", "/c", f"vol {drive}"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            if "número de serie" in line.lower() or "serial number" in line.lower() or "série" in line.lower():
                parts = line.split()
                if parts:
                    return parts[-1].replace("-", "").upper()
        # Fallback: usar wmic
        result2 = subprocess.run(
            ["wmic", "logicaldisk", "where",
             f"DeviceID='{drive.rstrip(chr(92))}'", "get", "VolumeSerialNumber"],
            capture_output=True, text=True, timeout=5,
        )
        lines = [l.strip() for l in result2.stdout.splitlines() if l.strip()]
        if len(lines) >= 2:
            return lines[1].upper()
    except Exception as exc:
        logger.debug("_volume_id_windows failed: %s", exc)
    return None


def _volume_id_unix(path: str) -> str | None:
    """Obtiene UUID de partición via blkid o diskutil (macOS)."""
    try:
        real = str(Path(path).resolve())
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["diskutil", "info", real],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if "Volume UUID" in line:
                    return line.split(":")[-1].strip()
        else:
            # Linux: encontrar dispositivo del mount point
            result = subprocess.run(
                ["df", "--output=source", real],
                capture_output=True, text=True, timeout=5,
            )
            lines = result.stdout.strip().splitlines()
            device = lines[-1].strip() if len(lines) >= 2 else ""
            if device:
                result2 = subprocess.run(
                    ["blkid", "-s", "UUID", "-o", "value", device],
                    capture_output=True, text=True, timeout=5,
                )
                uuid = result2.stdout.strip()
                if uuid:
                    return uuid
    except Exception as exc:
        logger.debug("_volume_id_unix failed: %s", exc)
    return None


def get_volume_id(path: str) -> str:
    """
    Retorna un ID estable del volumen que contiene ``path``.
    Fallback a un hash del mount-point si el SO no coopera.
    """
    vid: str | None = None
    try:
        if platform.system() == "Windows":
            vid = _volume_id_windows(path)
        else:
            vid = _volume_id_unix(path)
    except Exception:
        pass

    if not vid:
        # Fallback: usar st_dev (device id del sistema de archivos)
        try:
            vid = f"DEV_{os.stat(path).st_dev:X}"
        except OSError:
            vid = "UNKNOWN"
    return vid


def resolve_path_by_volume(vol_id: str, subpath: str) -> str | None:
    """
    Dado un vol_id y una subruta relativa, intenta encontrar el punto de montaje
    actual del volumen en el sistema para reconstruir la ruta absoluta actual.
    Solo útil en Windows (donde la letra puede cambiar).
    """
    if platform.system() != "Windows":
        return None
    try:
        import string
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                vid_found = _volume_id_windows(drive)
                if vid_found and vid_found == vol_id:
                    return str(Path(drive) / subpath)
    except Exception as exc:
        logger.debug("resolve_path_by_volume: %s", exc)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Modelo de sesión
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DiskRef:
    """Referencia a un disco: guarda la ruta original Y el ID de volumen."""
    path: str
    volume_id: str = ""
    label: str = ""          # etiqueta opcional (nombre del disco)

    def resolve(self) -> str:
        """
        Retorna la ruta válida actual. Si el vol_id coincide con otro mount
        point (letra cambió en Windows) usa ese. Si no, devuelve la ruta original.
        """
        if self.volume_id and platform.system() == "Windows":
            # Calcular la subruta relativa respecto a la raíz de la letra guardada
            p = Path(self.path)
            drive_root = Path(p.anchor)
            try:
                rel = p.relative_to(drive_root)
            except ValueError:
                rel = Path(p.name)
            resolved = resolve_path_by_volume(self.volume_id, str(rel))
            if resolved and Path(resolved).exists():
                return resolved
        return self.path

    @classmethod
    def from_path(cls, path: str) -> "DiskRef":
        p = Path(path)
        vid = get_volume_id(path) if p.exists() else ""
        # Intentar obtener etiqueta del volumen en Windows
        label = ""
        if platform.system() == "Windows" and p.exists():
            try:
                result = subprocess.run(
                    ["cmd", "/c", f"vol {p.anchor}"],
                    capture_output=True, text=True, timeout=5,
                )
                first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
                # "El volumen de la unidad C es Sistema" → "Sistema"
                for kw in [" es ", " is ", " ist "]:
                    if kw in first_line:
                        label = first_line.split(kw, 1)[-1].strip()
                        break
            except Exception:
                pass
        return cls(path=path, volume_id=vid, label=label)


@dataclass
class Session:
    name: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    last_used: str = ""
    origen: DiskRef = field(default_factory=lambda: DiskRef(""))
    destinos: list[DiskRef] = field(default_factory=list)
    # Snapshot de config relevante (patron, filtros, etc.)
    config_snapshot: dict[str, Any] = field(default_factory=dict)

    def mark_used(self) -> None:
        self.last_used = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        origen_raw = d.pop("origen", {})
        destinos_raw = d.pop("destinos", [])
        s = cls(**d)
        s.origen = DiskRef(**origen_raw) if isinstance(origen_raw, dict) else DiskRef(str(origen_raw))
        s.destinos = [
            DiskRef(**dr) if isinstance(dr, dict) else DiskRef(str(dr))
            for dr in destinos_raw
        ]
        return s

    def display_summary(self) -> str:
        usado = f"  Último uso: {self.last_used}" if self.last_used else ""
        ndest = len(self.destinos)
        return f"{self.name}  ({ndest} destino{'s' if ndest != 1 else ''}){usado}"


# ──────────────────────────────────────────────────────────────────────────────
# Gestor de sesiones
# ──────────────────────────────────────────────────────────────────────────────

class SessionManager:
    def __init__(self, path: Path = SESSIONS_FILE):
        self.path = path
        self._sessions: list[Session] = self._load()

    def _load(self) -> list[Session]:
        if not self.path.exists():
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return [Session.from_dict(d) for d in raw]
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("Sessions file unreadable: %s", exc)
            return []

    def _save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump([s.to_dict() for s in self._sessions], f, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.error("Cannot save sessions: %s", exc)

    @property
    def sessions(self) -> list[Session]:
        return list(self._sessions)

    def get(self, name: str) -> Session | None:
        return next((s for s in self._sessions if s.name == name), None)

    def save_session(self, session: Session) -> None:
        existing = self.get(session.name)
        if existing:
            self._sessions.remove(existing)
        self._sessions.insert(0, session)
        self._save()

    def delete(self, name: str) -> bool:
        before = len(self._sessions)
        self._sessions = [s for s in self._sessions if s.name != name]
        if len(self._sessions) < before:
            self._save()
            return True
        return False

    def names(self) -> list[str]:
        return [s.name for s in self._sessions]
