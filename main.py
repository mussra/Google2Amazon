"""
Punto de entrada de la aplicación.

Uso:
    python main.py

Requiere las dependencias listadas en requirements.txt:
    pip install -r requirements.txt
"""
from __future__ import annotations

import logging
import multiprocessing
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
    # freeze_support() es necesario cuando la app se empaqueta con PyInstaller
    # en Windows y se usa ProcessPoolExecutor (start method = spawn).
    # En desarrollo no hace nada.
    multiprocessing.freeze_support()
    configurar_logging()
    app_root = ctk.CTk()
    app_root.geometry("1020x680")
    CopyFilesApp(app_root)
    app_root.mainloop()


if __name__ == "__main__":
    main()
