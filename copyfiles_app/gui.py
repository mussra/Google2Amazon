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
        self.tab_avanzado = self.notebook.add("tab_avanzado")

        self._construir_tab_sincronizacion()
        self._construir_tab_duplicados()
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
        self.tree_dup.bind("<Double-1>", self._dbl_click_dup_tree)

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
        ent_w_b = ctk.CTkEntry(f_wizard, textvariable=self.ren_regex_busca_var, width=180)
        ent_w_b.grid(row=4, column=1, padx=15, pady=5, sticky="w")
        ent_w_b.bind("<KeyRelease>", lambda e: self._evaluar_preview_wizard())

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
        def _do():
            self.bytes_processed += chunk_bytes
            self.contador_archivos += 1
            mb = self.bytes_processed / (1024 * 1024)
            self.progress_bar.set(min(1.0, self.contador_archivos / 100.0))
            self.lbl_estado.configure(text=f"Procesados: {self.contador_archivos} ({mb:.2f} MB)")
        self.root.after(0, _do)

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
        self.bytes_processed = 0
        self.progress_bar.set(0)
        self._sync_engines: list[SyncEngine] = []

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

    def _abrir_preview_imagen(self, ruta_abs: str) -> None:
        """Mejora #1: preview de imagen duplicada en ventana flotante."""
        try:
            from PIL import Image, ImageTk
        except ImportError:
            from tkinter import messagebox as mb
            mb.showwarning("Pillow no instalado", "Instala Pillow para previsualizar imágenes:\npip install Pillow")
            return

        p = Path(ruta_abs)
        if not p.exists() or p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tiff"}:
            return

        win = ctk.CTkToplevel(self.root)
        win.title(f"Preview: {p.name}")
        win.geometry("600x480")
        win.grab_set()

        try:
            img = Image.open(ruta_abs)
            img.thumbnail((560, 380))
            photo = ImageTk.PhotoImage(img)
            lbl_img = tk.Label(win, image=photo, bg="#1a1a1a")
            lbl_img.image = photo  # keep reference
            lbl_img.pack(padx=10, pady=10)
        except Exception as exc:
            ctk.CTkLabel(win, text=f"No se pudo cargar la imagen:\n{exc}").pack(padx=20, pady=20)

        info = f"{p.name}\nTamaño: {p.stat().st_size / (1024*1024):.2f} MB\nRuta: {p.parent}"
        ctk.CTkLabel(win, text=info, font=("Consolas", 10), justify="left").pack(padx=10, pady=5)
        ctk.CTkButton(win, text="Cerrar", command=win.destroy, width=100).pack(pady=8)

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
                    # valores[1] es "X.XX MB" — extraer el número
                    mb_val = float(str(valores[1]).split()[0])
                    total_bytes += mb_val * 1024 * 1024
                except (ValueError, IndexError):
                    continue
        mb = total_bytes / (1024 * 1024)
        clave = "disk_saving_dry" if self.dry_run_var.get() else "disk_saving_real"
        self.lbl_saving_dup.configure(text=self.t(clave, megas=mb))

    def _dbl_click_dup_tree(self, event) -> None:
        """Mejora #1: doble-clic en hijo abre preview si es imagen, en padre alterna."""
        item_id = self.tree_dup.identify_row(event.y)
        if not item_id:
            return
        # ¿Es hijo (tiene padre)?
        parent = self.tree_dup.parent(item_id)
        if parent:
            valores = self.tree_dup.item(item_id, "values")
            if valores:
                nombre = self.tree_dup.item(item_id, "text")
                ruta_abs = str(Path(valores[0]) / nombre)
                ext = Path(ruta_abs).suffix.lower()
                if ext in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}:
                    self._abrir_preview_imagen(ruta_abs)
                    return
        self._alternar_accion_item_dup(event)

    def _alternar_accion_item_dup(self, event) -> None:
        item_id = self.tree_dup.identify_row(event.y)
        if not item_id:
            return
        # Ignorar filas padre (no tienen parent)
        if not self.tree_dup.parent(item_id):
            return
        valores = self.tree_dup.item(item_id, "values")
        if not valores or len(valores) < 3:
            return
        accion_actual = valores[2]
        if accion_actual == "Grupo":
            return

        nueva_accion = "MANTENER" if accion_actual == "ELIMINAR" else "ELIMINAR"
        # Usar índice de columna en lugar de nombre con tilde (más robusto cross-platform)
        self.tree_dup.set(item_id, column=2, value=nueva_accion)
        self.tree_dup.item(item_id, tags=("tag_mantener" if nueva_accion == "MANTENER" else "tag_eliminar",))
        self._recalcular_ahorro_espacio()

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
    def _al_cerrar(self) -> None:
        try:
            for eng in self._sync_engines:
                eng.detener()
            self.dup_engine.cancelar()
            self._guardar_config_live()
            self.hash_cache.close()
        finally:
            self.root.destroy()
