# CopyFiles Smart Organizer v18.3 - Refactored Edition

## Overview
A professional-grade file management suite with intelligent file organization, duplicate detection, and advanced configuration options. This refactored version implements:

- **Thread-Safe Architecture**: Queue-based communication between threads
- **Clean Separation of Concerns**: Business logic independent from UI
- **Type Safety**: Full type hints throughout
- **Persistent Storage**: Configuration, checkpoints, and operation history
- **Multi-Language Support**: Spanish and English
- **Advanced Themes**: 15+ predefined color schemes

## Features

### 1. Synchronization Engine
- Copy or move files with dynamic renaming
- Pattern-based organization by date, type, custom patterns
- Regex-based file transformations
- Atomic copy operations for data integrity
- Checkpoint system to resume interrupted operations
- Dry-run mode for simulation
- File filtering by type, size, and regex patterns
- Collision resolution (rename with timestamp or skip)

### 2. Duplicate Finder
- MD5 hash-based detection
- Multi-threaded analysis across drives
- Size and name-based filtering
- SQLite caching for performance
- Visual tree interface with action planning
- Disk space savings estimation
- Safe deletion to recycle bin or permanent removal

### 3. Advanced Configuration
- Language switching (ES/EN)
- 15+ color themes with custom hex support
- Dynamic file naming with token injection
- Regex-based file name cleanup
- Size range filtering
- Exclusion patterns
- Operation history auditing

## Project Structure

```
copyfiles-refactored/
├── config.py           # Configuration management & validation
├── localization.py     # Multi-language support
├── logger_setup.py     # Logging configuration
├── models.py          # Business logic (100% UI-independent)
├── ui_utils.py        # Thread-safe queue & UI utilities
├── ui.py              # Main application UI
└── requirements.txt   # Dependencies
```

## Installation

### Prerequisites
- Python 3.8+
- pip

### Setup

```bash
# Clone repository
git clone https://github.com/mussra/Google2Amazon.git
cd Google2Amazon

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run application
python ui.py
```

## Dependencies

```
customtkinter>=5.0.0
send2trash>=1.8.0
```

## Architecture Highlights

### Thread Safety
- Queue-based communication prevents UI freezing
- All background operations run in separate threads
- Main thread polls queue for updates (100ms interval)
- No direct UI manipulation from worker threads

### Business Logic Isolation
- `SyncEngine` and `DuplicatesFinder` have zero UI dependencies
- Can be used in CLI, API, or other interfaces
- Full event-driven API via `SyncEvent` dataclass
- Easy to test without mocking UI components

### Data Persistence
- Configuration: `~/.copyfiles/config.json`
- Checkpoints: `~/.copyfiles/checkpoint_*.json`
- History: `~/.copyfiles/history.json`
- Hash Cache: `~/.copyfiles/hashes.db` (SQLite)
- Logs: `~/.copyfiles/copyfiles.log` (rotating, 10MB max)

## Usage

### Basic Synchronization
1. Select source folder ("Origen")
2. Select destination folder ("Destino")
3. Choose file types to process
4. Select Copy or Move mode
5. Click "Lanzar Servicio" to start

### Finding & Cleaning Duplicates
1. Add directories to scan
2. Configure filters (size, name)
3. Click "Analizar Duplicados"
4. Review detected duplicates
5. Click items to toggle keep/delete
6. Click "Procesar Limpieza" to execute

### Advanced Configuration
- **Dynamic Naming**: Use tokens like `{año}/{mes}/{dia}/{nombre_origen}_{secuencial}`
- **Regex Cleanup**: Transform file names with find/replace patterns
- **Size Filtering**: Set minimum/maximum file sizes to process
- **Exclusion Patterns**: Regex pattern to skip certain files
- **Collision Handling**: Auto-rename or skip existing files
- **Themes**: Choose from 15 predefined color schemes

## Configuration Keys

Stored in `~/.copyfiles/config.json`:

```json
{
  "origen": "/path/to/source",
  "destino": "/path/to/destination",
  "chk_fotos": true,
  "chk_videos": false,
  "chk_docs": false,
  "chk_otros": false,
  "modo_mover": false,
  "dry_run": false,
  "copia_atomica": true,
  "colision": "renombrar",
  "patron_renombrado": "{año}/{mes}/{dia}/ARCHIVO_{secuencial}",
  "regex_excluir": "(?i)\\.(tmp|bak|ds_store|thumbs\\.db)$",
  "accion_duplicados": "Enviar a la Papelera (Seguro)",
  "idioma": "es",
  "modo_apariencia": "System"
}
```

## File Types

### Photos
`.jpg, .jpeg, .png, .gif, .bmp, .heic, .tiff, .webp`

### Videos
`.mp4, .mkv, .avi, .mov, .wmv, .flv, .webm, .m4v`

### Documents
`.pdf, .docx, .doc, .xlsx, .xls, .pptx, .txt, .odt, .csv`

## Token Reference

Use in naming patterns:

| Token | Example | Description |
|-------|---------|-------------|
| `{año}` | 2024 | File year |
| `{mes}` | 06 | File month |
| `{dia}` | 14 | File day |
| `{nombre_origen}` | photo | Original file name |
| `{secuencial}` | 0001 | Sequential number |
| `{usuario}` | john | System username |
| `{equipo}` | laptop | Computer name |
| `{directorio}` | Pictures | Parent directory |

## Troubleshooting

### Issue: "Origin and destination cannot be the same"
**Solution**: Ensure source and destination paths are different directories.

### Issue: Permission denied when deleting duplicates
**Solution**: Run with administrator privileges or check file permissions.

### Issue: Hash cache is stale
**Solution**: Click "Descartar Cache" to clear SQLite cache database.

### Issue: UI is freezing
**Solution**: This should not happen with refactored queue-based architecture. Report if it does.

## Performance Tips

1. **Use atomic copy**: Enable "Copia Atómica Segura (.tmp)" for data integrity
2. **Cache hashes**: Enable "usar_cache" for faster duplicate detection
3. **Filter files**: Use size ranges and regex patterns to reduce scope
4. **Dry run first**: Test with "Modo Simulación (Dry Run)" before real operation
5. **Adjust threads**: Use 4-8 threads for optimal duplicate scanning

## Development

### Adding New Features

Business logic additions go in `models.py`:
```python
class NewFeatureEngine:
    def __init__(self, config: AppConfig):
        self.config = config
    
    def process(self) -> Iterator[SyncEvent]:
        # Yield events
        yield SyncEvent(EventType.SUCCESS, "Processing...")
```

UI updates go in `ui.py` and use the queue:
```python
self.ui_queue.put(UIMessageType.LOG, "User action performed")
```

### Testing Business Logic

No UI dependencies means clean testing:
```python
from models import SyncEngine
from config import AppConfig

config = AppConfig(origen="/src", destino="/dst")
engine = SyncEngine(config)

for event in engine.sync():
    print(f"{event.type}: {event.message}")
```

## License

MIT License - See LICENSE file for details

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Submit a pull request

## Support

For issues, questions, or suggestions, please open a GitHub issue.

## Changelog

### v18.3 - Refactored Edition
- ✅ Thread-safe queue-based architecture
- ✅ Complete separation of business logic from UI
- ✅ Full type hints and docstrings
- ✅ Centralized logging with rotation
- ✅ Configuration validation using dataclasses
- ✅ Improved error handling throughout
- ✅ SQLite connection pooling for hash caching
- ✅ Clean modular structure
