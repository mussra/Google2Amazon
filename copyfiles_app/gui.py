"""
Interfaz gráfica de la Suite Organizadora.

Esta capa SOLO se encarga de la presentación y de traducir eventos de
usuario en llamadas a los motores (`SyncEngine`, `DuplicateEngine`).
Toda la lógica de negocio vive fuera de este módulo, lo que permite
testear los motores sin necesidad de levantar Tk y facilita mantener
o sustituir la interfaz en el futuro.
"""
from __future__ import annotations

import logging
import os
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from .constants import PALETAS_PREDEFINIDAS, DEFAULT_RENAME_TOKENS
from .duplicate_engine import DuplicateEngine, DuplicateOptions, _compute_phash
from .localization import Translator
from .models import AppConfig
from .persistence import HashCache, HistoryManager
from .sessions import SessionManager, Session, DiskRef, get_volume_id
from .mirror_engine import MirrorEngine, MirrorOptions, MirrorDiff
from .regex_wizard import RegexWizard
from .sync_engine import SyncEngine

logger = logging.getLogger(__name__)

_LANG_LABELS = {"Español": "es", "English": "en", "es": "Español", "en": "English"}


class CopyFilesApp:
    """Ventana principal de la aplicación."""

    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.resizable(True, True)

        self.config = AppConfig.load()
        self.history = HistoryManager()
        self.hash_cache = HashCache()

        self._aplicar_paleta(self.config.modo_apariencia)

        self._sync_engines: list[SyncEngine] = []
        self.dup_engine = DuplicateEngine(hash_cache=self.hash_cache)
        self.duplicados_detectados: dict[str, list] = {}
        self.contador_archivos = 0
        self.bytes_processed = 0
        self.duplicados_en_progreso = False
        self.progreso_hilos: dict[str, str] = {}
        self.session_manager = SessionManager()
        self.mirror_engine = MirrorEngine(hash_cache=self.hash_cache)
        self._mirror_diffs: list[MirrorDiff] = []
        # Progreso real: total de bytes a copiar (calculado en hilo de fondo)
        self._progress_total_bytes: int = 0
        self._progress_done_bytes: int = 0
        self._progress_lock = threading.Lock()

        self.t = Translator(self.config.idioma)

        self._crear_variables_tk()
        self._construir_interfaz()

        self.txt_origen.insert(0, self.config.origen)
        self._restaurar_destinos()

        self._actualizar_idioma_ui()
        self._precargar_checkpoint_sincro()
        self._inyectar_estilos_treeview()

        self.color_acento_var.trace_add("write", lambda *_: self._actualizar_estilo_personalizado())
        self.color_marcos_var.trace_add("write", lambda *_: self._actualizar_estilo_personalizado())

        self.root.protocol("WM_DELETE_WINDOW", self._al_cerrar)

    # ------------------------------------------------------------------
    # Inicialización de estado
    # ------------------------------------------------------------------
    def _crear_variables_tk(self) -> None:
        c = self.config
        self.chk_fotos_var = tk.BooleanVar(value=c.chk_fotos)
        self.chk_videos_var = tk.BooleanVar(value=c.chk_videos)
        self.chk_docs_var = tk.BooleanVar(value=c.chk_docs)
        self.chk_otros_var = tk.BooleanVar(value=c.chk_otros)
        self.radio_accion_var = tk.StringVar(value="mover" if c.modo_mover else "copiar")

        self.chk_dup_nombre_var = tk.BooleanVar(value=False)
        self.chk_dup_tamano_var = tk.BooleanVar(value=True)

        self.dry_run_var = tk.BooleanVar(value=c.dry_run)
        self.copia_atomica_var = tk.BooleanVar(value=c.copia_atomica)
        self.colision_var = tk.StringVar(value=c.colision)
        self.regex_excluir_var = tk.StringVar(value=c.regex_excluir)
        self.txt_size_min = tk.StringVar(value=c.filtro_tamano_min)
        self.txt_size_max = tk.StringVar(value=c.filtro_tamano_max)

        self.patron_renombrado_var = tk.StringVar(value=c.patron_renombrado)
        self.ren_regex_busca_var = tk.StringVar(value=c.ren_regex_busca)
        self.ren_regex_reemplaza_var = tk.StringVar(value=c.ren_regex_reemplaza)
        self.metodo_borrado_var = tk.StringVar(value=c.accion_duplicados)
        self.modo_similitud_var = tk.StringVar(value=c.modo_similitud_dup)
        self.umbral_similitud_var = tk.IntVar(value=c.umbral_similitud_dup)

        self.modo_apariencia_var = tk.StringVar(value=c.modo_apariencia)
        self.color_acento_var = tk.StringVar(value=c.color_acento_personalizado)
        self.color_marcos_var = tk.StringVar(value=c.color_fondo_paneles)

        self.idioma_combo_var = tk.StringVar(value=_LANG_LABELS[c.idioma])
        self.lbl_preview_var = tk.StringVar()

    # ------------------------------------------------------------------
    # Tema visual
    # ------------------------------------------------------------------
    def _aplicar_paleta(self, modo: str) -> None:
        paleta = PALETAS_PREDEFINIDAS.get(modo, PALETAS_PREDEFINIDAS["System"])
        if modo in ("System", "Dark"):
            ctk.set_appearance_mode(modo)
        else:
            ctk.set_appearance_mode("Light" if paleta["es_claro"] else "Dark")
        self._aplicar_colores(paleta["acento"], paleta["marco"])

    @staticmethod
    def _aplicar_colores(acento: str, marco: str) -> None:
        theme = ctk.ThemeManager.theme
        theme["CTkButton"]["fg_color"] = [acento, acento]
        theme["CTkButton"]["hover_color"] = [acento, acento]
        theme["CTkFrame"]["fg_color"] = [marco, marco]
        theme["CTkProgressBar"]["progress_color"] = [acento, acento]
        theme["CTkSlider"]["progress_color"] = [acento, acento]
        theme["CTkCheckBox"]["fg_color"] = [acento, acento]
        theme["CTkRadioButton"]["fg_color"] = [acento, acento]

    def _inyectar_estilos_treeview(self) -> None:
        modo = self.modo_apariencia_var.get()
        es_claro = PALETAS_PREDEFINIDAS.get(modo, {"es_claro": False})["es_claro"]
        bg_tree = "#ffffff" if es_claro else "#1a1a1a"
        fg_tree = "#111111" if es_claro else "#ffffff"
        bg_hdr = "#e1e1e1" if es_claro else "#2d2d2d"
        fg_hdr = "#000000" if es_claro else "#ffffff"

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=bg_tree, foreground=fg_tree,
                         fieldbackground=bg_tree, rowheight=26, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background=bg_hdr, foreground=fg_hdr,
                         font=("Segoe UI", 10, "bold"), borderwidth=0)
        style.map("Treeview", background=[("selected", self.color_acento_var.get())],
                  foreground=[("selected", "#ffffff")])

    def _actualizar_estilo_personalizado(self) -> None:
        acento, marco = self.color_acento_var.get(), self.color_marcos_var.get()
        if len(acento) == 7 and acento.startswith("#") and len(marco) == 7 and marco.startswith("#"):
            self._aplicar_colores(acento, marco)
            self._inyectar_estilos_treeview()

    # ------------------------------------------------------------------
    # Construcción de la interfaz
    # ------------------------------------------------------------------
    def _construir_interfaz(self) -> None:
        self.notebook = ctk.CTkTabview(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=5)

        self.tab_sincro = self.notebook.add("tab_sincro")
        self.tab_dup = self.notebook.add("tab_duplicados")
        self.tab_espejo = self.notebook.add("tab_espejo")
        self.tab_avanzado = self.notebook.add("tab_avanzado")

        self._construir_tab_sincronizacion()
        self._construir_tab_duplicados()
        self._construir_tab_espejo()
        self._construir_tab_avanzado()

    def _construir_tab_sincronizacion(self) -> None:
        frame_rutas = ctk.CTkFrame(self.tab_sincro)
        frame_rutas.pack(fill="x", padx=15, pady=10)

        self.lbl_origen = ctk.CTkLabel(frame_rutas, text=self.t("lbl_origen"))
        self.lbl_origen.grid(row=0, column=0, padx=10, pady=5, sticky="w")
        self.txt_origen = ctk.CTkEntry(frame_rutas, width=500)
        self.txt_origen.grid(row=0, column=1, padx=10, pady=5)
        self.btn_buscar_orig = ctk.CTkButton(frame_rutas, text=self.t("btn_buscar"),
                                              command=self._examinar_origen, width=100)
        self.btn_buscar_orig.grid(row=0, column=2, padx=10, pady=5)

        # ── Destinos múltiples ────────────────────────────────────────────
        lbl_dest = ctk.CTkLabel(frame_rutas, text=self.t("lbl_destino"))
        lbl_dest.grid(row=1, column=0, padx=10, pady=(5, 2), sticky="nw")

        f_dest_list = ctk.CTkFrame(frame_rutas, fg_color="transparent")
        f_dest_list.grid(row=1, column=1, padx=10, pady=(5, 2), sticky="ew")
        frame_rutas.columnconfigure(1, weight=1)

        self.lista_destinos = ttk.Treeview(f_dest_list, columns=("Ruta",), show="headings", height=3)
        self.lista_destinos.heading("Ruta", text="Carpetas destino (copias paralelas)")
        self.lista_destinos.column("Ruta", anchor="w")
        self.lista_destinos.pack(fill="x", expand=True)

        f_dest_btns = ctk.CTkFrame(frame_rutas, fg_color="transparent")
        f_dest_btns.grid(row=1, column=2, padx=10, pady=(5, 2), sticky="n")
        ctk.CTkButton(f_dest_btns, text="➕ Añadir", width=100,
                      command=self._agregar_destino).pack(pady=2)
        ctk.CTkButton(f_dest_btns, text="➖ Quitar", width=100,
                      fg_color="#b71c1c", hover_color="#c62828",
                      command=self._quitar_destino).pack(pady=2)

        frame_tipos = ctk.CTkFrame(self.tab_sincro)
        frame_tipos.pack(fill="x", padx=15, pady=5)
        self.chk_fotos = ctk.CTkCheckBox(frame_tipos, text=self.t("chk_fotos"), variable=self.chk_fotos_var,
                                          command=self._guardar_config_live)
        self.chk_fotos.pack(side="left", padx=15, pady=10)
        self.chk_videos = ctk.CTkCheckBox(frame_tipos, text=self.t("chk_videos"), variable=self.chk_videos_var,
                                           command=self._guardar_config_live)
        self.chk_videos.pack(side="left", padx=15, pady=10)
        self.chk_docs = ctk.CTkCheckBox(frame_tipos, text=self.t("chk_docs"), variable=self.chk_docs_var,
                                         command=self._guardar_config_live)
        self.chk_docs.pack(side="left", padx=15, pady=10)
        self.chk_otros = ctk.CTkCheckBox(frame_tipos, text=self.t("chk_otros"), variable=self.chk_otros_var,
                                          command=self._guardar_config_live)
        self.chk_otros.pack(side="left", padx=15, pady=10)

        frame_radio = ctk.CTkFrame(self.tab_sincro)
        frame_radio.pack(fill="x", padx=15, pady=5)
        self.rad_copiar = ctk.CTkRadioButton(frame_radio, text=self.t("rad_copiar"),
                                              variable=self.radio_accion_var, value="copiar",
                                              command=self._guardar_config_live)
        self.rad_copiar.pack(side="left", padx=20, pady=10)
        self.rad_mover = ctk.CTkRadioButton(frame_radio, text=self.t("rad_mover"),
                                             variable=self.radio_accion_var, value="mover",
                                             command=self._guardar_config_live)
        self.rad_mover.pack(side="left", padx=20, pady=10)

        frame_controles = ctk.CTkFrame(self.tab_sincro)
        frame_controles.pack(fill="x", padx=15, pady=10)
        self.btn_lanzar = ctk.CTkButton(frame_controles, text=self.t("btn_lanzar"), fg_color="#1b5e20",
                                         hover_color="#2e7d32", command=self._alternar_sincronizacion, width=180)
        self.btn_lanzar.pack(side="left", padx=15, pady=10)
        self.lbl_estado = ctk.CTkLabel(frame_controles, text=self.t("lbl_estado_off"), font=("Segoe UI", 12, "bold"))
        self.lbl_estado.pack(side="left", padx=20)

        # ── Panel de sesiones ─────────────────────────────────────────────
        f_ses = ctk.CTkFrame(self.tab_sincro)
        f_ses.pack(fill="x", padx=15, pady=(0, 6))

        ctk.CTkLabel(f_ses, text="💾 Sesiones guardadas:", font=("Segoe UI", 10, "bold")).pack(side="left", padx=(10, 4))
        self.combo_sesiones = ctk.CTkComboBox(f_ses, values=["— Nueva sesión —"], width=260,
                                               command=self._on_sesion_seleccionada)
        self.combo_sesiones.pack(side="left", padx=4)
        self.combo_sesiones.set("— Nueva sesión —")
        ctk.CTkButton(f_ses, text="💾 Guardar", width=90,
                      command=self._guardar_sesion_ui).pack(side="left", padx=4)
        ctk.CTkButton(f_ses, text="📂 Cargar", width=90,
                      command=self._cargar_sesion_ui).pack(side="left", padx=4)
        ctk.CTkButton(f_ses, text="🗑️ Borrar", width=90,
                      fg_color="#b71c1c", hover_color="#7f0000",
                      command=self._borrar_sesion_ui).pack(side="left", padx=4)
        self.lbl_ses_vol = ctk.CTkLabel(f_ses, text="", font=("Consolas", 9), text_color="#888888")
        self.lbl_ses_vol.pack(side="left", padx=10)
        self._refrescar_combo_sesiones()

        self.progress_bar = ctk.CTkProgressBar(self.tab_sincro)
        self.progress_bar.pack(fill="x", padx=15, pady=5)
        self.progress_bar.set(0)

        self.txt_log = ctk.CTkTextbox(self.tab_sincro, height=220, font=("Consolas", 11))
        self.txt_log.pack(fill="both", expand=True, padx=15, pady=10)

    def _construir_tab_duplicados(self) -> None:
        frame_top = ctk.CTkFrame(self.tab_dup)
        frame_top.pack(fill="x", padx=15, pady=10)
        ctk.CTkLabel(frame_top, text="Árboles de Directorios Raíz para Análisis Estructural:").pack(
            anchor="w", padx=10, pady=2)

        frame_lista_acc = ctk.CTkFrame(frame_top, fg_color="transparent")
        frame_lista_acc.pack(fill="x", padx=5, pady=2)

        self.lista_carpetas_dup = ttk.Treeview(frame_lista_acc, columns=("Ruta",), show="headings", height=3)
        self.lista_carpetas_dup.heading("Ruta", text="Rutas Configuradas para Indexación Recursiva")
        self.lista_carpetas_dup.pack(side="left", fill="x", expand=True, padx=5)

        f_botones_sidebar = ctk.CTkFrame(frame_lista_acc, fg_color="transparent")
        f_botones_sidebar.pack(side="right", padx=5)
        self.btn_add_ruta = ctk.CTkButton(f_botones_sidebar, text=self.t("btn_add_ruta"), width=90,
                                           command=self._agregar_ruta_dup)
        self.btn_add_ruta.pack(pady=2)
        self.btn_del_ruta = ctk.CTkButton(f_botones_sidebar, text=self.t("btn_del_ruta"), width=90,
                                           fg_color="#b71c1c", hover_color="#c62828",
                                           command=self._quitar_ruta_dup)
        self.btn_del_ruta.pack(pady=2)

        # ── Fila 1: filtros de contenido + similitud ──────────────────────
        f_fila1 = ctk.CTkFrame(frame_top, fg_color="transparent")
        f_fila1.pack(fill="x", padx=5, pady=(5, 2))

        self.chk_dup_tamano = ctk.CTkCheckBox(f_fila1, text=self.t("chk_dup_tamano"),
                                               variable=self.chk_dup_tamano_var)
        self.chk_dup_tamano.pack(side="left", padx=(10, 6))
        self.chk_dup_nombre = ctk.CTkCheckBox(f_fila1, text=self.t("chk_dup_nombre"),
                                               variable=self.chk_dup_nombre_var)
        self.chk_dup_nombre.pack(side="left", padx=6)

        ctk.CTkLabel(f_fila1, text="Modo imágenes:").pack(side="left", padx=(14, 2))
        self.combo_similitud = ctk.CTkComboBox(
            f_fila1,
            values=["Coincidencia Exacta", "Imágenes Similares (phash)"],
            width=210,
            command=self._on_modo_similitud_change,
        )
        self.combo_similitud.pack(side="left", padx=2)
        self.combo_similitud.set(
            "Imágenes Similares (phash)"
            if self.modo_similitud_var.get() == "similar"
            else "Coincidencia Exacta"
        )
        ctk.CTkLabel(f_fila1, text="Umbral:").pack(side="left", padx=(8, 2))
        self.slider_umbral = ctk.CTkSlider(
            f_fila1, from_=1, to=30, number_of_steps=29,
            variable=self.umbral_similitud_var, width=90,
        )
        self.slider_umbral.pack(side="left", padx=2)
        self.lbl_umbral_val = ctk.CTkLabel(f_fila1, text="10", width=26)
        self.lbl_umbral_val.pack(side="left")
        self.umbral_similitud_var.trace_add(
            "write",
            lambda *_: self.lbl_umbral_val.configure(text=str(self.umbral_similitud_var.get()))
        )

        # ── Fila 2: hilos + botón analizar (siempre visible) ─────────────
        f_fila2 = ctk.CTkFrame(frame_top, fg_color="transparent")
        f_fila2.pack(fill="x", padx=5, pady=(2, 6))

        ctk.CTkLabel(f_fila2, text="Hilos asignados:").pack(side="left", padx=(10, 4))
        self.combo_hilos = ctk.CTkComboBox(f_fila2, values=["1", "2", "4", "8", "16"], width=72)
        self.combo_hilos.pack(side="left", padx=(0, 10))
        self.combo_hilos.set("4")

        self.btn_analizar_dup = ctk.CTkButton(
            f_fila2, text=self.t("btn_analizar_dup"),
            fg_color="#0052cc", hover_color="#0043a4",
            command=self._click_analizar_duplicados, width=200,
        )
        self.btn_analizar_dup.pack(side="left", padx=4)

        ctk.CTkButton(
            f_fila2, text="🏷️ Reglas de marcado", width=160,
            fg_color="#37474f", hover_color="#455a64",
            command=self._abrir_wizard_marcado,
        ).pack(side="left", padx=8)

        f_perf = ctk.CTkFrame(self.tab_dup)
        f_perf.pack(fill="x", padx=15, pady=5)
        self.txt_consola_perf = ctk.CTkTextbox(f_perf, height=80, font=("Consolas", 10),
                                                fg_color="#0b0f19", text_color="#00ff66")
        self.txt_consola_perf.pack(fill="both", expand=True, padx=5, pady=5)
        self._actualizar_consola("=== MONITOR ASÍNCRONO DE CONCURRENCIA ===\nListo para mapear unidades físicas...")

        f_arbol = ctk.CTkFrame(self.tab_dup)
        f_arbol.pack(fill="both", expand=True, padx=15, pady=5)

        self.tree_dup = ttk.Treeview(f_arbol, columns=("Ruta", "Tamaño", "Acción"), show="tree headings")
        self.tree_dup.heading("#0", text=self.t("tree_hdr_grupo"))
        self.tree_dup.heading("Ruta", text=self.t("tree_hdr_ruta"))
        self.tree_dup.heading("Tamaño", text=self.t("tree_hdr_tamano"))
        self.tree_dup.heading("Acción", text=self.t("tree_hdr_accion"))
        self.tree_dup.column("#0", width=220, anchor="w")
        self.tree_dup.column("Ruta", width=420, anchor="w")
        self.tree_dup.column("Tamaño", width=100, anchor="e")
        self.tree_dup.column("Acción", width=140, anchor="center")
        self.tree_dup.pack(side="left", fill="both", expand=True)

        scr_d = ttk.Scrollbar(f_arbol, orient="vertical", command=self.tree_dup.yview)
        self.tree_dup.configure(yscrollcommand=scr_d.set)
        scr_d.pack(side="right", fill="y")
        self.tree_dup.bind("<Button-1>", self._single_click_dup_tree)
        self.tree_dup.bind("<Double-1>", self._dbl_click_dup_tree)
        self.tree_dup.bind("<Button-3>", self._ctx_menu_dup_tree)

        f_status_dup = ctk.CTkFrame(self.tab_dup)
        f_status_dup.pack(fill="x", padx=15, pady=5)
        self.lbl_status_dup = ctk.CTkLabel(f_status_dup, text=self.t("dup_init_status"),
                                            font=("Segoe UI", 11, "bold"), text_color="#1f6aa5")
        self.lbl_status_dup.pack(side="left", padx=10)
        self.lbl_saving_dup = ctk.CTkLabel(f_status_dup, text=self.t("disk_saving_init"),
                                            font=("Segoe UI", 11, "italic"), text_color="#2e7d32")
        self.lbl_saving_dup.pack(side="right", padx=10)

        f_footer_dup = ctk.CTkFrame(self.tab_dup)
        f_footer_dup.pack(fill="x", padx=15, pady=10)
        self.lbl_metrics_dup = ctk.CTkLabel(f_footer_dup, text=self.t("metrics_init"), font=("Consolas", 11))
        self.lbl_metrics_dup.pack(side="left", padx=10)
        self.lbl_stats_tipos = ctk.CTkLabel(f_footer_dup, text="", font=("Consolas", 10), text_color="#888888")
        self.lbl_stats_tipos.pack(side="left", padx=10)
        self.btn_exportar_csv = ctk.CTkButton(
            f_footer_dup, text="📄 Exportar CSV", fg_color="#37474f", hover_color="#455a64",
            state="disabled", command=self._exportar_csv_duplicados, width=140,
        )
        self.btn_exportar_csv.pack(side="right", padx=5)
        self.btn_ejecutar_dup = ctk.CTkButton(f_footer_dup, text=self.t("btn_ejecutar_acc"), fg_color="#b71c1c",
                                               hover_color="#c62828", state="disabled",
                                               command=self._ejecutar_limpieza_duplicados, width=220)
        self.btn_ejecutar_dup.pack(side="right", padx=10)

    def _construir_tab_espejo(self) -> None:
        """Tab de sincronización bidireccional (espejo bajo demanda)."""
        # ── Directorios espejo ────────────────────────────────────────
        f_top = ctk.CTkFrame(self.tab_espejo)
        f_top.pack(fill="x", padx=15, pady=10)
        ctk.CTkLabel(f_top, text="📂 Directorios a mantener como espejo:",
                     font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=10, pady=4)

        f_lista = ctk.CTkFrame(f_top, fg_color="transparent")
        f_lista.pack(fill="x", padx=8, pady=4)
        self.lista_espejo = tk.ttk.Treeview(f_lista, columns=("Vol",), show="headings", height=4)
        self.lista_espejo.heading("Vol", text="Directorio (se añaden todos los que quieras espejar)")
        self.lista_espejo.column("Vol", anchor="w")
        self.lista_espejo.pack(side="left", fill="x", expand=True)

        f_esp_btns = ctk.CTkFrame(f_top, fg_color="transparent")
        f_esp_btns.pack(side="right", padx=8, pady=4)
        ctk.CTkButton(f_esp_btns, text="➕ Añadir dir.", width=110,
                      command=self._agregar_dir_espejo).pack(pady=2)
        ctk.CTkButton(f_esp_btns, text="➖ Quitar", width=110,
                      fg_color="#b71c1c", hover_color="#c62828",
                      command=self._quitar_dir_espejo).pack(pady=2)

        # ── Opciones ──────────────────────────────────────────────────
        f_opts = ctk.CTkFrame(self.tab_espejo, fg_color="transparent")
        f_opts.pack(fill="x", padx=15, pady=4)
        self.mir_fotos_var = tk.BooleanVar(value=True)
        self.mir_videos_var = tk.BooleanVar(value=True)
        self.mir_docs_var = tk.BooleanVar(value=True)
        self.mir_otros_var = tk.BooleanVar(value=True)
        self.mir_hash_var = tk.BooleanVar(value=False)
        self.mir_contenido_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(f_opts, text="📷 Fotos", variable=self.mir_fotos_var).pack(side="left", padx=8)
        ctk.CTkCheckBox(f_opts, text="🎥 Vídeos", variable=self.mir_videos_var).pack(side="left", padx=8)
        ctk.CTkCheckBox(f_opts, text="📄 Docs", variable=self.mir_docs_var).pack(side="left", padx=8)
        ctk.CTkCheckBox(f_opts, text="📦 Otros", variable=self.mir_otros_var).pack(side="left", padx=8)
        ctk.CTkCheckBox(f_opts, text="🔑 Verificar integridad (mismo nombre, distinto contenido)",
                        variable=self.mir_hash_var).pack(side="left", padx=12)
        ctk.CTkCheckBox(
            f_opts,
            text="🔍 Comparar por contenido (ignora nombre — detecta fotos renombradas)",
            variable=self.mir_contenido_var,
        ).pack(side="left", padx=12)

        # ── Botones de acción ─────────────────────────────────────────
        f_acc = ctk.CTkFrame(self.tab_espejo, fg_color="transparent")
        f_acc.pack(fill="x", padx=15, pady=6)
        self.btn_analizar_espejo = ctk.CTkButton(
            f_acc, text="🔍 Analizar diferencias", width=200,
            fg_color="#0052cc", hover_color="#0043a4",
            command=self._analizar_espejo)
        self.btn_analizar_espejo.pack(side="left", padx=6)
        self.btn_sincronizar_espejo = ctk.CTkButton(
            f_acc, text="⚡ Sincronizar ahora", width=200,
            fg_color="#1b5e20", hover_color="#2e7d32",
            state="disabled", command=self._sincronizar_espejo)
        self.btn_sincronizar_espejo.pack(side="left", padx=6)
        self.btn_cancelar_espejo = ctk.CTkButton(
            f_acc, text="⏹ Cancelar", width=120,
            fg_color="#b71c1c", hover_color="#7f0000",
            state="disabled", command=self._cancelar_espejo)
        self.btn_cancelar_espejo.pack(side="left", padx=6)

        self.lbl_espejo_status = ctk.CTkLabel(
            f_acc, text="Añade directorios y pulsa Analizar.", font=("Segoe UI", 10))
        self.lbl_espejo_status.pack(side="left", padx=12)

        # ── Progress bar ──────────────────────────────────────────────
        self.progress_espejo = ctk.CTkProgressBar(self.tab_espejo)
        self.progress_espejo.pack(fill="x", padx=15, pady=4)
        self.progress_espejo.set(0)

        # ── Árbol de resultados ───────────────────────────────────────
        f_arbol = ctk.CTkFrame(self.tab_espejo)
        f_arbol.pack(fill="both", expand=True, padx=15, pady=6)
        self.tree_espejo = tk.ttk.Treeview(
            f_arbol, columns=("Origen", "Destino", "Tamaño", "Estado"), show="tree headings")
        self.tree_espejo.heading("#0", text="Archivo")
        self.tree_espejo.heading("Origen", text="Directorio origen")
        self.tree_espejo.heading("Destino", text="Directorio destino")
        self.tree_espejo.heading("Tamaño", text="Tamaño")
        self.tree_espejo.heading("Estado", text="Estado")
        self.tree_espejo.column("#0", width=260, anchor="w")
        self.tree_espejo.column("Origen", width=180, anchor="w")
        self.tree_espejo.column("Destino", width=180, anchor="w")
        self.tree_espejo.column("Tamaño", width=90, anchor="e")
        self.tree_espejo.column("Estado", width=100, anchor="center")
        self.tree_espejo.tag_configure("pendiente", foreground="#ff8f00")
        self.tree_espejo.tag_configure("ok", foreground="#2e7d32")
        self.tree_espejo.tag_configure("error", foreground="#b71c1c")
        scr_e = tk.ttk.Scrollbar(f_arbol, orient="vertical", command=self.tree_espejo.yview)
        self.tree_espejo.configure(yscrollcommand=scr_e.set)
        self.tree_espejo.pack(side="left", fill="both", expand=True)
        scr_e.pack(side="right", fill="y")
        self.tree_espejo.bind("<Button-3>", self._ctx_espejo)

        # ── Log ───────────────────────────────────────────────────────
        self.txt_log_espejo = ctk.CTkTextbox(self.tab_espejo, height=100, font=("Consolas", 10))
        self.txt_log_espejo.pack(fill="x", padx=15, pady=(0, 8))

    def _construir_tab_avanzado(self) -> None:
        self.scrollable_frame_avanzado = ctk.CTkScrollableFrame(self.tab_avanzado)
        self.scrollable_frame_avanzado.pack(fill="both", expand=True, padx=5, pady=5)

        f_comunes = ctk.CTkFrame(self.scrollable_frame_avanzado)
        f_comunes.pack(fill="x", padx=15, pady=5)
        self.chk_dry = ctk.CTkCheckBox(f_comunes, text=self.t("chk_dry"), variable=self.dry_run_var,
                                        command=self._guardar_config_live)
        self.chk_dry.grid(row=0, column=0, padx=15, pady=8, sticky="w")
        self.chk_atomic = ctk.CTkCheckBox(f_comunes, text=self.t("chk_atomic"), variable=self.copia_atomica_var,
                                           command=self._guardar_config_live)
        self.chk_atomic.grid(row=0, column=1, padx=15, pady=8, sticky="w")

        self.lbl_ex_rx = ctk.CTkLabel(f_comunes, text=self.t("lbl_ex_rx"))
        self.lbl_ex_rx.grid(row=1, column=0, padx=15, pady=5, sticky="w")
        entry_ex_rx = ctk.CTkEntry(f_comunes, textvariable=self.regex_excluir_var, width=320)
        entry_ex_rx.grid(row=1, column=1, padx=15, pady=5, sticky="w")
        entry_ex_rx.bind("<FocusOut>", lambda e: self._guardar_config_live())

        f_colisiones = ctk.CTkFrame(self.scrollable_frame_avanzado)
        f_colisiones.pack(fill="x", padx=15, pady=5)
        self.tit_colisiones = ctk.CTkLabel(f_colisiones, text=self.t("tit_colisiones"), font=("Segoe UI", 11, "bold"))
        self.tit_colisiones.pack(anchor="w", padx=15, pady=5)
        self.rad_col_ren = ctk.CTkRadioButton(f_colisiones, text=self.t("rad_col_ren"), variable=self.colision_var,
                                               value="renombrar", command=self._guardar_config_live)
        self.rad_col_ren.pack(anchor="w", padx=30, pady=3)
        self.rad_col_omit = ctk.CTkRadioButton(f_colisiones, text=self.t("rad_col_omit"), variable=self.colision_var,
                                                value="omitir", command=self._guardar_config_live)
        self.rad_col_omit.pack(anchor="w", padx=30, pady=3)

        f_rangos = ctk.CTkFrame(self.scrollable_frame_avanzado)
        f_rangos.pack(fill="x", padx=15, pady=5)
        self.tit_rangos = ctk.CTkLabel(f_rangos, text=self.t("tit_rangos"), font=("Segoe UI", 11, "bold"))
        self.tit_rangos.grid(row=0, column=0, columnspan=4, sticky="w", padx=15, pady=5)
        self.lbl_tam_min = ctk.CTkLabel(f_rangos, text=self.t("lbl_tam_min"))
        self.lbl_tam_min.grid(row=1, column=0, padx=15, pady=5, sticky="w")
        ent_min = ctk.CTkEntry(f_rangos, textvariable=self.txt_size_min, width=100)
        ent_min.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        ent_min.bind("<FocusOut>", lambda e: self._guardar_config_live())

        self.lbl_tam_max = ctk.CTkLabel(f_rangos, text=self.t("lbl_tam_max"))
        self.lbl_tam_max.grid(row=1, column=2, padx=15, pady=5, sticky="w")
        ent_max = ctk.CTkEntry(f_rangos, textvariable=self.txt_size_max, width=100)
        ent_max.grid(row=1, column=3, padx=5, pady=5, sticky="w")
        ent_max.bind("<FocusOut>", lambda e: self._guardar_config_live())

        f_wizard = ctk.CTkFrame(self.scrollable_frame_avanzado)
        f_wizard.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_wizard, text="▼ ENMASCARAMIENTO Y SINTAXIS DINÁMICA DE SALIDA",
                     font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=15, pady=5)
        self.wizard_formula = ctk.CTkLabel(f_wizard, text=self.t("wizard_formula"))
        self.wizard_formula.grid(row=1, column=0, padx=15, pady=5, sticky="w")
        ent_p_ren = ctk.CTkEntry(f_wizard, textvariable=self.patron_renombrado_var, width=420)
        ent_p_ren.grid(row=1, column=1, padx=15, pady=5, sticky="w")
        ent_p_ren.bind("<KeyRelease>", lambda e: self._evaluar_preview_wizard())

        self.wizard_lbl_token = ctk.CTkLabel(f_wizard, text=self.t("wizard_lbl_token"))
        self.wizard_lbl_token.grid(row=2, column=0, padx=15, pady=5, sticky="w")
        f_tokens = ctk.CTkFrame(f_wizard, fg_color="transparent")
        f_tokens.grid(row=2, column=1, padx=15, pady=5, sticky="w")
        for tk_s in DEFAULT_RENAME_TOKENS:
            ctk.CTkButton(f_tokens, text=tk_s, width=60, height=22, font=("Consolas", 10),
                          command=lambda t=tk_s: self._inyectar_token_wizard(t)).pack(side="left", padx=2)

        ctk.CTkLabel(
            f_wizard,
            text="💡 {ruta_relativa} replica la estructura completa desde el origen. "
                 "Ej: origen=folder1 → destino/folder1/folder2/imagen.jpg",
            font=("Segoe UI", 9), text_color="#888888",
        ).grid(row=3, column=0, columnspan=2, padx=15, pady=(0, 2), sticky="w")

        self.wizard_regex_clean = ctk.CTkLabel(f_wizard, text=self.t("wizard_regex_clean"))
        self.wizard_regex_clean.grid(row=4, column=0, padx=15, pady=5, sticky="w")
        f_rx_busca = ctk.CTkFrame(f_wizard, fg_color="transparent")
        f_rx_busca.grid(row=4, column=1, padx=15, pady=5, sticky="w")
        ent_w_b = ctk.CTkEntry(f_rx_busca, textvariable=self.ren_regex_busca_var, width=180)
        ent_w_b.pack(side="left")
        ent_w_b.bind("<KeyRelease>", lambda e: self._evaluar_preview_wizard())
        ctk.CTkButton(f_rx_busca, text="🔧 Wizard", width=80, height=26,
                      command=lambda: self._abrir_regex_wizard_renombrado()).pack(side="left", padx=6)

        self.wizard_regex_repl = ctk.CTkLabel(f_wizard, text=self.t("wizard_regex_repl"))
        self.wizard_regex_repl.grid(row=5, column=0, padx=15, pady=5, sticky="w")
        ent_w_r = ctk.CTkEntry(f_wizard, textvariable=self.ren_regex_reemplaza_var, width=180)
        ent_w_r.grid(row=5, column=1, padx=15, pady=5, sticky="w")
        ent_w_r.bind("<KeyRelease>", lambda e: self._evaluar_preview_wizard())

        self.wizard_preview = ctk.CTkLabel(f_wizard, text=self.t("wizard_preview"), font=("Segoe UI", 10, "bold"))
        self.wizard_preview.grid(row=6, column=0, padx=15, pady=5, sticky="w")
        ctk.CTkLabel(f_wizard, textvariable=self.lbl_preview_var, font=("Consolas", 11, "italic"),
                     text_color="#0d6efd").grid(row=6, column=1, padx=15, pady=5, sticky="w")

        f_limpieza_dup = ctk.CTkFrame(self.scrollable_frame_avanzado)
        f_limpieza_dup.pack(fill="x", padx=15, pady=5)
        self.lbl_metodo_borrado = ctk.CTkLabel(f_limpieza_dup, text=self.t("lbl_metodo_borrado"))
        self.lbl_metodo_borrado.pack(side="left", padx=15, pady=10)
        ctk.CTkComboBox(f_limpieza_dup, values=["Enviar a la Papelera (Seguro)", "Eliminación Destructiva Permanente"],
                         variable=self.metodo_borrado_var, width=280,
                         command=lambda e: self._guardar_config_live()).pack(side="left", padx=15, pady=10)

        f_apariencia = ctk.CTkFrame(self.scrollable_frame_avanzado)
        f_apariencia.pack(fill="x", padx=15, pady=5)
        self.tit_apariencia = ctk.CTkLabel(f_apariencia, text=self.t("tit_apariencia"), font=("Segoe UI", 11, "bold"))
        self.tit_apariencia.grid(row=0, column=0, columnspan=4, sticky="w", padx=15, pady=5)
        self.lbl_idioma_avanzado = ctk.CTkLabel(f_apariencia, text=self.t("lbl_idioma_avanzado"))
        self.lbl_idioma_avanzado.grid(row=1, column=0, padx=15, pady=5, sticky="w")
        ctk.CTkComboBox(f_apariencia, values=["Español", "English"], variable=self.idioma_combo_var,
                         command=self._cambiar_idioma).grid(row=1, column=1, padx=15, pady=5, sticky="w")

        self.lbl_modo_color = ctk.CTkLabel(f_apariencia, text=self.t("lbl_modo_color"))
        self.lbl_modo_color.grid(row=1, column=2, padx=15, pady=5, sticky="w")
        ctk.CTkComboBox(f_apariencia, values=list(PALETAS_PREDEFINIDAS.keys()), variable=self.modo_apariencia_var,
                         command=self._cambiar_paleta).grid(row=1, column=3, padx=15, pady=5, sticky="w")

        self.lbl_color_acento = ctk.CTkLabel(f_apariencia, text=self.t("lbl_color_acento"))
        self.lbl_color_acento.grid(row=2, column=0, padx=15, pady=5, sticky="w")
        ctk.CTkEntry(f_apariencia, textvariable=self.color_acento_var, width=120).grid(
            row=2, column=1, padx=15, pady=5, sticky="w")
        self.lbl_color_paneles = ctk.CTkLabel(f_apariencia, text=self.t("lbl_color_paneles"))
        self.lbl_color_paneles.grid(row=2, column=2, padx=15, pady=5, sticky="w")
        ctk.CTkEntry(f_apariencia, textvariable=self.color_marcos_var, width=120).grid(
            row=2, column=3, padx=15, pady=5, sticky="w")

        f_corporativo = ctk.CTkFrame(self.scrollable_frame_avanzado, fg_color="transparent")
        f_corporativo.pack(fill="x", padx=15, pady=15)
        self.btn_visitar_web = ctk.CTkButton(f_corporativo, text=self.t("btn_visitar_web"), fg_color="#0052cc",
                                              hover_color="#0043a4",
                                              command=lambda: webbrowser.open("https://github.com"), width=220)
        self.btn_visitar_web.pack(side="left", padx=15)
        ctk.CTkButton(f_corporativo, text="📋 Auditoría de Historial", fg_color="#455a64", hover_color="#37474f",
                      command=self._abrir_ventana_historico, width=220).pack(side="left", padx=5)
        self._evaluar_preview_wizard()

    # ------------------------------------------------------------------
    # Helpers de rutas / carpetas
    # ------------------------------------------------------------------
    def _examinar_origen(self) -> None:
        dir_s = filedialog.askdirectory()
        if dir_s:
            self.txt_origen.delete(0, "end")
            self.txt_origen.insert(0, dir_s)
            self._guardar_config_live()

    def _agregar_ruta_dup(self) -> None:
        dir_s = filedialog.askdirectory()
        if not dir_s:
            return
        for item in self.lista_carpetas_dup.get_children():
            if self.lista_carpetas_dup.item(item, "values")[0] == dir_s:
                return
        self.lista_carpetas_dup.insert("", "end", values=(dir_s,))

    def _quitar_ruta_dup(self) -> None:
        for item in self.lista_carpetas_dup.selection():
            self.lista_carpetas_dup.delete(item)

    def _inyectar_token_wizard(self, token: str) -> None:
        self.patron_renombrado_var.set(self.patron_renombrado_var.get() + token)
        self._guardar_config_live()
        self._evaluar_preview_wizard()

    # ------------------------------------------------------------------
    # Idioma / tema
    # ------------------------------------------------------------------
    def _cambiar_idioma(self, val: str) -> None:
        self.t.set_language(_LANG_LABELS[val])
        self._actualizar_idioma_ui()
        self._guardar_config_live()

    def _cambiar_paleta(self, nombre_modo: str) -> None:
        self._aplicar_paleta(nombre_modo)
        paleta = PALETAS_PREDEFINIDAS.get(nombre_modo, PALETAS_PREDEFINIDAS["System"])
        self.color_acento_var.set(paleta["acento"])
        self.color_marcos_var.set(paleta["marco"])
        self._inyectar_estilos_treeview()
        self._guardar_config_live()

    def _actualizar_idioma_ui(self) -> None:
        t = self.t
        self.root.title(t("titulo"))

        if hasattr(self.notebook, "_segmented_button"):
            botones = self.notebook._segmented_button._buttons_dict
            botones["tab_sincro"].configure(text=t("tab_sincro"))
            botones["tab_duplicados"].configure(text=t("tab_duplicados"))
            botones["tab_espejo"].configure(text=t("tab_espejo"))
            botones["tab_avanzado"].configure(text=t("tab_avanzado"))

        widget_keys = {
            "lbl_origen": "lbl_origen", "lbl_destino": "lbl_destino",
            "btn_buscar_orig": "btn_buscar",
            "chk_fotos": "chk_fotos", "chk_videos": "chk_videos", "chk_docs": "chk_docs", "chk_otros": "chk_otros",
            "rad_copiar": "rad_copiar", "rad_mover": "rad_mover",
            "chk_dry": "chk_dry", "chk_atomic": "chk_atomic", "lbl_ex_rx": "lbl_ex_rx",
            "tit_colisiones": "tit_colisiones", "rad_col_ren": "rad_col_ren", "rad_col_omit": "rad_col_omit",
            "tit_rangos": "tit_rangos", "lbl_tam_min": "lbl_tam_min", "lbl_tam_max": "lbl_tam_max",
            "wizard_formula": "wizard_formula", "wizard_lbl_token": "wizard_lbl_token",
            "wizard_regex_clean": "wizard_regex_clean", "wizard_regex_repl": "wizard_regex_repl",
            "wizard_preview": "wizard_preview", "lbl_metodo_borrado": "lbl_metodo_borrado",
            "lbl_idioma_avanzado": "lbl_idioma_avanzado", "lbl_modo_color": "lbl_modo_color",
            "lbl_color_acento": "lbl_color_acento", "lbl_color_paneles": "lbl_color_paneles",
            "btn_visitar_web": "btn_visitar_web", "btn_add_ruta": "btn_add_ruta", "btn_del_ruta": "btn_del_ruta",
            "chk_dup_tamano": "chk_dup_tamano", "chk_dup_nombre": "chk_dup_nombre",
        }
        for attr, clave in widget_keys.items():
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.configure(text=t(clave))

        self.tree_dup.heading("#0", text=t("tree_hdr_grupo"))
        self.tree_dup.heading("Ruta", text=t("tree_hdr_ruta"))
        self.tree_dup.heading("Tamaño", text=t("tree_hdr_tamano"))
        self.tree_dup.heading("Acción", text=t("tree_hdr_accion"))

        en_marcha = bool(self._sync_engines)
        self.btn_lanzar.configure(text=t("btn_detener") if en_marcha else t("btn_lanzar"))
        self.lbl_estado.configure(text=t("lbl_estado_on") if en_marcha else t("lbl_estado_off"))
        self._evaluar_preview_wizard()

    def _evaluar_preview_wizard(self) -> None:
        patron = self.patron_renombrado_var.get()
        busca = self.ren_regex_busca_var.get()
        reemplaza = self.ren_regex_reemplaza_var.get()
        try:
            res = SyncEngine.resolver_nombre_dinamico(patron, __file__, 1, busca, reemplaza)
            self.lbl_preview_var.set(res + ".jpg")
        except Exception:
            self.lbl_preview_var.set("Sintaxis Errónea")

    # ------------------------------------------------------------------
    # Configuración
    # ------------------------------------------------------------------
    def _recopilar_config(self) -> AppConfig:
        import json as _json
        destinos = self._get_destinos()
        return AppConfig(
            idioma=self.t.lang, origen=self.txt_origen.get().strip(),
            destino=_json.dumps(destinos, ensure_ascii=False),
            chk_fotos=self.chk_fotos_var.get(), chk_videos=self.chk_videos_var.get(),
            chk_docs=self.chk_docs_var.get(), chk_otros=self.chk_otros_var.get(),
            modo_mover=self.radio_accion_var.get() == "mover", dry_run=self.dry_run_var.get(),
            copia_atomica=self.copia_atomica_var.get(), colision=self.colision_var.get(),
            regex_excluir=self.regex_excluir_var.get(), patron_renombrado=self.patron_renombrado_var.get(),
            ren_regex_busca=self.ren_regex_busca_var.get(), ren_regex_reemplaza=self.ren_regex_reemplaza_var.get(),
            accion_duplicados=self.metodo_borrado_var.get(), filtro_tamano_min=self.txt_size_min.get(),
            filtro_tamano_max=self.txt_size_max.get(), modo_apariencia=self.modo_apariencia_var.get(),
            modo_similitud_dup=self.modo_similitud_var.get(),
            umbral_similitud_dup=self.umbral_similitud_var.get(),
            color_acento_personalizado=self.color_acento_var.get(), color_fondo_paneles=self.color_marcos_var.get(),
        )

    def _guardar_config_live(self) -> None:
        self.config = self._recopilar_config()
        self.config.save()

    # ------------------------------------------------------------------
    # Log / progreso de sincronización
    # ------------------------------------------------------------------
    def _agregar_log(self, mensaje: str, tipo: str = "normal") -> None:
        def _do():
            self.txt_log.configure(state="normal")
            ts = datetime.now().strftime("%H:%M:%S")
            self.txt_log.insert("end", f"[{ts}] {mensaje}\n")
            self.txt_log.configure(state="disabled")
            self.txt_log.see("end")
        self.root.after(0, _do)

    def _actualizar_progreso(self, chunk_bytes: int) -> None:
        with self._progress_lock:
            self._progress_done_bytes += chunk_bytes
            done = self._progress_done_bytes
            total = self._progress_total_bytes
            self.contador_archivos += 1
            n = self.contador_archivos

        def _do():
            mb_done = done / (1024 * 1024)
            if total > 0:
                ratio = min(1.0, done / total)
                mb_total = total / (1024 * 1024)
                self.progress_bar.set(ratio)
                self.lbl_estado.configure(
                    text=f"Procesados: {n} ({mb_done:.1f} / {mb_total:.1f} MB  {ratio*100:.0f}%)"
                )
            else:
                # Total aún no calculado: barra indeterminada (oscila entre 0 y 0.95)
                self.progress_bar.set(min(0.95, n / max(n + 10, 50)))
                self.lbl_estado.configure(text=f"Procesados: {n} ({mb_done:.1f} MB…)")
        self.root.after(0, _do)

    def _calcular_total_bytes(self, origen: str, destinos_count: int) -> None:
        """Hilo de fondo: suma el tamaño de todos los archivos del origen
        y actualiza _progress_total_bytes. No penaliza la copia porque
        corre en paralelo y solo hace stat() sin leer contenido."""
        total = 0
        try:
            cfg = self.config
            from .constants import DICCIONARIO_EXTENSIONES
            import re as _re
            for raiz, _, ficheros in os.walk(origen):
                for nombre in ficheros:
                    ruta = Path(raiz) / nombre
                    try:
                        ext = ruta.suffix.lower()
                        # Respetar los mismos filtros que SyncEngine._pasa_filtros
                        if cfg.regex_excluir:
                            if _re.search(cfg.regex_excluir, nombre):
                                continue
                        if ext in DICCIONARIO_EXTENSIONES["fotos"] and not cfg.chk_fotos:
                            continue
                        if ext in DICCIONARIO_EXTENSIONES["videos"] and not cfg.chk_videos:
                            continue
                        if ext in DICCIONARIO_EXTENSIONES["documentos"] and not cfg.chk_docs:
                            continue
                        total += ruta.stat().st_size
                    except OSError:
                        continue
        except Exception as exc:
            logger.debug("_calcular_total_bytes error: %s", exc)
        # Multiplicar por número de destinos (la copia se hace N veces)
        with self._progress_lock:
            self._progress_total_bytes = total * destinos_count
        logger.debug("Total estimado: %.1f MB", total * destinos_count / (1024 * 1024))

    # ------------------------------------------------------------------
    # Gestión de destinos múltiples
    # ------------------------------------------------------------------
    def _agregar_destino(self) -> None:
        ruta = filedialog.askdirectory(title="Seleccionar carpeta destino")
        if not ruta:
            return
        rutas_existentes = [
            self.lista_destinos.item(i, "values")[0]
            for i in self.lista_destinos.get_children()
        ]
        if ruta not in rutas_existentes:
            self.lista_destinos.insert("", "end", values=(ruta,))
        self._guardar_config_live()

    def _quitar_destino(self) -> None:
        sel = self.lista_destinos.selection()
        for item in sel:
            self.lista_destinos.delete(item)
        self._guardar_config_live()

    def _get_destinos(self) -> list[str]:
        return [
            self.lista_destinos.item(i, "values")[0]
            for i in self.lista_destinos.get_children()
        ]

    def _alternar_sincronizacion(self) -> None:
        # ── DETENER ──────────────────────────────────────────────────────
        if self._sync_engines:
            for eng in self._sync_engines:
                eng.detener()
            self._sync_engines.clear()
            self.btn_lanzar.configure(text=self.t("btn_lanzar"), fg_color="#1b5e20", hover_color="#2e7d32")
            self.lbl_estado.configure(text=self.t("lbl_estado_off"))
            self._agregar_log("⏹️ Servicio detenido por el usuario.")
            return

        # ── VALIDAR ───────────────────────────────────────────────────────
        self._guardar_config_live()
        cfg = self.config
        destinos = self._get_destinos()

        if not cfg.origen:
            messagebox.showwarning("Configuración", "Defina la carpeta origen.")
            return
        if not destinos:
            messagebox.showwarning("Configuración", "Añade al menos una carpeta destino.")
            return
        if not Path(cfg.origen).exists():
            messagebox.showerror("Configuración", "La carpeta origen no existe.")
            return

        # ── LANZAR N motores en paralelo, uno por destino ────────────────
        self.contador_archivos = 0
        with self._progress_lock:
            self._progress_done_bytes = 0
            self._progress_total_bytes = 0
        self.progress_bar.set(0)
        self._sync_engines: list[SyncEngine] = []

        # Hilo de cálculo del total (no bloquea el inicio de la copia)
        threading.Thread(
            target=self._calcular_total_bytes,
            args=(cfg.origen, len(destinos)),
            daemon=True,
        ).start()

        import dataclasses
        pendientes = [True] * len(destinos)   # un slot por motor

        def _make_finished_cb(idx: int):
            def _on_finished():
                pendientes[idx] = False
                self._agregar_log(f"✅ Destino [{idx+1}] finalizado: {destinos[idx]}")
                if not any(pendientes):           # todos terminaron
                    def _ui():
                        self._sync_engines.clear()
                        self.btn_lanzar.configure(
                            text=self.t("btn_lanzar"), fg_color="#1b5e20", hover_color="#2e7d32")
                        self.lbl_estado.configure(text=self.t("lbl_estado_off"))
                        self.progress_bar.set(1.0)
                    self.root.after(0, _ui)
            return _on_finished

        for idx, dest in enumerate(destinos):
            cfg_dest = dataclasses.replace(cfg, destino=dest)
            engine = SyncEngine(
                cfg_dest,
                self._agregar_log,
                self._actualizar_progreso,
                history=self.history,
                on_finished=_make_finished_cb(idx),
            )
            engine.activo = True
            self._sync_engines.append(engine)
            self._agregar_log(f"🚀 Motor [{idx+1}/{len(destinos)}] → {dest}")
            threading.Thread(target=engine.iniciar, daemon=True).start()

        n = len(destinos)
        self.btn_lanzar.configure(text=self.t("btn_detener"), fg_color="#b71c1c", hover_color="#c62828")
        self.lbl_estado.configure(
            text=f"{self.t('lbl_estado_on')} ({n} destino{'s' if n > 1 else ''})"
        )

    # ------------------------------------------------------------------
    # Sesiones guardadas
    # ------------------------------------------------------------------
    def _refrescar_combo_sesiones(self) -> None:
        nombres = ["— Nueva sesión —"] + self.session_manager.names()
        self.combo_sesiones.configure(values=nombres)

    def _on_sesion_seleccionada(self, nombre: str) -> None:
        if nombre == "— Nueva sesión —":
            self.lbl_ses_vol.configure(text="")
            return
        ses = self.session_manager.get(nombre)
        if not ses:
            return
        # Mostrar estado de discos (conectado / desconectado)
        partes = []
        for dr in [ses.origen] + ses.destinos:
            resuelto = dr.resolve()
            ok = Path(resuelto).exists()
            icono = "✅" if ok else "⚠️"
            label = dr.label or Path(dr.path).anchor
            partes.append(f"{icono}{label}({dr.volume_id[:6]})")
        self.lbl_ses_vol.configure(text="  ".join(partes))

    def _guardar_sesion_ui(self) -> None:
        from tkinter.simpledialog import askstring
        nombre = askstring("Guardar sesión", "Nombre para esta sesión:",
                           initialvalue=self.combo_sesiones.get()
                           if self.combo_sesiones.get() != "— Nueva sesión —" else "")
        if not nombre or not nombre.strip():
            return
        nombre = nombre.strip()

        origen_path = self.txt_origen.get().strip()
        destinos_paths = self._get_destinos()

        ses = Session(
            name=nombre,
            origen=DiskRef.from_path(origen_path) if origen_path else DiskRef(""),
            destinos=[DiskRef.from_path(d) for d in destinos_paths],
            config_snapshot={
                "patron_renombrado": self.patron_renombrado_var.get(),
                "chk_fotos": self.chk_fotos_var.get(),
                "chk_videos": self.chk_videos_var.get(),
                "chk_docs": self.chk_docs_var.get(),
                "chk_otros": self.chk_otros_var.get(),
                "modo_mover": self.radio_accion_var.get(),
                "colision": self.colision_var.get(),
            },
        )
        self.session_manager.save_session(ses)
        self._refrescar_combo_sesiones()
        self.combo_sesiones.set(nombre)
        self._on_sesion_seleccionada(nombre)
        messagebox.showinfo("Sesión guardada", f"Sesión «{nombre}» guardada correctamente.")

    def _cargar_sesion_ui(self) -> None:
        nombre = self.combo_sesiones.get()
        if nombre == "— Nueva sesión —":
            messagebox.showinfo("Sesiones", "Selecciona una sesión del desplegable para cargarla.")
            return
        ses = self.session_manager.get(nombre)
        if not ses:
            return

        # Resolver rutas (puede haber cambiado la letra de unidad)
        origen_resuelto = ses.origen.resolve()
        if not Path(origen_resuelto).exists():
            resp = messagebox.askyesno(
                "Disco no disponible",
                f"El disco origen «{ses.origen.label or ses.origen.path}» "
                f"(ID: {ses.origen.volume_id}) no se detecta.\n\n"
                "¿Cargar la sesión de todas formas con las rutas guardadas?",
            )
            if not resp:
                return

        self.txt_origen.delete(0, "end")
        self.txt_origen.insert(0, origen_resuelto)

        # Limpiar y repoblar destinos
        for item in self.lista_destinos.get_children():
            self.lista_destinos.delete(item)
        for dr in ses.destinos:
            resuelto = dr.resolve()
            existe = Path(resuelto).exists()
            label_extra = "" if existe else " ⚠️ (no disponible)"
            self.lista_destinos.insert("", "end", values=(resuelto + label_extra,))

        # Restaurar config snapshot
        snap = ses.config_snapshot
        if snap:
            self.patron_renombrado_var.set(snap.get("patron_renombrado", self.patron_renombrado_var.get()))
            self.chk_fotos_var.set(snap.get("chk_fotos", True))
            self.chk_videos_var.set(snap.get("chk_videos", False))
            self.chk_docs_var.set(snap.get("chk_docs", False))
            self.chk_otros_var.set(snap.get("chk_otros", False))
            self.radio_accion_var.set(snap.get("modo_mover", "copiar"))
            self.colision_var.set(snap.get("colision", "renombrar"))

        ses.mark_used()
        self.session_manager.save_session(ses)
        self._on_sesion_seleccionada(nombre)
        self._guardar_config_live()
        messagebox.showinfo("Sesión cargada", f"Sesión «{nombre}» cargada.")

    def _borrar_sesion_ui(self) -> None:
        nombre = self.combo_sesiones.get()
        if nombre == "— Nueva sesión —":
            return
        if not messagebox.askyesno("Borrar sesión", f"¿Eliminar la sesión «{nombre}»?"):
            return
        self.session_manager.delete(nombre)
        self._refrescar_combo_sesiones()
        self.combo_sesiones.set("— Nueva sesión —")
        self.lbl_ses_vol.configure(text="")

    def _restaurar_destinos(self) -> None:
        """Carga la lista de destinos guardada (JSON) en config.destino."""
        import json as _json
        raw = self.config.destino
        if not raw:
            return
        try:
            destinos = _json.loads(raw)
            if isinstance(destinos, list):
                for d in destinos:
                    if d:
                        self.lista_destinos.insert("", "end", values=(d,))
                return
        except (_json.JSONDecodeError, TypeError):
            pass
        # Compatibilidad con versiones anteriores: valor es ruta plana
        if raw.strip():
            self.lista_destinos.insert("", "end", values=(raw.strip(),))

    def _precargar_checkpoint_sincro(self) -> None:
        from .constants import CHK_SINCRO
        if CHK_SINCRO.exists():
            self._agregar_log("💡 Detectado checkpoint de sincronización previo. Listo para reanudar.")

    # ------------------------------------------------------------------
    # Duplicados
    # ------------------------------------------------------------------
    def _actualizar_consola(self, txt: str) -> None:
        def _do():
            self.txt_consola_perf.configure(state="normal")
            self.txt_consola_perf.delete("1.0", "end")
            self.txt_consola_perf.insert("1.0", txt)
            self.txt_consola_perf.configure(state="disabled")
        self.root.after(0, _do)

    def _actualizar_estado_hilo(self, nombre_hilo: str, estado: str) -> None:
        self.progreso_hilos[nombre_hilo] = estado
        buff = "=== ESTADO DE CARGA MULTI-HILO DEL PROCESADOR ===\n"
        for k, v in self.progreso_hilos.items():
            buff += f"⚡ [{k}]: {v}\n"
        self._actualizar_consola(buff)

    def _click_analizar_duplicados(self) -> None:
        if self.duplicados_en_progreso:
            self.dup_engine.cancelar()
            self.btn_analizar_dup.configure(text=self.t("btn_analizar_dup"), fg_color="#0052cc")
            return

        rutas = [self.lista_carpetas_dup.item(i, "values")[0].strip()
                 for i in self.lista_carpetas_dup.get_children()]
        rutas = [r for r in rutas if r]

        if not rutas:
            origen_main = self.txt_origen.get().strip()
            if origen_main:
                rutas = [origen_main]
            else:
                messagebox.showerror("Error de Unidad",
                                      "Por favor, configure o seleccione un directorio válido para analizar.")
                return

        self.btn_analizar_dup.configure(text="🛑 Detener Análisis", fg_color="#b71c1c")
        self.duplicados_en_progreso = True
        self.progreso_hilos.clear()

        try:
            hilos = int(self.combo_hilos.get())
        except ValueError:
            hilos = 4

        modo_sim = (
            "similar"
            if "Similar" in self.combo_similitud.get()
            else "exact"
        )
        opts = DuplicateOptions(
            incluir_fotos=self.chk_fotos_var.get(), incluir_videos=self.chk_videos_var.get(),
            incluir_docs=self.chk_docs_var.get(), incluir_otros=self.chk_otros_var.get(),
            filtrar_por_nombre=self.chk_dup_nombre_var.get(), filtrar_por_tamano=self.chk_dup_tamano_var.get(),
            max_hilos=hilos,
            modo_similitud=modo_sim,
            umbral_similitud=self.umbral_similitud_var.get(),
        )

        def ejecucion_fondo():
            duplicados, metricas = self.dup_engine.buscar_duplicados(
                rutas, opts, self._actualizar_consola, self._actualizar_estado_hilo
            )
            self.duplicados_detectados = duplicados

            def _finalizar():
                self.lbl_metrics_dup.configure(text=self.t(
                    "metrics_text", ficheros=metricas["ficheros"],
                    directorios=metricas["directorios"], tiempo=metricas["tiempo_ms"],
                ))
                # Estadísticas por tipo de archivo (Mejora #2)
                stats = metricas.get("stats", {})
                if stats:
                    top = sorted(stats.items(), key=lambda x: -x[1])[:4]
                    stats_txt = "  |  ".join(f"{ext}: {n}" for ext, n in top)
                    self.lbl_stats_tipos.configure(text=f"Por tipo: {stats_txt}")
                self._finalizar_analisis_ui()
            self.root.after(0, _finalizar)

        threading.Thread(target=ejecucion_fondo, daemon=True).start()

    def _finalizar_analisis_ui(self) -> None:
        self.duplicados_en_progreso = False
        self.btn_analizar_dup.configure(text=self.t("btn_analizar_dup"), fg_color="#0052cc")
        self.btn_ejecutar_dup.configure(state="disabled")
        self.btn_exportar_csv.configure(state="disabled")

        for row in self.tree_dup.get_children():
            self.tree_dup.delete(row)

        if not self.duplicados_detectados:
            self.lbl_status_dup.configure(text=self.t("dup_status_no_found"), text_color="#2e7d32")
            self.lbl_saving_dup.configure(text=self.t("disk_saving_init"))
            return

        self.lbl_status_dup.configure(
            text=self.t("dup_status_found", num_grupos=len(self.duplicados_detectados)), text_color="#b71c1c")

        for idx_grupo, (llave_grupo, nodos) in enumerate(self.duplicados_detectados.items(), start=1):
            es_similar = llave_grupo.startswith("SIMILAR_")
            icono = "🖼️" if es_similar else "📦"
            etiqueta_tipo = " [SIMILAR]" if es_similar else ""
            id_padre = self.tree_dup.insert(
                "", "end",
                text=f"{icono} {self.t('dup_grupo_prefijo')} {idx_grupo}{etiqueta_tipo}",
                open=True,
            )
            if es_similar:
                self.tree_dup.item(id_padre, tags=("tag_similar_group",))
            self.tree_dup.set(id_padre, "Ruta", "")
            self.tree_dup.set(id_padre, "Tamaño", "")
            self.tree_dup.set(id_padre, "Acción", "Grupo")

            for idx, nodo in enumerate(nodos):
                p = Path(nodo.path)
                sz_mb = nodo.size / (1024 * 1024)
                accion_inicial = "ELIMINAR" if idx > 0 else "MANTENER"

                id_hijo = self.tree_dup.insert(id_padre, "end", text=p.name)
                self.tree_dup.set(id_hijo, "Ruta", str(p.parent))
                self.tree_dup.set(id_hijo, "Tamaño", f"{sz_mb:.2f} MB")
                self.tree_dup.set(id_hijo, "Acción", accion_inicial)
                self.tree_dup.item(id_hijo, tags=("tag_mantener" if accion_inicial == "MANTENER" else "tag_eliminar",))

        self.tree_dup.tag_configure("tag_mantener", background="#e8f5e9", foreground="#2e7d32")
        self.tree_dup.tag_configure("tag_eliminar", background="#ffebee", foreground="#c62828")
        # Mark SIMILAR groups distinctly
        self.tree_dup.tag_configure("tag_similar_group", foreground="#ff8f00", font=("Segoe UI", 10, "italic"))
        self.btn_ejecutar_dup.configure(state="normal")
        self.btn_exportar_csv.configure(state="normal")
        self._recalcular_ahorro_espacio()

    def _on_modo_similitud_change(self, val: str) -> None:
        """Habilita/deshabilita slider según modo seleccionado."""
        is_sim = "Similar" in val
        state = "normal" if is_sim else "disabled"
        self.slider_umbral.configure(state=state)
        self.modo_similitud_var.set("similar" if is_sim else "exact")
        self._guardar_config_live()

    def _exportar_csv_duplicados(self) -> None:
        """Mejora #3: exportar informe a CSV."""
        from tkinter import filedialog as fd
        ruta = fd.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Todos", "*.*")],
            title="Guardar informe de duplicados",
        )
        if not ruta:
            return
        ok = self.dup_engine.exportar_csv(self.duplicados_detectados, ruta)
        if ok:
            from tkinter import messagebox as mb
            mb.showinfo("Exportación completada", f"Informe guardado en:\n{ruta}")
        else:
            from tkinter import messagebox as mb
            mb.showerror("Error", "No se pudo exportar el CSV. Revisa el log.")

    def _dbl_click_dup_tree(self, event) -> None:
        """Doble clic: abre preview en imagen individual, o comparador en grupo SIMILAR."""
        item_id = self.tree_dup.identify_row(event.y)
        if not item_id:
            return
        parent = self.tree_dup.parent(item_id)
        if not parent:
            # Clic en fila padre: si es grupo SIMILAR, lanzar comparador con todos sus hijos
            texto_padre = self.tree_dup.item(item_id, "text")
            if "SIMILAR" in texto_padre:
                rutas = []
                for hijo in self.tree_dup.get_children(item_id):
                    v = self.tree_dup.item(hijo, "values")
                    n = self.tree_dup.item(hijo, "text")
                    if v:
                        rutas.append(str(Path(v[0]) / n))
                if len(rutas) >= 2:
                    self._abrir_comparador_similares(rutas)
            return

        valores = self.tree_dup.item(item_id, "values")
        if not valores:
            return
        nombre = self.tree_dup.item(item_id, "text")
        ruta_abs = str(Path(valores[0]) / nombre)
        ext = Path(ruta_abs).suffix.lower()
        if ext in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".heic"}:
            # Si el padre es grupo SIMILAR y hay hermanos, abrir comparador
            texto_padre = self.tree_dup.item(parent, "text")
            if "SIMILAR" in texto_padre:
                hermanos = self.tree_dup.get_children(parent)
                rutas = []
                for h in hermanos:
                    v = self.tree_dup.item(h, "values")
                    n = self.tree_dup.item(h, "text")
                    if v:
                        rutas.append(str(Path(v[0]) / n))
                if len(rutas) >= 2:
                    self._abrir_comparador_similares(rutas, destacar=ruta_abs)
                    return
            self._abrir_preview_imagen(ruta_abs)

    def _single_click_dup_tree(self, event) -> None:
        """Clic simple: alterna MANTENER / ELIMINAR en filas hijo."""
        item_id = self.tree_dup.identify_row(event.y)
        if not item_id or not self.tree_dup.parent(item_id):
            return
        valores = self.tree_dup.item(item_id, "values")
        if not valores or len(valores) < 3 or valores[2] == "Grupo":
            return
        nueva = "MANTENER" if valores[2] == "ELIMINAR" else "ELIMINAR"
        self.tree_dup.set(item_id, column=2, value=nueva)
        self.tree_dup.item(item_id, tags=("tag_mantener" if nueva == "MANTENER" else "tag_eliminar",))
        self._recalcular_ahorro_espacio()

    def _ctx_menu_dup_tree(self, event) -> None:
        """Clic derecho: menú contextual con 'Abrir carpeta' y 'Preview'."""
        item_id = self.tree_dup.identify_row(event.y)
        if not item_id:
            return
        self.tree_dup.selection_set(item_id)
        parent = self.tree_dup.parent(item_id)

        menu = tk.Menu(self.root, tearoff=0)

        if parent:  # fila hijo
            valores = self.tree_dup.item(item_id, "values")
            nombre = self.tree_dup.item(item_id, "text")
            ruta_abs = str(Path(valores[0]) / nombre) if valores else ""
            carpeta = str(valores[0]) if valores else ""
            ext = Path(ruta_abs).suffix.lower() if ruta_abs else ""

            menu.add_command(
                label="📂 Abrir carpeta contenedora",
                command=lambda: self._abrir_carpeta_en_explorador(carpeta),
            )
            if ext in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".heic"}:
                menu.add_separator()
                menu.add_command(
                    label="🔍 Previsualizar imagen",
                    command=lambda: self._abrir_preview_imagen(ruta_abs),
                )
                texto_padre = self.tree_dup.item(parent, "text")
                if "SIMILAR" in texto_padre:
                    rutas = [
                        str(Path(self.tree_dup.item(h, "values")[0]) / self.tree_dup.item(h, "text"))
                        for h in self.tree_dup.get_children(parent)
                        if self.tree_dup.item(h, "values")
                    ]
                    if len(rutas) >= 2:
                        menu.add_command(
                            label="⚖️ Comparar con grupo",
                            command=lambda: self._abrir_comparador_similares(rutas, destacar=ruta_abs),
                        )
        else:  # fila padre
            texto_padre = self.tree_dup.item(item_id, "text")
            if "SIMILAR" in texto_padre:
                rutas = [
                    str(Path(self.tree_dup.item(h, "values")[0]) / self.tree_dup.item(h, "text"))
                    for h in self.tree_dup.get_children(item_id)
                    if self.tree_dup.item(h, "values")
                ]
                if len(rutas) >= 2:
                    menu.add_command(
                        label="⚖️ Comparar imágenes similares",
                        command=lambda: self._abrir_comparador_similares(rutas),
                    )

        menu.tk_popup(event.x_root, event.y_root)

    def _abrir_carpeta_en_explorador(self, carpeta: str) -> None:
        """Abre el explorador del sistema en la carpeta indicada."""
        import subprocess, sys as _sys
        if not carpeta or not Path(carpeta).exists():
            messagebox.showwarning("Carpeta no encontrada", f"La carpeta no existe:\n{carpeta}")
            return
        try:
            if _sys.platform == "win32":
                os.startfile(carpeta)
            elif _sys.platform == "darwin":
                subprocess.Popen(["open", carpeta])
            else:
                subprocess.Popen(["xdg-open", carpeta])
        except Exception as exc:
            messagebox.showerror("Error", f"No se pudo abrir el explorador:\n{exc}")

    def _abrir_preview_imagen(self, ruta_abs: str) -> None:
        """Preview individual de imagen con botón de acción rápida MANTENER/ELIMINAR."""
        try:
            from PIL import Image, ImageTk
        except ImportError:
            messagebox.showwarning("Pillow no instalado", "pip install Pillow")
            return

        p = Path(ruta_abs)
        if not p.exists():
            messagebox.showwarning("Archivo no encontrado", str(p))
            return

        win = ctk.CTkToplevel(self.root)
        win.title(f"Preview: {p.name}")
        win.geometry("660x520")
        win.grab_set()

        try:
            img = Image.open(ruta_abs)
            orig_w, orig_h = img.size
            img.thumbnail((600, 380))
            photo = ImageTk.PhotoImage(img)
            lbl_img = tk.Label(win, image=photo, bg="#1a1a1a")
            lbl_img.image = photo
            lbl_img.pack(padx=10, pady=10)
        except Exception as exc:
            ctk.CTkLabel(win, text=f"No se pudo cargar:\n{exc}").pack(padx=20, pady=20)
            orig_w, orig_h = 0, 0

        info = (f"{p.name}   |   {orig_w}×{orig_h} px   |   "
                f"{p.stat().st_size / (1024*1024):.2f} MB\n{p.parent}")
        ctk.CTkLabel(win, text=info, font=("Consolas", 10), justify="left").pack(padx=10)

        f_btns = ctk.CTkFrame(win, fg_color="transparent")
        f_btns.pack(pady=8)

        def _set_accion(accion: str):
            # Buscar el item del tree que corresponde a esta ruta y cambiar su acción
            for padre in self.tree_dup.get_children():
                for hijo in self.tree_dup.get_children(padre):
                    v = self.tree_dup.item(hijo, "values")
                    n = self.tree_dup.item(hijo, "text")
                    if v and str(Path(v[0]) / n) == ruta_abs:
                        self.tree_dup.set(hijo, column=2, value=accion)
                        self.tree_dup.item(hijo, tags=(
                            "tag_mantener" if accion == "MANTENER" else "tag_eliminar",))
                        self._recalcular_ahorro_espacio()
                        break
            win.destroy()

        ctk.CTkButton(f_btns, text="✅ MANTENER", fg_color="#2e7d32", hover_color="#1b5e20",
                      command=lambda: _set_accion("MANTENER"), width=140).pack(side="left", padx=8)
        ctk.CTkButton(f_btns, text="🗑️ ELIMINAR", fg_color="#b71c1c", hover_color="#7f0000",
                      command=lambda: _set_accion("ELIMINAR"), width=140).pack(side="left", padx=8)
        ctk.CTkButton(f_btns, text="📂 Abrir carpeta",
                      command=lambda: self._abrir_carpeta_en_explorador(str(p.parent)),
                      width=140).pack(side="left", padx=8)
        ctk.CTkButton(f_btns, text="Cerrar", command=win.destroy, width=80).pack(side="left", padx=8)

    def _abrir_comparador_similares(self, rutas: list[str],
                                     destacar: str | None = None) -> None:
        """Ventana comparadora: muestra hasta 4 imágenes en grid + mapa de diferencias."""
        try:
            from PIL import Image, ImageTk, ImageChops, ImageEnhance, ImageFilter
        except ImportError:
            messagebox.showwarning("Pillow no instalado", "pip install Pillow")
            return

        rutas = [r for r in rutas if Path(r).exists()]
        if len(rutas) < 2:
            return

        # Limitar a 4 para que la UI sea manejable
        rutas = rutas[:4]
        n = len(rutas)

        win = ctk.CTkToplevel(self.root)
        win.title(f"Comparador de imágenes similares ({n} archivos)")
        win.geometry("1100x680")
        win.grab_set()

        # ── Panel superior: imágenes en fila ────────────────────────────
        THUMB = (240, 200)

        f_imgs = ctk.CTkFrame(win, fg_color="#111111")
        f_imgs.pack(fill="x", padx=10, pady=8)

        imagenes_pil: list[Image.Image] = []
        photos: list[ImageTk.PhotoImage] = []
        acciones_vars: list[tk.StringVar] = []

        for idx, ruta in enumerate(rutas):
            p = Path(ruta)
            f_col = ctk.CTkFrame(f_imgs, fg_color="#1a1a1a", corner_radius=6)
            f_col.pack(side="left", padx=6, pady=6, expand=True, fill="both")

            try:
                img = Image.open(ruta).convert("RGB")
                imagenes_pil.append(img)
                thumb = img.copy()
                thumb.thumbnail(THUMB)
                ph = ImageTk.PhotoImage(thumb)
                photos.append(ph)
                borde = "#ffcc00" if ruta == destacar else "#333333"
                lbl = tk.Label(f_col, image=ph, bg=borde, bd=3, relief="solid")
                lbl.image = ph
                lbl.pack(padx=4, pady=4)
            except Exception:
                imagenes_pil.append(None)
                photos.append(None)
                ctk.CTkLabel(f_col, text="Error\ncargando").pack(padx=8, pady=40)

            sz = p.stat().st_size / (1024 * 1024) if p.exists() else 0
            ctk.CTkLabel(f_col, text=p.name, font=("Segoe UI", 9, "bold"),
                         wraplength=230).pack(padx=4)
            ctk.CTkLabel(f_col, text=f"{sz:.2f} MB · {str(p.parent)[-30:]}",
                         font=("Consolas", 8), text_color="#888888",
                         wraplength=230).pack(padx=4)

            # Selector de acción por imagen
            var = tk.StringVar(value="MANTENER" if idx == 0 else "ELIMINAR")
            acciones_vars.append(var)

            def _make_toggle(v=var, r=ruta, fc=f_col):
                def _toggle():
                    nuevo = "ELIMINAR" if v.get() == "MANTENER" else "MANTENER"
                    v.set(nuevo)
                    fc.configure(fg_color="#1a2e1a" if nuevo == "MANTENER" else "#2e1a1a")
                    # Sincronizar con el treeview
                    for padre in self.tree_dup.get_children():
                        for hijo in self.tree_dup.get_children(padre):
                            vv = self.tree_dup.item(hijo, "values")
                            nn = self.tree_dup.item(hijo, "text")
                            if vv and str(Path(vv[0]) / nn) == r:
                                self.tree_dup.set(hijo, column=2, value=nuevo)
                                self.tree_dup.item(hijo, tags=(
                                    "tag_mantener" if nuevo == "MANTENER" else "tag_eliminar",))
                                self._recalcular_ahorro_espacio()
                                return
                return _toggle

            btn_toggle = ctk.CTkButton(
                f_col, textvariable=var, width=110,
                fg_color="#2e7d32" if var.get() == "MANTENER" else "#b71c1c",
                command=_make_toggle(),
            )
            # Re-colorear al pulsar
            _orig_cmd = btn_toggle.cget("command")
            def _colored_cmd(b=btn_toggle, v=var, cmd=_make_toggle()):
                cmd()
                b.configure(fg_color="#2e7d32" if v.get() == "MANTENER" else "#b71c1c")
            btn_toggle.configure(command=_colored_cmd)
            btn_toggle.pack(pady=4)

        # ── Panel inferior: mapa de diferencias ─────────────────────────
        f_diff = ctk.CTkFrame(win, fg_color="#111111")
        f_diff.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        ctk.CTkLabel(f_diff, text="🔬 Mapa de diferencias (ampliado 5×)",
                     font=("Segoe UI", 10, "bold")).pack(pady=(6, 2))

        lbl_diff = tk.Label(f_diff, bg="#111111")
        lbl_diff.pack(padx=8, pady=4)

        def _calcular_diff(idx_a: int = 0, idx_b: int = 1) -> None:
            img_a = imagenes_pil[idx_a] if idx_a < len(imagenes_pil) else None
            img_b = imagenes_pil[idx_b] if idx_b < len(imagenes_pil) else None
            if img_a is None or img_b is None:
                return
            try:
                # Igualar tamaño para la resta
                size = (min(img_a.width, img_b.width, 480),
                        min(img_a.height, img_b.height, 200))
                a_res = img_a.resize(size, Image.LANCZOS)
                b_res = img_b.resize(size, Image.LANCZOS)
                diff = ImageChops.difference(a_res, b_res)
                # Amplificar 5× y añadir suavizado leve para destacar contornos
                diff = ImageEnhance.Brightness(diff).enhance(5.0)
                diff = diff.filter(ImageFilter.SMOOTH)
                ph_diff = ImageTk.PhotoImage(diff)
                lbl_diff.configure(image=ph_diff)
                lbl_diff.image = ph_diff
            except Exception as exc:
                lbl_diff.configure(text=f"Error calculando diferencias: {exc}")

        # Controles de selección de par a comparar
        f_sel = ctk.CTkFrame(win, fg_color="transparent")
        f_sel.pack(pady=4)
        ctk.CTkLabel(f_sel, text="Comparar imágenes:").pack(side="left", padx=6)
        nombres = [Path(r).name[:20] for r in rutas]
        var_a = tk.IntVar(value=0)
        var_b = tk.IntVar(value=1)
        for i, nm in enumerate(nombres):
            ctk.CTkRadioButton(f_sel, text=f"A={nm}", variable=var_a, value=i,
                               command=lambda: _calcular_diff(var_a.get(), var_b.get())
                               ).pack(side="left", padx=3)
        ctk.CTkLabel(f_sel, text="  vs  ").pack(side="left")
        for i, nm in enumerate(nombres):
            ctk.CTkRadioButton(f_sel, text=f"B={nm}", variable=var_b, value=i,
                               command=lambda: _calcular_diff(var_a.get(), var_b.get())
                               ).pack(side="left", padx=3)

        ctk.CTkButton(win, text="Cerrar", command=win.destroy, width=100).pack(pady=6)

        # Calcular diferencia inicial
        win.after(100, lambda: _calcular_diff(0, 1))

    def _recalcular_ahorro_espacio(self) -> None:
        total_bytes = 0
        for padre in self.tree_dup.get_children():
            for hijo in self.tree_dup.get_children(padre):
                valores = self.tree_dup.item(hijo, "values")
                if not valores or len(valores) < 3:
                    continue
                if valores[2] not in ("ELIMINAR", "DELETE"):
                    continue
                try:
                    mb_val = float(str(valores[1]).split()[0])
                    total_bytes += mb_val * 1024 * 1024
                except (ValueError, IndexError):
                    continue
        mb = total_bytes / (1024 * 1024)
        clave = "disk_saving_dry" if self.dry_run_var.get() else "disk_saving_real"
        self.lbl_saving_dup.configure(text=self.t(clave, megas=mb))

    def _alternar_accion_item_dup(self, event) -> None:
        """Compatibilidad interna; delega a _single_click_dup_tree."""
        self._single_click_dup_tree(event)

    def _ejecutar_limpieza_duplicados(self) -> None:
        metodo = self.metodo_borrado_var.get()
        es_dry = self.dry_run_var.get()
        ficheros_procesados = 0
        bytes_liberados = 0

        for padre in self.tree_dup.get_children():
            for hijo in self.tree_dup.get_children(padre):
                valores = self.tree_dup.item(hijo, "values")
                if not valores or len(valores) < 3:
                    continue
                if valores[2] not in ("ELIMINAR", "DELETE"):
                    continue
                nombre = self.tree_dup.item(hijo, "text")
                ruta_abs = str(Path(str(valores[0])) / nombre)

                if es_dry:
                    tamano = Path(ruta_abs).stat().st_size if Path(ruta_abs).exists() else 0
                    ficheros_procesados += 1
                    bytes_liberados += tamano
                    continue

                tamano = self.dup_engine.eliminar(ruta_abs, metodo)
                if tamano:
                    self.history.append("Buscador Duplicados", f"ELIMINADO ({metodo})", ruta_abs, "", tamano)
                ficheros_procesados += 1
                bytes_liberados += tamano

        mb_liberados = bytes_liberados / (1024 * 1024)
        titulo = self.t("msg_dry_title" if es_dry else "msg_real_title")
        cuerpo = self.t("msg_dry_body" if es_dry else "msg_real_body",
                         ficheros=ficheros_procesados, megas=mb_liberados)

        messagebox.showinfo(titulo, cuerpo)
        self.btn_ejecutar_dup.configure(state="disabled")
        self.btn_exportar_csv.configure(state="disabled")
        for row in self.tree_dup.get_children():
            self.tree_dup.delete(row)
        self.lbl_status_dup.configure(text=self.t("dup_init_status"))
        self.lbl_saving_dup.configure(text=self.t("disk_saving_init"))

    # ------------------------------------------------------------------
    # Histórico
    # ------------------------------------------------------------------
    def _abrir_ventana_historico(self) -> None:
        win_hist = ctk.CTkToplevel(self.root)
        win_hist.title("Auditoría de Historial de Operaciones - Motor de Persistencia")
        win_hist.geometry("900x500")
        win_hist.grab_set()

        frame_t = ctk.CTkFrame(win_hist)
        frame_t.pack(fill="both", expand=True, padx=15, pady=10)

        tree_h = ttk.Treeview(frame_t, columns=("TS", "Servicio", "Accion", "Dimension"), show="headings")
        tree_h.heading("TS", text="Timestamp")
        tree_h.heading("Servicio", text="Módulo")
        tree_h.heading("Accion", text="Acción")
        tree_h.heading("Dimension", text="Volumen")
        tree_h.column("TS", width=140, anchor="center")
        tree_h.column("Servicio", width=140, anchor="center")
        tree_h.column("Accion", width=120, anchor="center")
        tree_h.column("Dimension", width=100, anchor="e")
        tree_h.pack(side="left", fill="both", expand=True)

        scr_h = ttk.Scrollbar(frame_t, orient="vertical", command=tree_h.yview)
        tree_h.configure(yscrollcommand=scr_h.set)
        scr_h.pack(side="right", fill="y")

        lista_historica = self.history.load()
        total_bytes = 0
        for idx, item in enumerate(lista_historica):
            t_bytes = item.get("bytes", 0)
            total_bytes += t_bytes
            kb_size = t_bytes / 1024
            size_str = f"{kb_size:.1f} KB" if kb_size < 1024 else f"{kb_size / 1024:.2f} MB"
            tree_h.insert("", "end", iid=str(idx),
                           values=(item.get("timestamp"), item.get("servicio"), item.get("accion"), size_str))

        lbl_info_meta = ctk.CTkLabel(
            win_hist,
            text=f"Registros encontrados: {len(lista_historica)} | Masa de datos gestionada: {total_bytes/(1024*1024):.2f} MB",
            font=("Segoe UI", 11, "bold"))
        lbl_info_meta.pack(padx=15, pady=5, anchor="w")

        def mostrar_detalle(event=None):
            sel = tree_h.selection()
            if not sel:
                return
            real_item = lista_historica[int(sel[0])]
            msg_detalle = (
                "AUDITORÍA ATÓMICA DE OPERACIÓN\n"
                "================================\n"
                f"Fecha Ejecución: {real_item.get('timestamp')}\n"
                f"Módulo Engine: {real_item.get('servicio')}\n"
                f"Acción Física: {real_item.get('accion')}\n"
                f"Volumen: {real_item.get('bytes', 0)/1024:.2f} KB\n\n"
                f"ORIGEN TARGET:\n{real_item.get('origen')}\n\n"
                f"DESTINO GENERADO:\n{real_item.get('destino')}"
            )
            messagebox.showinfo("Detalle de Auditoría de Archivo", msg_detalle, parent=win_hist)

        tree_h.bind("<Double-1>", mostrar_detalle)
        f_botones_h = ctk.CTkFrame(win_hist, fg_color="transparent")
        f_botones_h.pack(fill="x", padx=15, pady=10)
        ctk.CTkButton(f_botones_h, text="🔍 Detalle", width=140, command=mostrar_detalle).pack(side="left", padx=5)

        def purgar_historico():
            if messagebox.askyesno("Confirmar Purga", "¿Eliminar definitivamente todo el registro histórico?",
                                    parent=win_hist):
                if self.history.clear():
                    for row in tree_h.get_children():
                        tree_h.delete(row)
                    lbl_info_meta.configure(text="Registros encontrados: 0 | Masa de datos gestionada: 0.00 MB")

        ctk.CTkButton(f_botones_h, text="🗑️ Purgar Todo", fg_color="#b71c1c", hover_color="#c62828", width=140,
                      command=purgar_historico).pack(side="right", padx=5)

    # ------------------------------------------------------------------
    # Cierre
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Tab Espejo — lógica
    # ------------------------------------------------------------------
    def _agregar_dir_espejo(self) -> None:
        ruta = filedialog.askdirectory(title="Seleccionar directorio espejo")
        if not ruta:
            return
        existentes = [self.lista_espejo.item(i, "values")[0]
                      for i in self.lista_espejo.get_children()]
        if ruta not in existentes:
            self.lista_espejo.insert("", "end", values=(ruta,))

    def _quitar_dir_espejo(self) -> None:
        for item in self.lista_espejo.selection():
            self.lista_espejo.delete(item)

    def _get_dirs_espejo(self) -> list[str]:
        return [self.lista_espejo.item(i, "values")[0]
                for i in self.lista_espejo.get_children()]

    def _log_espejo(self, msg: str) -> None:
        def _do():
            self.txt_log_espejo.configure(state="normal")
            ts = datetime.now().strftime("%H:%M:%S")
            self.txt_log_espejo.insert("end", f"[{ts}] {msg}\n")
            self.txt_log_espejo.configure(state="disabled")
            self.txt_log_espejo.see("end")
        self.root.after(0, _do)

    def _analizar_espejo(self) -> None:
        dirs = self._get_dirs_espejo()
        if len(dirs) < 2:
            messagebox.showwarning("Espejo", "Añade al menos 2 directorios para comparar.")
            return

        for row in self.tree_espejo.get_children():
            self.tree_espejo.delete(row)
        self._mirror_diffs = []
        self.progress_espejo.set(0)
        self.btn_analizar_espejo.configure(state="disabled")
        self.btn_cancelar_espejo.configure(state="normal")
        self.btn_sincronizar_espejo.configure(state="disabled")
        self.lbl_espejo_status.configure(text="⏳ Analizando…")
        self.mirror_engine._cancelado.clear()

        opts = MirrorOptions(
            incluir_fotos=self.mir_fotos_var.get(),
            incluir_videos=self.mir_videos_var.get(),
            incluir_docs=self.mir_docs_var.get(),
            incluir_otros=self.mir_otros_var.get(),
            comparar_por_hash=self.mir_hash_var.get(),
            comparar_por_contenido=self.mir_contenido_var.get(),
        )

        def _fondo():
            diffs = self.mirror_engine.analizar(dirs, opts, self._log_espejo)
            def _ui():
                self._mirror_diffs = diffs
                for d in diffs:
                    sz = f"{d.size_bytes/(1024*1024):.2f} MB"
                    self.tree_espejo.insert(
                        "", "end", text=Path(d.ruta_relativa).name,
                        values=(d.origen.parent.name, d.destino.parent.name, sz, "Pendiente"),
                        tags=("pendiente",),
                    )
                n = len(diffs)
                self.lbl_espejo_status.configure(
                    text=f"{'✅ Todo sincronizado' if n == 0 else f'⚠️ {n} archivos pendientes de copiar'}")
                self.btn_analizar_espejo.configure(state="normal")
                self.btn_cancelar_espejo.configure(state="disabled")
                if n > 0:
                    self.btn_sincronizar_espejo.configure(state="normal")
            self.root.after(0, _ui)

        threading.Thread(target=_fondo, daemon=True).start()

    def _sincronizar_espejo(self) -> None:
        if not self._mirror_diffs:
            return
        self.btn_sincronizar_espejo.configure(state="disabled")
        self.btn_analizar_espejo.configure(state="disabled")
        self.btn_cancelar_espejo.configure(state="normal")
        self.mirror_engine._cancelado.clear()
        diffs = list(self._mirror_diffs)

        def _progreso(copiados: int, total: int):
            ratio = copiados / total if total else 0
            def _ui():
                self.progress_espejo.set(ratio)
                self.lbl_espejo_status.configure(text=f"⚡ Copiando {copiados}/{total}…")
                # Marcar filas como OK
                hijos = self.tree_espejo.get_children()
                idx = copiados - 1
                if 0 <= idx < len(hijos):
                    self.tree_espejo.set(hijos[idx], "Estado", "✅ Copiado")
                    self.tree_espejo.item(hijos[idx], tags=("ok",))
            self.root.after(0, _ui)

        def _fondo():
            ok, errores = self.mirror_engine.sincronizar(diffs, self._log_espejo, _progreso)
            def _ui():
                self.progress_espejo.set(1.0)
                self.lbl_espejo_status.configure(
                    text=f"🏁 Sincronizado: {ok} copiados, {errores} errores.")
                self.btn_analizar_espejo.configure(state="normal")
                self.btn_cancelar_espejo.configure(state="disabled")
                self._mirror_diffs.clear()
            self.root.after(0, _ui)

        threading.Thread(target=_fondo, daemon=True).start()

    def _cancelar_espejo(self) -> None:
        self.mirror_engine.cancelar()
        self.btn_cancelar_espejo.configure(state="disabled")
        self.btn_analizar_espejo.configure(state="normal")
        self.lbl_espejo_status.configure(text="🛑 Cancelado.")

    def _ctx_espejo(self, event) -> None:
        item = self.tree_espejo.identify_row(event.y)
        if not item:
            return
        self.tree_espejo.selection_set(item)
        vals = self.tree_espejo.item(item, "values")
        if not vals:
            return
        nombre = self.tree_espejo.item(item, "text")
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"📂 Abrir origen ({vals[0]})",
                         command=lambda: self._abrir_carpeta_en_explorador(
                             str(next((d.origen.parent for d in self._mirror_diffs
                                       if d.origen.name == nombre), Path(vals[0])))))
        menu.tk_popup(event.x_root, event.y_root)

    # ------------------------------------------------------------------
    # Wizard RegEx — renombrado
    # ------------------------------------------------------------------
    def _abrir_regex_wizard_renombrado(self) -> None:
        """Abre el wizard con archivos de muestra tomados del origen configurado."""
        origen = self.txt_origen.get().strip()
        muestras: list[Path] = []
        if origen and Path(origen).exists():
            for raiz, _, fics in os.walk(origen):
                for f in fics:
                    muestras.append(Path(raiz) / f)
                if len(muestras) >= 500:
                    break

        def _aplicar(expr: str):
            self.ren_regex_busca_var.set(expr)
            self._evaluar_preview_wizard()

        RegexWizard(
            parent=self.root,
            titulo="Wizard RegEx — Filtro de renombrado",
            on_confirm=_aplicar,
            valor_inicial=self.ren_regex_busca_var.get(),
            archivos_muestra=muestras,
            modo_marcado=False,
        )

    # ------------------------------------------------------------------
    # Wizard RegEx — marcado de duplicados
    # ------------------------------------------------------------------
    def _abrir_wizard_marcado(self) -> None:
        """Abre el wizard en modo marcado con los archivos del árbol de duplicados."""
        muestras: list[Path] = []
        for padre in self.tree_dup.get_children():
            for hijo in self.tree_dup.get_children(padre):
                v = self.tree_dup.item(hijo, "values")
                n = self.tree_dup.item(hijo, "text")
                if v:
                    muestras.append(Path(v[0]) / n)

        if not muestras:
            # Sin análisis previo: tomar muestra del disco
            for ruta in self._get_dirs_espejo() or [self.txt_origen.get().strip()]:
                if Path(ruta).exists():
                    for r, _, fics in os.walk(ruta):
                        for f in fics:
                            muestras.append(Path(r) / f)
                        if len(muestras) >= 300:
                            break

        def _aplicar_marcado(expr: str, accion_match: str, accion_no_match: str):
            import re as _re
            campo_var = "nombre_archivo"   # el wizard ya habrá fijado el campo
            try:
                flags = _re.IGNORECASE
                patron = _re.compile(expr, flags)
            except _re.error:
                return
            n_marcados = 0
            for padre in self.tree_dup.get_children():
                for hijo in self.tree_dup.get_children(padre):
                    v = self.tree_dup.item(hijo, "values")
                    n = self.tree_dup.item(hijo, "text")
                    if not v or len(v) < 3:
                        continue
                    p = Path(v[0]) / n
                    # Evaluar según campo seleccionado en el wizard
                    sujeto = p.stem   # nombre_archivo por defecto
                    coincide = bool(patron.search(sujeto))
                    accion = accion_match if coincide else accion_no_match
                    self.tree_dup.set(hijo, column=2, value=accion)
                    self.tree_dup.item(hijo, tags=(
                        "tag_mantener" if accion == "MANTENER" else "tag_eliminar",))
                    n_marcados += 1
            self._recalcular_ahorro_espacio()
            messagebox.showinfo("Reglas aplicadas",
                                f"Regla aplicada a {n_marcados} archivos.\n"
                                f"Coincide → {accion_match} | No coincide → {accion_no_match}")

        RegexWizard(
            parent=self.root,
            titulo="Wizard RegEx — Reglas de marcado de duplicados",
            on_confirm=_aplicar_marcado,
            valor_inicial="",
            archivos_muestra=muestras,
            modo_marcado=True,
        )

    def _al_cerrar(self) -> None:
        try:
            for eng in self._sync_engines:
                eng.detener()
            self.dup_engine.cancelar()
            self.mirror_engine.cancelar()
            self._guardar_config_live()
            self.hash_cache.close()
        finally:
            self.root.destroy()
