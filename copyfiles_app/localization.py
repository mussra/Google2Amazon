"""
Internacionalización (i18n) de la interfaz.

Expone el diccionario LOCALIZATION (es/en) y una pequeña clase
Translator que centraliza el acceso seguro a las claves de traducción,
con fallback a la clave bruta y formateo con kwargs protegido.
"""
from __future__ import annotations

LOCALIZATION = {
    "es": {
        "titulo": "Organizador Inteligente v18.3 - Edición Concurrente Premium",
        "tab_sincro": " Sincronización ",
        "tab_duplicados": " Buscador de Duplicados ",
        "tab_avanzado": " Configuración Avanzada ",
        "lbl_origen": "Carpeta Origen:",
        "lbl_destino": "Carpeta Destino:",
        "btn_buscar": "Buscar...",
        "chk_fotos": "📷 Fotos",
        "chk_videos": "🎥 Vídeos",
        "chk_docs": "📄 Documentos",
        "chk_otros": "📦 Otros / Desconocidos",
        "rad_copiar": "Copiar archivos (Mantener origen)",
        "rad_mover": "Mover archivos (Purgar origen)",
        "btn_lanzar": "▶ Lanzar Servicio",
        "btn_detener": "⏹ Apagar Servicio",
        "lbl_estado_off": "Estado: Apagado",
        "lbl_estado_on": "Estado: Activo...",
        "btn_add_ruta": "➕ Añadir",
        "btn_del_ruta": "➖ Quitar",
        "chk_dup_tamano": "Filtro por Tamaño idéntico",
        "chk_dup_nombre": "Filtro por Nombre idéntico",
        "btn_analizar_dup": "🔍 Analizar Duplicados",
        "btn_ejecutar_acc": "Procesar Limpieza de Duplicados",
        "dup_init_status": "Listo para analizar.",
        "wizard_formula": "Fórmula Dinámica Máster:",
        "wizard_lbl_token": "Inyectores rápidos:",
        "wizard_preview": "VISTA PREVIA DEL MOTOR:",
        "chk_dry": "Modo Simulación (Dry Run)",
        "chk_atomic": "Copia Atómica Segura (.tmp)",
        "lbl_ex_rx": "Filtro de Exclusión Global (RegEx):",
        "tit_colisiones": "▼ RESOLUCIÓN DE COLISIONES DE NOMBRES",
        "rad_col_ren": "Autorenombrar archivo (Añade timestamp único)",
        "rad_col_omit": "Omitir si ya existe en destino",
        "tit_rangos": "▼ LIMITACIÓN POR RANGOS DE TAMAÑO (MB)",
        "lbl_tam_min": "Tamaño Mínimo (MB):",
        "lbl_tam_max": "Tamaño Maximó (MB):",
        "wizard_regex_clean": "Limpieza RegEx -> Buscar:",
        "wizard_regex_repl": "Reemplazar por:",
        "lbl_metodo_borrado": "Método de eliminación de duplicados:",
        "lbl_idioma_avanzado": "Idioma de la interfaz de usuario:",
        "btn_visitar_web": "🌐 Visitar Web Corporativa",
        "tree_hdr_grupo": "Grupo / Fichero",
        "tree_hdr_ruta": "Ubicación Absoluta",
        "tree_hdr_tamano": "Tamaño",
        "tree_hdr_accion": "Plan de Acción",
        "dup_status_hilos": "Analizando ficheros en hilos asíncronos...",
        "dup_status_no_found": "Análisis finalizado: ¡No se encontraron duplicados!",
        "dup_status_found": "Detectados {num_grupos} grupos con duplicados. Doble-clic para alternar.",
        "dup_grupo_prefijo": "Grupo",
        "metrics_text": "Métricas: Escaneados: {ficheros} | Directorios: {directorios} | Tiempo: {tiempo:.2f} ms",
        "metrics_init": "Métricas del análisis: Ficheros: 0 | Directorios: 0 | Tiempo: 0.00 ms",
        "disk_saving_dry": "Espacio liberable: {megas:.2f} MB. [SIMULACIÓN - DRY RUN]\nVerde = Se queda | Rojo = Se elimina / va a papelera",
        "disk_saving_real": "Espacio liberable: {megas:.2f} MB.\nVerde = Se queda | Rojo = Se elimina / va a papelera",
        "disk_saving_init": "Espacio liberable estimado: 0.00 MB [Entorno Seguro]",
        "msg_dry_title": "Simulación Dry Run",
        "msg_dry_body": "Simulación finalizada. Se habrían processed {ficheros} duplicados ({megas:.2f} MB).",
        "msg_real_title": "Limpieza Completada",
        "msg_real_body": "Operación completada. Se eliminaron {ficheros} archivos duplicados reales de forma efectiva, liberando {megas:.2f} MB.",
        "tit_apariencia": "▼ PANEL DE APARIENCIA Y ENTORNO VISUAL",
        "lbl_modo_color": "Tema de Interfaz:",
        "lbl_color_acento": "Acento Hex Manual:",
        "lbl_color_paneles": "Marcos Hex Manual:"
    },
    "en": {
        "titulo": "Smart Organizer v18.3 - Concurrent Premium Edition",
        "tab_sincro": " Synchronization ",
        "tab_duplicados": " Duplicate Finder ",
        "tab_avanzado": " Advanced Settings ",
        "lbl_origen": "Source Folder:",
        "lbl_destino": "Destination Folder:",
        "btn_buscar": "Browse...",
        "chk_fotos": "📷 Photos",
        "chk_videos": "🎥 Videos",
        "chk_docs": "📄 Documents",
        "chk_otros": "📦 Others / Unknown",
        "rad_copiar": "Copy files (Keep source)",
        "rad_mover": "Move files (Purge source)",
        "btn_lanzar": "▶ Launch Service",
        "btn_detener": "⏹ Stop Service",
        "lbl_estado_off": "Status: Off",
        "lbl_estado_on": "Status: Active...",
        "btn_add_ruta": "➕ Add",
        "btn_del_ruta": "REMOVE",
        "chk_dup_tamano": "Identical Size Filter",
        "chk_dup_nombre": "Identical Name Filter",
        "btn_analizar_dup": "🔍 Scan Duplicates",
        "btn_ejecutar_acc": "Process Duplicate Cleanup",
        "dup_init_status": "Ready to analyze.",
        "wizard_formula": "Master Dynamic Formula:",
        "wizard_lbl_token": "Quick injectors:",
        "wizard_preview": "ENGINE LIVE PREVIEW:",
        "chk_dry": "Simulation Mode (Dry Run)",
        "chk_atomic": "Safe Atomic Copy (.tmp)",
        "lbl_ex_rx": "Global Exclusion Filter (RegEx):",
        "tit_colisiones": "▼ NAME COLLISION RESOLUTION",
        "rad_col_ren": "Auto-rename file (Adds unique timestamp)",
        "rad_col_omit": "Skip if file already exists in destination",
        "tit_rangos": "▼ SIZE RANGE LIMITATIONS (MB)",
        "lbl_tam_min": "Min Size (MB):",
        "lbl_tam_max": "Max Size (MB):",
        "wizard_regex_clean": "RegEx Cleanup -> Find:",
        "wizard_regex_repl": "Replace with:",
        "lbl_metodo_borrado": "Duplicate deletion method:",
        "lbl_idioma_avanzado": "User Interface Language:",
        "btn_visitar_web": "🌐 Visit Corporate Website",
        "tree_hdr_grupo": "Group / File",
        "tree_hdr_ruta": "Absolute Location",
        "tree_hdr_tamano": "Size",
        "tree_hdr_accion": "Action Plan",
        "dup_status_hilos": "Analyzing files in asynchronous threads...",
        "dup_status_no_found": "Analysis finished: No duplicates found!",
        "dup_status_found": "Detected {num_grupos} duplicate groups. Double-click to toggle.",
        "dup_grupo_prefijo": "Group",
        "metrics_text": "Metrics: Scanned: {ficheros} | Directories: {directorios} | Time: {tiempo:.2f} ms",
        "metrics_init": "Scan metrics: Files: 0 | Directories: 0 | Time: 0.00 ms",
        "disk_saving_dry": "Freeable space: {megas:.2f} MB. [SIMULATION - DRY RUN]\nGreen = Keep | Red = Delete / Send to trash",
        "disk_saving_real": "Freeable space: {megas:.2f} MB.\nGreen = Keep | Red = Delete / Send to trash",
        "disk_saving_init": "Estimated freeable space: 0.00 MB [Safe Mode]",
        "msg_dry_title": "Dry Run Simulation",
        "msg_dry_body": "Simulation finished. {ficheros} duplicates would be processed ({megas:.2f} MB).",
        "msg_real_title": "Cleanup Completed",
        "msg_real_body": "Operation completed. {ficheros} duplicate files successfully deleted from disk, freeing {megas:.2f} MB.",
        "tit_apariencia": "▼ APPEARANCE & VISUAL STYLE CONFIG",
        "lbl_modo_color": "Interface Theme:",
        "lbl_color_acento": "Manual Accent Hex:",
        "lbl_color_paneles": "Manual Frame Hex:"
    }
}

class Translator:
    """Acceso seguro a las cadenas localizadas.

    Si la clave no existe, se devuelve la propia clave en vez de lanzar
    una excepción, para que la interfaz nunca quede en blanco por un
    error de traducción.
    """

    def __init__(self, lang: str = "es"):
        self.lang = lang if lang in LOCALIZATION else "es"

    def set_language(self, lang: str) -> None:
        self.lang = lang if lang in LOCALIZATION else "es"

    def __call__(self, clave: str, **kwargs) -> str:
        tabla = LOCALIZATION.get(self.lang, LOCALIZATION["es"])
        plantilla = tabla.get(clave, clave)
        if not kwargs:
            return plantilla
        try:
            return plantilla.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return plantilla
