# Organizador Inteligente — Suite Refactorizada (v19.0)

Refactor completo del script original `CopyFiles.py` (1 solo archivo, 1380 líneas)
en un paquete modular, mantenible y robusto.

## Estructura

```
main.py                        # Punto de entrada
requirements.txt
copyfiles_app/
    constants.py                # Rutas, extensiones, paletas de color
    localization.py             # Diccionario es/en + Translator
    models.py                   # AppConfig (dataclass con validación y persistencia)
    persistence.py              # HashCache (SQLite thread-safe) + HistoryManager
    sync_engine.py               # SyncEngine: lógica de copiar/mover/renombrar
    duplicate_engine.py          # DuplicateEngine: búsqueda de duplicados multi-hilo
    gui.py                       # Interfaz (CustomTkinter), solo presentación
```

## Cambios principales respecto al original

- **Separación de responsabilidades**: la lógica de negocio (sincronización,
  duplicados, persistencia) vive en módulos independientes de la GUI, así
  se puede probar y reutilizar sin levantar Tkinter (ver `test` de humo
  incluido en el desarrollo).
- **Sin `except Exception: pass` silenciosos**: todos los errores se loguean
  con `logging` y se manejan con la excepción más específica posible
  (`OSError`, `sqlite3.Error`, `json.JSONDecodeError`, `re.error`...),
  lo que hace los fallos visibles y depurables en vez de ocultos.
- **Caché de hashes más eficiente**: antes se abría una conexión SQLite nueva
  por cada archivo analizado; ahora se reutiliza una conexión por hilo
  (`threading.local`), con `WAL` activado, evitando overhead y bloqueos
  bajo concurrencia.
- **Configuración tipada**: `AppConfig` es un `dataclass` con validación y
  tolerancia a archivos de configuración corruptos o con campos antiguos
  (en vez de un diccionario suelto que podía fallar silenciosamente).
- **`os.replace` en vez de `os.rename`** para la copia atómica (multiplataforma
  y atómico también en Windows si origen/destino están en el mismo volumen).
- **Comprobaciones añadidas**: se valida que la carpeta origen exista antes
  de lanzar la sincronización; varios errores que antes interrumpían un
  hilo en silencio ahora producen un mensaje claro en el log.
- **Código muerto/duplicado eliminado**: los métodos vacíos
  (`comprobar_checkpoint_duplicados_existente`, `guardar_checkpoint_duplicados`)
  y la duplicación masiva del bloque "actualizar_idioma_ui" (30 líneas de
  `if hasattr(...): widget.configure(...)`) se reemplazaron por un bucle
  sobre un diccionario de mapeo.
- **Tipado y docstrings**: type hints en firmas públicas y docstrings
  explicando el propósito de cada módulo/clase, para facilitar el
  mantenimiento futuro.

## Uso

```bash
pip install -r requirements.txt
python main.py
```

## Notas

- El paquete fue probado con un test de humo no interactivo (creación de
  archivos, sincronización con renombrado dinámico, detección de
  duplicados y persistencia de configuración) que se ejecutó con éxito.
  No se pudo probar el lanzamiento real de la ventana CustomTkinter en
  este entorno por no tener entorno gráfico/red, pero el código de la
  interfaz es una traducción 1:1 (refactorizada) de los widgets del
  script original, que sí dependía de un entorno con pantalla.
