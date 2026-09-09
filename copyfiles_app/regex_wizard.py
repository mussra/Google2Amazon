"""
Ventana flotante reutilizable para construir y probar expresiones regulares.

Uso:
    RegexWizard(parent, titulo, campo_target_var, archivos_muestra)

``campo_target_var`` es un tk.StringVar; al confirmar, se escribe en él
la expresión construida. ``archivos_muestra`` es una lista de Path o str
que se usan para el preview en vivo.

Campos sobre los que se puede construir la expresión:
  nombre_archivo, extension, nombre_directorio_padre, ruta_completa,
  tamaño_mb (se evalúa aparte).
"""
from __future__ import annotations

import re
import tkinter as tk
from pathlib import Path
from typing import Callable

import customtkinter as ctk


# Bloques predefinidos de patrón frecuentes
_BLOQUES = [
    ("Empieza por…",    lambda v: f"^{re.escape(v)}"),
    ("Termina en…",     lambda v: f"{re.escape(v)}$"),
    ("Contiene…",       lambda v: re.escape(v)),
    ("No contiene…",    lambda v: f"^(?!.*{re.escape(v)})"),
    ("Extensión…",      lambda v: rf"\.{re.escape(v.lstrip('.'))}" + "$"),
    ("Número al final", lambda v: r"\d+$"),
    ("Fecha YYYYMMDD",  lambda v: r"\d{8}"),
    ("Cualquier cosa",  lambda v: r".*"),
]

_CAMPOS = ["nombre_archivo", "directorio_padre", "ruta_completa", "extension"]


class RegexWizard:
    """
    Ventana flotante de construcción y test de expresiones regulares.

    Parámetros
    ----------
    parent          Widget padre Tk/CTk.
    titulo          Título de la ventana.
    on_confirm      Callback(expr: str) llamado al pulsar Aplicar.
    valor_inicial   Expresión inicial a mostrar en el campo.
    archivos_muestra Lista de rutas (str|Path) para preview en vivo.
    modo_marcado    Si True, añade columna «Acción» (MANTENER/ELIMINAR)
                    en el preview y activa los combos de acción.
    """

    def __init__(
        self,
        parent: tk.Widget,
        titulo: str,
        on_confirm: Callable[[str], None],
        valor_inicial: str = "",
        archivos_muestra: list[str | Path] | None = None,
        modo_marcado: bool = False,
    ):
        self.on_confirm = on_confirm
        self.archivos_muestra = [Path(p) for p in (archivos_muestra or [])]
        self.modo_marcado = modo_marcado

        self.win = ctk.CTkToplevel(parent)
        self.win.title(titulo)
        self.win.geometry("900x640")
        self.win.grab_set()

        self._build_ui(valor_inicial)

    # ------------------------------------------------------------------
    # Construcción de la UI
    # ------------------------------------------------------------------
    def _build_ui(self, valor_inicial: str) -> None:
        # ── Sección superior: campo libre + estado ────────────────────
        f_top = ctk.CTkFrame(self.win)
        f_top.pack(fill="x", padx=12, pady=8)

        ctk.CTkLabel(f_top, text="Expresión Regular:", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, padx=8, pady=4, sticky="w")
        self.var_expr = tk.StringVar(value=valor_inicial)
        self.ent_expr = ctk.CTkEntry(f_top, textvariable=self.var_expr, width=480, font=("Consolas", 12))
        self.ent_expr.grid(row=0, column=1, padx=8, pady=4, sticky="ew")
        f_top.columnconfigure(1, weight=1)

        self.lbl_estado = ctk.CTkLabel(f_top, text="", font=("Consolas", 10))
        self.lbl_estado.grid(row=0, column=2, padx=8)

        ctk.CTkLabel(f_top, text="Campo a evaluar:", font=("Segoe UI", 10)).grid(
            row=1, column=0, padx=8, pady=2, sticky="w")
        self.var_campo = tk.StringVar(value=_CAMPOS[0])
        ctk.CTkComboBox(f_top, values=_CAMPOS, variable=self.var_campo,
                        command=lambda _: self._actualizar_preview(), width=220).grid(
            row=1, column=1, padx=8, pady=2, sticky="w")

        ctk.CTkLabel(f_top, text="Ignorar may/min:", font=("Segoe UI", 10)).grid(
            row=1, column=2, padx=4, sticky="w")
        self.var_ignorecase = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(f_top, text="", variable=self.var_ignorecase,
                        command=self._actualizar_preview).grid(row=1, column=3, padx=4)

        # ── Sección wizard: bloques ───────────────────────────────────
        f_wiz = ctk.CTkFrame(self.win)
        f_wiz.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(f_wiz, text="🧩 Constructor de bloques:", font=("Segoe UI", 10, "bold")).pack(
            anchor="w", padx=8, pady=(6, 2))

        f_bloques = ctk.CTkFrame(f_wiz, fg_color="transparent")
        f_bloques.pack(fill="x", padx=8, pady=4)

        self.var_valor_bloque = tk.StringVar()
        ctk.CTkEntry(f_bloques, textvariable=self.var_valor_bloque,
                     placeholder_text="valor del bloque…", width=200).pack(side="left", padx=4)

        for nombre, builder in _BLOQUES:
            ctk.CTkButton(
                f_bloques, text=nombre, width=110, height=26, font=("Segoe UI", 9),
                command=lambda b=builder: self._aplicar_bloque(b),
            ).pack(side="left", padx=2)

        # ── Operadores ────────────────────────────────────────────────
        f_ops = ctk.CTkFrame(self.win, fg_color="transparent")
        f_ops.pack(fill="x", padx=12, pady=2)
        ctk.CTkLabel(f_ops, text="Combinar con:", font=("Segoe UI", 9)).pack(side="left", padx=4)
        for op_lbl, op_val in [("Y (AND)", "(?=.*BLOQUE_A)(?=.*BLOQUE_B)"),
                                 ("O (OR)", "BLOQUE_A|BLOQUE_B"),
                                 ("NO (NOT)", "^(?!.*BLOQUE)")]:
            ctk.CTkButton(
                f_ops, text=op_lbl, width=110, height=24, font=("Segoe UI", 9),
                fg_color="#37474f", hover_color="#455a64",
                command=lambda v=op_val: self._insertar_texto(v),
            ).pack(side="left", padx=2)
        ctk.CTkButton(f_ops, text="🗑 Limpiar", width=80, height=24,
                      fg_color="#b71c1c", hover_color="#7f0000",
                      command=lambda: self.var_expr.set("")).pack(side="left", padx=6)

        # ── Preview en vivo ───────────────────────────────────────────
        f_prev = ctk.CTkFrame(self.win)
        f_prev.pack(fill="both", expand=True, padx=12, pady=6)

        hdr = ["Archivo", "Campo evaluado", "✅ Coincide"]
        if self.modo_marcado:
            hdr.append("Acción")
        cols = tuple(hdr[1:])

        self.tree_prev = tk.ttk.Treeview(f_prev, columns=cols, show="tree headings", height=14)
        self.tree_prev.heading("#0", text=hdr[0])
        self.tree_prev.column("#0", width=280, anchor="w")
        self.tree_prev.heading(hdr[1], text=hdr[1]); self.tree_prev.column(hdr[1], width=240, anchor="w")
        self.tree_prev.heading(hdr[2], text=hdr[2]); self.tree_prev.column(hdr[2], width=80, anchor="center")
        if self.modo_marcado:
            self.tree_prev.heading("Acción", text="Acción")
            self.tree_prev.column("Acción", width=120, anchor="center")

        self.tree_prev.tag_configure("match", foreground="#2e7d32", background="#e8f5e9")
        self.tree_prev.tag_configure("nomatch", foreground="#b71c1c", background="#ffebee")

        scr = tk.ttk.Scrollbar(f_prev, orient="vertical", command=self.tree_prev.yview)
        self.tree_prev.configure(yscrollcommand=scr.set)
        self.tree_prev.pack(side="left", fill="both", expand=True)
        scr.pack(side="right", fill="y")

        # ── Acción en modo marcado ─────────────────────────────────────
        if self.modo_marcado:
            f_acc = ctk.CTkFrame(self.win, fg_color="transparent")
            f_acc.pack(fill="x", padx=12, pady=2)
            ctk.CTkLabel(f_acc, text="Si coincide →", font=("Segoe UI", 10, "bold")).pack(side="left", padx=6)
            self.var_accion_match = tk.StringVar(value="ELIMINAR")
            ctk.CTkComboBox(f_acc, values=["ELIMINAR", "MANTENER"],
                            variable=self.var_accion_match, width=140,
                            command=lambda _: self._actualizar_preview()).pack(side="left", padx=4)
            ctk.CTkLabel(f_acc, text="Si NO coincide →", font=("Segoe UI", 10)).pack(side="left", padx=6)
            self.var_accion_no_match = tk.StringVar(value="MANTENER")
            ctk.CTkComboBox(f_acc, values=["ELIMINAR", "MANTENER"],
                            variable=self.var_accion_no_match, width=140,
                            command=lambda _: self._actualizar_preview()).pack(side="left", padx=4)

        # ── Botones finales ───────────────────────────────────────────
        f_bot = ctk.CTkFrame(self.win, fg_color="transparent")
        f_bot.pack(fill="x", padx=12, pady=8)
        ctk.CTkButton(f_bot, text="✅ Aplicar", fg_color="#1b5e20", hover_color="#2e7d32",
                      command=self._confirmar, width=140).pack(side="right", padx=6)
        ctk.CTkButton(f_bot, text="Cancelar", fg_color="#37474f",
                      command=self.win.destroy, width=100).pack(side="right", padx=4)
        self.lbl_resumen = ctk.CTkLabel(f_bot, text="", font=("Consolas", 10), text_color="#1f6aa5")
        self.lbl_resumen.pack(side="left", padx=8)

        # Trace en tiempo real
        self.var_expr.trace_add("write", lambda *_: self._actualizar_preview())
        self.var_campo.trace_add("write", lambda *_: self._actualizar_preview())
        self._actualizar_preview()

    # ------------------------------------------------------------------
    # Lógica
    # ------------------------------------------------------------------
    def _campo_valor(self, p: Path) -> str:
        campo = self.var_campo.get()
        if campo == "nombre_archivo":
            return p.stem
        if campo == "extension":
            return p.suffix.lstrip(".")
        if campo == "directorio_padre":
            return p.parent.name
        if campo == "ruta_completa":
            return str(p)
        return p.name

    def _evaluar(self, expr: str, p: Path) -> bool:
        try:
            flags = re.IGNORECASE if self.var_ignorecase.get() else 0
            return bool(re.search(expr, self._campo_valor(p), flags))
        except re.error:
            return False

    def _actualizar_preview(self) -> None:
        expr = self.var_expr.get()
        # Validar regex
        try:
            flags = re.IGNORECASE if self.var_ignorecase.get() else 0
            re.compile(expr, flags)
            self.lbl_estado.configure(text="✅ RegEx válida", text_color="#2e7d32")
        except re.error as exc:
            self.lbl_estado.configure(text=f"❌ {exc}", text_color="#b71c1c")
            self._limpiar_tree()
            return

        self._limpiar_tree()
        n_match = 0
        for p in self.archivos_muestra[:300]:   # max 300 filas para no colgar la UI
            coincide = self._evaluar(expr, p) if expr else False
            campo_val = self._campo_valor(p)
            tag = "match" if coincide else "nomatch"
            icono = "✅" if coincide else "—"
            if coincide:
                n_match += 1
            if self.modo_marcado:
                accion = self.var_accion_match.get() if coincide else self.var_accion_no_match.get()
                self.tree_prev.insert("", "end", text=p.name,
                                      values=(campo_val, icono, accion), tags=(tag,))
            else:
                self.tree_prev.insert("", "end", text=p.name,
                                      values=(campo_val, icono), tags=(tag,))

        total = len(self.archivos_muestra)
        self.lbl_resumen.configure(
            text=f"{n_match} de {total} archivos coinciden")

    def _limpiar_tree(self) -> None:
        for row in self.tree_prev.get_children():
            self.tree_prev.delete(row)

    def _aplicar_bloque(self, builder: Callable) -> None:
        valor = self.var_valor_bloque.get()
        bloque = builder(valor)
        actual = self.var_expr.get()
        self.var_expr.set((actual + bloque) if actual else bloque)

    def _insertar_texto(self, texto: str) -> None:
        actual = self.var_expr.get()
        self.var_expr.set(actual + texto)

    def _confirmar(self) -> None:
        expr = self.var_expr.get()
        try:
            re.compile(expr)
        except re.error as exc:
            tk.messagebox.showerror("RegEx inválida", str(exc), parent=self.win)
            return

        if self.modo_marcado:
            self.on_confirm(expr,
                            self.var_accion_match.get(),
                            self.var_accion_no_match.get())
        else:
            self.on_confirm(expr)
        self.win.destroy()
