"""
Punto de entrada de la aplicación.

Uso:
    python main.py

Requiere las dependencias listadas en requirements.txt:
    pip install -r requirements.txt
"""
from __future__ import annotations

import logging
import sys

import customtkinter as ctk

from copyfiles_app.gui import CopyFilesApp


def configurar_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> None:
    configurar_logging()
    app_root = ctk.CTk()
    app_root.geometry("1020x680")
    CopyFilesApp(app_root)
    app_root.mainloop()


if __name__ == "__main__":
    main()
