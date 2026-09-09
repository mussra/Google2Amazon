"""
Generación de informes de operaciones de copia en HTML y CSV.
No depende de librerías externas más allá de la stdlib.
"""
from __future__ import annotations

import csv
import os
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sync_engine import ReportEntry

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Informe CopyFiles — {fecha}</title>
<style>
  body {{ font-family: Segoe UI, Arial, sans-serif; margin: 32px; background: #f5f5f5; }}
  h1 {{ color: #1b5e20; }} h2 {{ color: #37474f; border-bottom: 2px solid #ccc; padding-bottom:4px; }}
  .summary {{ display:flex; gap:24px; margin:16px 0; }}
  .card {{ background:#fff; border-radius:8px; padding:16px 24px; box-shadow:0 1px 4px #0002; min-width:120px; }}
  .card .val {{ font-size:2em; font-weight:bold; }} .ok {{ color:#2e7d32; }} .err {{ color:#b71c1c; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; border-radius:8px;
           box-shadow:0 1px 4px #0002; margin-top:12px; }}
  th {{ background:#37474f; color:#fff; padding:8px 12px; text-align:left; }}
  td {{ padding:6px 12px; border-bottom:1px solid #eee; font-size:0.85em; }}
  tr.ok td {{ background:#f1f8f1; }} tr.err td {{ background:#fff5f5; }}
  tr:hover td {{ background:#fffde7; }}
</style>
</head>
<body>
<h1>📋 Informe de copia — CopyFiles</h1>
<p>Generado: <b>{fecha}</b> &nbsp;|&nbsp; Origen: <b>{origen}</b> &nbsp;|&nbsp;
   Destinos: <b>{destinos}</b></p>
<div class="summary">
  <div class="card"><div class="val ok">{n_ok}</div>Copiados/Movidos</div>
  <div class="card"><div class="val" style="color:#ff8f00">{n_omit}</div>Omitidos</div>
  <div class="card"><div class="val err">{n_err}</div>Errores</div>
  <div class="card"><div class="val">{mb_total:.1f} MB</div>Transferidos</div>
</div>
<h2>Detalle de operaciones</h2>
<table>
<tr><th>#</th><th>Hora</th><th>Acción</th><th>Archivo origen</th><th>Archivo destino</th><th>Tamaño</th></tr>
{filas}
</table>
</body></html>
"""

_FILA = (
    '<tr class="{cls}"><td>{idx}</td><td>{ts}</td><td><b>{accion}</b></td>'
    "<td>{origen}</td><td>{destino}</td><td>{mb:.2f} MB</td></tr>"
)


def generar_html(
    entries: list["ReportEntry"],
    origen: str,
    destinos: list[str],
    ruta_salida: Path,
) -> None:
    n_ok = sum(1 for e in entries if e.ok and e.accion not in ("OMITIDO",))
    n_omit = sum(1 for e in entries if e.accion == "OMITIDO")
    n_err = sum(1 for e in entries if not e.ok)
    mb_total = sum(e.bytes_size for e in entries if e.ok) / (1024 * 1024)

    filas = "\n".join(
        _FILA.format(
            cls="ok" if e.ok else "err",
            idx=i + 1,
            ts=e.timestamp,
            accion=e.accion,
            origen=Path(e.origen).name,
            destino=Path(e.destino).name if e.destino else "—",
            mb=e.bytes_size / (1024 * 1024),
        )
        for i, e in enumerate(entries)
    )

    html = _HTML_TEMPLATE.format(
        fecha=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        origen=origen,
        destinos=", ".join(destinos),
        n_ok=n_ok, n_omit=n_omit, n_err=n_err, mb_total=mb_total,
        filas=filas,
    )
    ruta_salida.write_text(html, encoding="utf-8")


def generar_csv(
    entries: list["ReportEntry"],
    ruta_salida: Path,
) -> None:
    with open(ruta_salida, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Timestamp", "Accion", "Origen", "Destino", "Bytes", "OK"])
        for e in entries:
            w.writerow([e.timestamp, e.accion, e.origen, e.destino, e.bytes_size, e.ok])


def abrir_archivo(ruta: Path) -> None:
    """Abre el informe en el visor predeterminado del sistema."""
    try:
        if platform.system() == "Windows":
            os.startfile(str(ruta))
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(ruta)])
        else:
            subprocess.Popen(["xdg-open", str(ruta)])
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Apagado programado
# ──────────────────────────────────────────────────────────────────────────────

def programar_apagado(delay_segundos: int = 60) -> None:
    """
    Programa el apagado del equipo con ``delay_segundos`` de antelación.
    Usa shutdown nativo en cada plataforma.
    """
    if platform.system() == "Windows":
        subprocess.Popen(["shutdown", "/s", "/t", str(delay_segundos)])
    elif platform.system() == "Darwin":
        subprocess.Popen(
            ["sudo", "shutdown", "-h", f"+{delay_segundos // 60}"]
        )
    else:
        subprocess.Popen(["shutdown", f"+{delay_segundos // 60}"])


def cancelar_apagado() -> None:
    if platform.system() == "Windows":
        subprocess.Popen(["shutdown", "/a"])
    else:
        subprocess.Popen(["sudo", "shutdown", "-c"])
