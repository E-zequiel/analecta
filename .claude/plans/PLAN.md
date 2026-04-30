# Plan: Analecta — Descomposición del Proyecto

## Context

El usuario quiere construir **Analecta**: una app de escritorio local que toma URLs como input, extrae su contenido, lo convierte a Markdown limpio, descarga assets (imágenes), y lo almacena en un vault local. Fusiona la funcionalidad de Wallabag (extracción web), Obsidian (visor/editor Markdown, URL scheme), y Logseq (sistema de hashtags como nodos, `template::`). No es una extensión de navegador. Es estrictamente local, con mínima superficie de ataque.

- **Nombre**: Analecta (latín: "fragmentos recogidos, selecciones literarias")
- **GUI**: PySide6 (LGPL, Qt nativo, Wayland first-class, QWebEngineView para Markdown)
- **Lang**: Python 3 principal; Rust si algún módulo requiere performance crítica
- **Herramientas**: `uv`, `mise`, `pytest`, SQLite
- **Theme**: Tokyo Night (obsidian-tokyonight → QSS)
- **Tipografía**: JetBrains Mono (regular + Nerd Font opcional)

---

## Módulos (orden de desarrollo)

### M1 — Bootstrap del Proyecto
**Archivos críticos**: `pyproject.toml`, `.mise.toml`, `src/analecta/__init__.py`, `src/analecta/config.py`

- `uv init` con estructura `src/` layout
- `.mise.toml` con Python version
- Config system: TOML (`~/.config/analecta/config.toml`) + Pydantic para validación
  - `vault_path`, `font_variant`, `update_channel`
  - **`virustotal_api_key` NO va en config.toml**: se almacena en el keyring del sistema vía `keyring`
- Logging estructurado (stdlib `logging` + handler para UI)
- `__main__.py` como entry point

### M2 — Pipeline de Extracción
**Archivos críticos**: `src/analecta/extraction/core.py`, `article.py`, `youtube.py`, `social.py`

- Detección de tipo de fuente (artículo web, YouTube, Substack, X/Twitter)
- **Artículos**: `trafilatura` como extractor principal; `readability-lxml` como fallback
- **JS-heavy sites**: `playwright` (headless Chromium), activado opcionalmente o por detección de fallo
- **YouTube**: `youtube-transcript-api`; notificación si no existe transcripción
- **Substack**: RSS + HTML adapter
- **X**: `NotImplementedError` — Nitter está obsoleto; no hay vía de extracción viable sin API key. Registrar la URL y retornar error informativo.
- Interfaz común: `SourceExtractor` → retorna `ExtractedContent(title, html, url, source_type, metadata)`

### M3 — Pipeline de Assets
**Archivos críticos**: `src/analecta/extraction/assets.py`

- Descubrimiento de imágenes en HTML extraído
- Descarga asíncrona con `httpx`
- Estructura: `{vault}/assets/{slug}/{sha256[:16]}.{ext}` (content-addressed, no secuencial)
- Embedding Logseq-style: `![{original_name}](../assets/{slug}/{sha256[:16]}.{ext})`
- Fallback: mantener URL original si descarga falla (crítico para gráficos)

### M4 — Motor Markdown
**Archivos críticos**: `src/analecta/markdown/converter.py`, `frontmatter.py`, `hashtags.py`

- `markdownify` (HTML→MD) con configuración fine-tuned
- YAML frontmatter: `title`, `url`, `source_type`, `created_at`, `tags: []`, `status: unread`
- Normalización de hashtags: snake_case, sin espacios, sufijo de línea (nunca inicio de línea)
- Validación anti-"hashtag hashtag" (no `##Título` sin espacio)
- `template::` block generator para entradas nuevas
- Naming de archivos: `YYYY-MM-DD-{slug}.md`

### M5 — Capa de Almacenamiento
**Archivos críticos**: `src/analecta/storage/vault.py`, `index.py`

- Vault: `{vault_path}/pages/`, `{vault_path}/assets/`
- SQLite schema:
  ```sql
  entries(id, title, url, file_path, source_type, created_at, updated_at, status, tags_json)
  tags(id, name, count)
  entry_tags(entry_id, tag_id)
  ```
- FTS5 virtual table para búsqueda full-text
- CRUD: `add_entry`, `update_status`, `update_tags`, `soft_delete`, `search`
- Migraciones manuales versionadas (`migrations/001_init.sql`, etc.)

### M6 — Capa PKM
**Archivos críticos**: `src/analecta/pkm/tags.py`, `templates.py`, `url_scheme.py`

- Tag registry: grafo de co-ocurrencia entre tags
- Backlinks: índice invertido (tag → lista de entries)
- **analecta:// URL scheme**:
  - Registro via `.desktop` file + `xdg-mime` en instalación
  - Handler: `analecta://open?id={entry_id}` → abre entry en la app
  - Generador de URL por entry (para copiar al portapapeles → pegar en Logseq/Obsidian)
- "Abrir en file manager": `QDesktopServices.openUrl(QUrl.fromLocalFile(dir_path))`

### M7 — Módulo de Seguridad
**Archivos críticos**: `src/analecta/security/virustotal.py`

- VirusTotal API v3, activación opcional desde config
- Flow: recibir URL → `POST /urls` → poll resultado → parsear veredicto
- Si hay detecciones: modal con detalles + opción continuar/abortar
- Si no hay detecciones: continuar silenciosamente (con notificación opcional)
- API key cifrada en el keyring del sistema (`keyring` library)

### M8 — UI: Shell Central
**Archivos críticos**: `src/analecta/ui/theme.py`, `fonts.py`, `main_window.py`

- `QApplication` + `QMainWindow`
- Tokyo Night QSS: extraer paleta de obsidian-tokyonight (`#1a1b26` bg, `#c0caf5` fg, `#7aa2f7` accent, etc.)
- `QFontDatabase.addApplicationFont()` para JetBrains Mono bundleada
- Layout: `QSplitter(sidebar | content_area)`
- `QStackedWidget` para navegar entre dashboard / viewer / editor / settings

### M9 — UI: Dashboard
**Archivos críticos**: `src/analecta/ui/dashboard.py`

- `QListView` + `QAbstractListModel` custom (entries del vault)
- Filter bar: botones para `all / unread / read / favorite / deleted / to_recommend`
- Sidebar de hashtags: `QTreeWidget` con conteo
- Barra de búsqueda: FTS5 → actualiza modelo en tiempo real
- Preview card al hover/selección

### M10 — UI: Visor de Artículos
**Archivos críticos**: `src/analecta/ui/viewer.py`

- `QWebEngineView`: render Markdown→HTML con `markdown-it-py` + extensiones
- Modo read-only por defecto (interceptar eventos de edición)
- Botón de desbloqueo → activa editor
- Toolbar de acciones:
  - Copiar `analecta://` URL
  - Abrir en navegador (`QDesktopServices`)
  - Abrir en file manager
  - Scan VirusTotal (si configurado)
  - Favorito / Leído / Recomendar (toggle)

### M11 — UI: Editor
**Archivos críticos**: `src/analecta/ui/editor.py`

- `QPlainTextEdit` con JetBrains Mono, syntax highlighting básico para MD
- Preview live toggle (split view: editor | QWebEngineView)
- Guardar: escribe `.md` + actualiza SQLite index + recalcula tags
- Revertir: restaura contenido desde disco

### M12 — UI: Configuración
**Archivos críticos**: `src/analecta/ui/settings.py`

- Vault path: `QFileDialog.getExistingDirectory`
- VirusTotal API key: `QLineEdit` con echo mode Password → guardar en keyring
- Font: JetBrains Mono regular / Nerd Font
- Update channel: stable / dev

### M13 — System Tray
**Archivos críticos**: `src/analecta/ui/tray.py`

- `QSystemTrayIcon` con icono Tokyo Night
- Menú: "Añadir URL desde portapapeles", "Abrir Analecta", "Salir"
- Notificación al completar extracción (éxito/error)
- Opción de arranque con el sistema (`.desktop` autostart)

### M14 — Sistema de Actualización
**Archivos críticos**: `src/analecta/updater/checker.py`

- Mecanismo: `uv tool upgrade analecta` (app distribuida vía PyPI o índice privado)
- Check de versión al arranque: comparar `__version__` con latest en PyPI/GitHub releases
- Modal "nueva versión disponible" con changelog + botón "Actualizar"
- Actualización ejecuta: `subprocess.run(["uv", "tool", "upgrade", "analecta"])`
- Reinicio automático opcional post-update

---

## Custom Skills (`.claude/commands/`)

Cuatro comandos para desarrollo granular de Analecta:

### `/fetch` → `fetch.md`
Toma una URL del contexto/argumento, ejecuta el pipeline de extracción completo (M2+M3+M4) y muestra el Markdown resultante. Útil para testear extractores y ver el output antes de guardarlo.

### `/dev` → `dev.md`
Lanza Analecta en modo desarrollo:
```
uv run python -m analecta --dev --vault /tmp/analecta-dev-vault
```
Con un vault temporal pre-cargado con entries de ejemplo.

### `/test` → `test.md`
Ejecuta la suite completa con cobertura:
```
uv run pytest tests/ -v --cov=analecta --cov-report=term-missing
```
Reporta los módulos con cobertura < 80%.

### `/build` → `build.md`
Genera distributable con `uv build` (wheel + sdist). Corre tests de humo y verifica que el entry point funcione. El empaquetado standalone (PyInstaller) **no está definido aún — no implementar sin consultar**.

---

## Orden de desarrollo recomendado

```
M1 → M5 → M2 → M3 → M4 → M6 → M7 → M8 → M9 → M10 → M11 → M12 → M13 → M14
 │         │
 └── Skills: /fetch, /dev, /test al terminar M1 ──────────────── /build al terminar M14
```

La prioridad es tener el pipeline de extracción + almacenamiento funcionando antes de tocar la UI.

---

## Verificación end-to-end

1. `uv run python -m analecta` lanza la ventana sin errores
2. Ingresar URL de artículo → extrae, descarga imágenes, guarda `.md` en vault
3. Ingresar URL de YouTube → extrae transcripción (o notifica si no existe)
4. Dashboard muestra la entry nueva con título, fecha, tags
5. Click en entry → QWebEngineView renderiza el Markdown con imágenes embebidas
6. Copiar analecta:// URL → pegar en terminal: `xdg-open analecta://open?id=1` → abre app en esa entry
7. `uv run pytest tests/ -v` pasa todos los tests

