# Migración a arquitectura híbrida — Tauri 2.0 + sidecar Python

## Context

Analecta hoy es una app de escritorio Python pura: PySide6 + qasync, SQLite, distribuida como wheel/`uv tool`. Tras M1–M14 + post-M14 polish, todo está cerrado en `main` con 386 tests verdes.

El próximo salto es arquitectónico: **migrar a un modelo híbrido Tauri 2.0 (shell Rust + WebKitGTK) + sidecar Python (FastAPI) + frontend SvelteKit** según `docs/professional-hybrid-architecture.md`. El objetivo final es:

- Bundle nativo con tamaño reducido (`.deb` / `.rpm` / `.AppImage`) en lugar de `pip install`.
- UI moderna en HTML/CSS con tema Tokyo Night y CodeMirror 6 para edición.
- Lógica Python intacta detrás de una API REST local con lifecycle gestionado por Tauri.
- Camino claro a Windows/macOS posterior (Linux first).

La migración se desarrolla en `feat/hybrid-architecture` con estrategia **big-bang**: PySide6 sigue intacto en `main` hasta que el bundle alcanza paridad y mergeamos. No hay capas intermedias temporales.

---

## Decisiones consolidadas

| Eje | Decisión | Notas |
|-----|----------|-------|
| Frontend | **SvelteKit + TypeScript + Vite** | Bundle chico, reactivo, encaja con la filosofía minimalista |
| Render Markdown | **Cliente** con `markdown-it` + plugins | Cero round-trip; preview instantánea |
| Editor | **CodeMirror 6** + `@uiw/codemirror-theme-tokyo-night` | Soporte Markdown + Vi opcional |
| Layout | `backend/` + `frontend/` + `src-tauri/` (estándar Tauri) | Mover `src/analecta/` → `backend/src/analecta/` |
| Empaquetado sidecar | **PyInstaller `--onedir`** | Consenso comunidad; evita problema de PID con `--onefile` |
| IPC | stdin/stdout para lifecycle, HTTP loopback para datos, **SSE** para push backend→UI | FastAPI nativo, sin overhead de WebSocket |
| Puerto sidecar | Dinámico (`socket.bind(("", 0))`) → `LISTENING_ON_PORT:<n>` por stdout → Tauri emite evento `sidecar-ready` al frontend | Evita choques con otras apps |
| DB | `sqlite3` síncrono envuelto en `asyncio.to_thread()` | Menos invasivo que migrar a `aiosqlite`; el código actual se preserva |
| Python sidecar | **3.13** (no 3.14) | PyInstaller 6.x soporta hasta 3.13 estable; 3.14 aún experimental |
| Distribución | **Sólo Tauri bundle** + `tauri-plugin-updater` | Drop completo de PyPI/`uv tool`; un único canal firmable |
| Migración | Big-bang en `feat/hybrid-architecture` | Cutover al alcanzar paridad funcional |

---

## Riesgos y observaciones detectados

1. **`CLAUDE.md` actual** prohíbe el binario standalone ("Stop and ask"). El bloque `A2` rescribe esa sección y elimina las reglas PySide6.
2. **Tray en Wayland/GNOME** (Pop!_OS 24.04): el `StatusNotifierItem` que usa `tauri-plugin-tray` no es nativo en GNOME — requiere la extensión `AppIndicator and KStatusNotifierItem Support`. Documentar en README como dependencia para usuarios GNOME (KDE/i3/Sway funcionan out-of-the-box).
3. **PyInstaller + Python 3.14**: incompatibilidad parcial. Bajamos el sidecar a 3.13. El resto del proyecto puede mantener 3.14 si fuera necesario, pero por simplicidad uniformamos a 3.13.
4. **Updater dual**: descartado. `updater/checker.py` se elimina; `tauri-plugin-updater` cubre todo el ciclo.
5. **Tests UI PySide6** (`test_ui_*.py`, ~1500 líneas): obsoletos tras cutover. Se reemplazan por tests FastAPI + 1 smoke E2E.
6. **Memoria `feedback_systemtray_teardown`**: queda obsoleta tras cutover (no más `QSystemTrayIcon`).
7. **`hashtags.py` y `pkm/url_scheme.py`** dependen de funciones puras Python — se preservan tal cual y se exponen vía API.

---

## Estructura final del repo

```
analecta/
├── backend/                         # Sidecar Python
│   ├── src/analecta/                # ← código Python actual movido aquí
│   │   ├── api/                     # NUEVO: rutas FastAPI
│   │   │   ├── __init__.py
│   │   │   ├── deps.py              # singletons (VaultIndex, AppConfig)
│   │   │   ├── events.py            # bus interno → SSE
│   │   │   └── routes/
│   │   │       ├── entries.py
│   │   │       ├── search.py
│   │   │       ├── tags.py
│   │   │       ├── extract.py
│   │   │       ├── config.py
│   │   │       ├── security.py
│   │   │       ├── system.py        # health, version, sse stream
│   │   │       └── pkm.py           # url-scheme parse, templates
│   │   ├── server.py                # NUEVO: FastAPI + uvicorn entrypoint
│   │   ├── config.py                # SE PRESERVA
│   │   ├── extraction/, markdown/, storage/, pkm/, security/   ← intactos
│   │   ├── ui/                      # SE ELIMINA en G2
│   │   ├── updater/                 # SE ELIMINA en G4
│   │   └── __main__.py              # SE REDUCE: sólo lanza server.py
│   ├── tests/                       # tests actuales no-UI + nuevos de API
│   ├── pyproject.toml               # deps backend
│   ├── backend.spec                 # PyInstaller
│   └── .python-version              # 3.13
├── frontend/                        # SvelteKit
│   ├── src/
│   │   ├── lib/
│   │   │   ├── api/                 # cliente HTTP tipado
│   │   │   ├── stores/              # Svelte stores (config, entries, port)
│   │   │   ├── markdown/            # markdown-it config + Tokyo Night CSS
│   │   │   ├── components/
│   │   │   └── theme/               # palette + JetBrains Mono
│   │   ├── routes/
│   │   │   ├── +layout.svelte       # shell sidebar+contenido
│   │   │   ├── +page.svelte         # dashboard
│   │   │   ├── viewer/[id]/+page.svelte
│   │   │   ├── editor/[id]/+page.svelte
│   │   │   ├── settings/+page.svelte
│   │   │   └── first-run/+page.svelte
│   │   ├── app.html
│   │   └── app.css
│   ├── static/fonts/                # JetBrainsMono.ttf bundleado
│   ├── package.json
│   ├── svelte.config.js             # adapter-static
│   ├── vite.config.ts
│   └── tsconfig.json
├── src-tauri/                       # Shell Rust
│   ├── src/
│   │   ├── lib.rs                   # setup + sidecar lifecycle
│   │   ├── main.rs
│   │   ├── sidecar.rs               # spawn + port capture
│   │   └── commands.rs              # comandos expuestos al frontend
│   ├── binaries/                    # gitignored (PyInstaller output renombrado)
│   ├── icons/
│   ├── capabilities/default.json
│   ├── tauri.conf.json
│   ├── Cargo.toml
│   └── build.rs
├── scripts/
│   ├── build_sidecar.py             # corre PyInstaller + rename target-triple
│   ├── dev.py                       # arranca sidecar standalone (sin Tauri)
│   └── system_deps.sh               # apt-get install para devs Linux
├── docs/                            # SE PRESERVA
├── migrations/                      # SE PRESERVA (consumida por VaultIndex)
├── .github/workflows/
│   ├── ci.yml                       # lint + tests por PR
│   └── release.yml                  # bundle multi-plataforma en tag
├── .mise.toml                       # Python 3.13 + Node lts + Rust stable
├── package.json                     # scripts raíz: tauri dev / build
├── CLAUDE.md                        # reescrito en A2
└── README.md
```

---

## Plan de migración por bloques

Cada bloque es atómico, secuencial, y termina con un criterio de verificación. Numeración `<Fase>.<Bloque>`. Total: **30 bloques en 7 fases**.

---

### Fase A — Foundation (preparación, sin código de producción)

#### A1. Restructure del repo
**Archivos:** todo `src/analecta/` → `backend/src/analecta/`, `tests/` → `backend/tests/`, `pyproject.toml` → `backend/pyproject.toml`. Crear `frontend/`, `src-tauri/`, `scripts/`, `.github/workflows/` vacíos. Mover `migrations/` a la raíz (queda como referencia compartida; consumido por `backend`).
**Acciones:** `git mv` sólo (preservar historia). Ajustar paths en `backend/pyproject.toml` (`[tool.pytest.ini_options]`, `[tool.coverage.run]`, `[project.scripts]`). Crear `package.json` raíz con scripts `tauri dev`, `tauri build`.
**Verificación:** `cd backend && mise exec -- uv run pytest -v` debe pasar idéntico a antes (386 tests verdes). Cero cambios funcionales.

#### A2. Reescribir `CLAUDE.md`
**Archivo:** `/CLAUDE.md`.
**Cambios:** eliminar regla "M1 → M5 → M2 → ... → M14" (obsoleta). Eliminar sección **UI Rules (PySide6)** entera. Eliminar prohibición de PyInstaller standalone (autorizado por esta tarea). Agregar sección **Hybrid Architecture** con: layout, IPC channels, sidecar lifecycle, distribución Tauri-only. Mantener: data contracts, naming conventions, security constraints, secret management. Agregar nota sobre extensión AppIndicator para GNOME.
**Verificación:** lectura del nuevo `CLAUDE.md` no debe contradecir ningún bloque posterior del plan.

#### A3. Ampliar `mise` con Node y Rust
**Archivo:** `.mise.toml`.
**Cambios:** agregar `node = "lts"` y `rust = "stable"` a `[tools]`. Mantener `python = "3.13"` (downgrade desde 3.14).
**Verificación:** `mise install` instala las 3 toolchains; `mise exec -- node --version`, `mise exec -- rustc --version`, `mise exec -- python --version` retornan correctamente.

#### A4. Documentar y instalar deps de sistema
**Archivo:** `scripts/system_deps.sh`.
**Cambios:** script idempotente que instala (Pop!_OS 24.04 / Ubuntu 22.04+):
```bash
sudo apt-get install -y \
  libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev \
  patchelf build-essential curl pkg-config libssl-dev
```
Incluir nota: GNOME requiere extensión `AppIndicator and KStatusNotifierItem Support`.
**Verificación:** ejecutar el script termina sin errores; `pkg-config --modversion webkit2gtk-4.1` retorna versión.

---

### Fase B — Backend FastAPI sidecar

#### B1. Dependencias y skeleton FastAPI
**Archivos:** `backend/pyproject.toml`, `backend/src/analecta/server.py`, `backend/src/analecta/api/__init__.py`.
**Cambios:** agregar deps: `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `sse-starlette`. Crear `server.py` con:
- `find_free_port()` (ver §5.2 del doc).
- `print(f"LISTENING_ON_PORT:{port}", flush=True)` antes de `uvicorn.run`.
- Handler `SIGTERM`/`SIGINT` que cierra limpio.
- `lifespan` async context manager con `print("SIDECAR_READY", flush=True)`.
- App FastAPI con CORS restringido a `http://tauri.localhost` y `http://localhost:*` en dev.
**Verificación:** `mise exec -- uv run python -m analecta` arranca, imprime `LISTENING_ON_PORT:<n>`, responde 200 en `GET /api/v1/system/health`.

#### B2. Inyección de dependencias y singletons
**Archivo:** `backend/src/analecta/api/deps.py`.
**Cambios:** `get_config()` → carga `AppConfig` desde `~/.config/analecta/config.toml` (reusa `config.load_config`). `get_index()` → singleton `VaultIndex(config.vault_path / "analecta.db")` con cleanup en `lifespan`. `get_vault()` → `VaultManager(config.vault_path)`. `get_event_bus()` → `asyncio.Queue` para SSE.
**Verificación:** unit test que pide `Depends(get_index)` desde una ruta dummy y obtiene la misma instancia en dos llamadas concurrentes.

#### B3. Rutas: entries + search + tags
**Archivos:** `backend/src/analecta/api/routes/{entries,search,tags}.py`.
**Endpoints:**
- `GET /api/v1/entries?status=&tag=&q=` → `index.list_entries()` / `search()` (delega según query).
- `GET /api/v1/entries/{id}` → `index.get_entry()`.
- `PATCH /api/v1/entries/{id}` body `{status?, tags?, fts?: {title,content}}`.
- `DELETE /api/v1/entries/{id}` → soft delete (status=`deleted`).
- `GET /api/v1/tags` → `index.list_tags()`.
**Patrón:** todas las llamadas SQLite síncronas envueltas en `asyncio.to_thread(lambda: ...)`.
**Pydantic models:** `EntryOut`, `EntryPatchIn`, `TagOut`. No reemplazar `EntryRecord` interno, sólo serializar.
**Verificación:** tests con `httpx.AsyncClient` + `LifespanManager`; cobertura de 4 status codes (200, 404, 422, 500).

#### B4. Rutas: extract pipeline
**Archivo:** `backend/src/analecta/api/routes/extract.py`.
**Endpoint:** `POST /api/v1/extract` body `{url}` → ejecuta el pipeline existente (`_process_url` portado desde `__main__.py:127-179`):
1. `extract(url)` → `ExtractedContent`.
2. `AssetDownloader().process(html, slug, vault_path)`.
3. `MarkdownConverter().convert(...)`.
4. `vault.write_page(...)`.
5. `index.add_entry(...)` + `update_fts_content(...)`.
6. `event_bus.put({"type": "entry_added", "id": entry_id})`.
7. Response: `EntryOut`.
**Manejo de errores:** `ExtractionError` → 422 con detalle; `IntegrityError` (URL duplicada) → 409.
**Verificación:** test con httpx mock + URL fixture; verificar que el archivo .md aparece en `tmp_path/pages/` y la fila en SQLite.

#### B5. Rutas: config + security + pkm + system
**Archivos:** `backend/src/analecta/api/routes/{config,security,system,pkm}.py`.
**Endpoints:**
- `GET /api/v1/config` → `AppConfig` actual.
- `PUT /api/v1/config` → valida y persiste vía `save_config()`.
- `GET /api/v1/security/virustotal/key/exists` → bool (no devuelve la key).
- `PUT /api/v1/security/virustotal/key` body `{value}` → `keyring.set_password()`.
- `POST /api/v1/security/virustotal/scan` body `{entry_id}` → corre `VirusTotalScanner.scan()` async, emite eventos progreso al bus.
- `GET /api/v1/system/health` → `{status: "ok", version, port}`.
- `GET /api/v1/system/events` (SSE) → stream del bus.
- `GET /api/v1/pkm/parse-url?url=analecta://...` → `{entry_id: int | null}`.
**Verificación:** todas las rutas retornan 200 con stub keyring (`keyring.set_keyring(MemoryKeyring())` en conftest).

#### B6. SSE event bus
**Archivo:** `backend/src/analecta/api/events.py`.
**Cambios:** `EventBus` con `asyncio.Queue` por suscriptor (multiplexado). Helper `await bus.publish(event)`. Endpoint SSE en `system.py` usa `sse_starlette.EventSourceResponse`. Eventos definidos: `entry_added`, `entry_updated`, `scan_progress`, `scan_completed`, `extraction_failed`.
**Verificación:** test que abre stream SSE con `httpx`, dispara `POST /extract` en otro task, y recibe `entry_added`.

#### B7. Adaptación de tests existentes
**Archivos:** `backend/tests/conftest.py`, `backend/tests/test_*.py`.
**Cambios:** centralizar fixtures en `conftest.py`: `tmp_vault`, `index`, `vault`, `app_config`, `client` (httpx async). Borrar todos los `test_ui_*.py` (los reemplazamos en G1+G6). Marcar como `@pytest.mark.skip` con TODO los tests que dependan de `qasync` (deberían ser cero tras inspección).
**Verificación:** `mise exec -- uv run pytest -v backend/tests --ignore=backend/tests/test_ui_*` pasa sin errores.

---

### Fase C — Tauri shell (Rust)

#### C1. Inicializar `src-tauri`
**Comando:** `cargo install tauri-cli@^2.0` (vía mise). `cd src-tauri && cargo init --lib`.
**Archivos:** `Cargo.toml` con deps: `tauri = { version = "2", features = ["protocol-asset"] }`, `tauri-plugin-shell`, `tauri-plugin-deep-link`, `tauri-plugin-single-instance`, `tauri-plugin-dialog`, `tauri-plugin-notification`, `tauri-plugin-clipboard-manager`, `tauri-plugin-autostart`, `tauri-plugin-opener`, `tauri-plugin-updater`, `tauri-plugin-keyring` (o stub que llame al sidecar).
**Verificación:** `cargo build` compila sin errores.

#### C2. `tauri.conf.json`
**Archivo:** `src-tauri/tauri.conf.json`.
**Contenido (puntos críticos):**
- `productName: "Analecta"`, `identifier: "io.analecta.desktop"`, `version` desde `package.json`.
- `build.beforeDevCommand: "npm run dev --workspace frontend"`.
- `build.beforeBuildCommand: "npm run build --workspace frontend && python scripts/build_sidecar.py"`.
- `build.devUrl: "http://localhost:5173"`, `build.frontendDist: "../frontend/build"`.
- `app.windows[0]`: 1280x800, title "Analecta", min 800x600.
- `app.security.csp`: `"default-src 'self'; connect-src 'self' http://localhost:* http://127.0.0.1:* ipc: http://ipc.localhost; img-src 'self' asset: data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self' asset:; script-src 'self'"`.
- `bundle.externalBin: ["binaries/analecta-sidecar"]`.
- `bundle.linux.deb.depends: ["libwebkit2gtk-4.1-0", "libgtk-3-0"]`.
- `bundle.linux.appimage.bundleMediaFramework: true`.
**Verificación:** `cargo tauri info` no reporta errores de schema.

#### C3. Capabilities
**Archivo:** `src-tauri/capabilities/default.json`.
**Permisos mínimos:** `core:default`, `shell:allow-spawn` (sólo `binaries/analecta-sidecar` sidecar=true), `shell:allow-execute` (idem), `dialog:allow-open`, `notification:default`, `clipboard-manager:allow-read-text`, `clipboard-manager:allow-write-text`, `opener:allow-open-url`, `opener:allow-open-path`, `deep-link:default`, `single-instance:default`, `autostart:allow-enable`, `autostart:allow-disable`, `autostart:allow-is-enabled`, `updater:default`.
**Verificación:** `cargo tauri build --no-bundle` compila.

#### C4. Lifecycle del sidecar en `lib.rs`
**Archivos:** `src-tauri/src/lib.rs`, `src-tauri/src/sidecar.rs`.
**Implementación:** seguir §9 del doc. `SidecarState(Mutex<Option<CommandChild>>)`, spawn en `setup`, capturar `LISTENING_ON_PORT:<n>` de stdout y emitir `sidecar-ready` con `{port}` al frontend. `on_window_event` con `CloseRequested` → `child.kill()`. Logs de stdout/stderr a `tracing` (no a stderr crudo).
**Verificación:** `cargo tauri dev` arranca, log muestra `[sidecar stdout] LISTENING_ON_PORT:N`, frontend (vacío por ahora) recibe el evento.

#### C5. Smoke test de integración
**Acciones:** desde DevTools del WebView (F12), correr `await fetch('http://localhost:' + port + '/api/v1/system/health')` y verificar 200.
**Verificación:** respuesta JSON `{status: "ok"}` visible en consola.

---

### Fase D — Frontend SvelteKit

#### D1. Scaffolding + theming base
**Comando:** `npm create svelte@latest frontend -- --template skeleton --types typescript`. Agregar deps: `markdown-it`, `markdown-it-task-lists`, `markdown-it-footnote`, `@codemirror/lang-markdown`, `@codemirror/state`, `@codemirror/view`, `@uiw/codemirror-theme-tokyo-night`, `@tauri-apps/api`, `@tauri-apps/plugin-shell` etc.
**Archivos:** `svelte.config.js` con `@sveltejs/adapter-static` (Tauri necesita output estático). `frontend/src/lib/theme/palette.ts` con constantes Tokyo Night exportadas como CSS vars en `app.css`. `frontend/static/fonts/JetBrainsMono-*.ttf` bundleadas; `@font-face` en `app.css`.
**Verificación:** `npm run build` produce `frontend/build/` estático con 0 errores TS.

#### D2. Bootstrap: esperar sidecar
**Archivo:** `frontend/src/lib/stores/sidecar.ts`.
**Cambios:** store `port = writable<number | null>(null)`. En `+layout.svelte` `onMount`, suscribirse a evento Tauri `sidecar-ready` con `listen('sidecar-ready', e => port.set(e.payload.port))`. Renderizar `<SidecarLoadingScreen />` mientras `port` es null. Si tras 10s sigue null, mostrar mensaje de error con stderr capturado.
**Verificación:** levantar `npm run tauri dev`; primera pantalla muestra spinner ~200ms y luego carga la dashboard vacía.

#### D3. API client tipado
**Archivo:** `frontend/src/lib/api/client.ts`.
**Cambios:** wrapper `apiFetch<T>(path, opts)` que prefija `http://localhost:${port}/api/v1`. Tipos `Entry`, `Tag`, `AppConfig`, `ExtractedContent` espejando los Pydantic del backend (mantener manual; no generar OpenAPI client por ahora). Helpers por recurso: `entries.list()`, `entries.get(id)`, `entries.patch(id, body)`, `tags.list()`, `extract.url(url)`, etc.
**Verificación:** ts-check pasa; llamada manual desde dashboard mock retorna entries.

#### D4. Layout shell + navegación
**Archivo:** `frontend/src/routes/+layout.svelte`.
**Cambios:** sidebar (260px) + slot principal. Sidebar: logo, nav items (Dashboard, Settings), tag tree (placeholder hasta D5).
**Verificación:** rutas `/`, `/settings/` navegan correctamente con SvelteKit router.

#### D5. Dashboard (paridad con `dashboard.py`)
**Archivos:** `frontend/src/routes/+page.svelte`, `frontend/src/lib/components/{EntryList,FilterBar,TagTree,SearchInput}.svelte`.
**Cambios:** SearchInput con debounce 300ms (`tick`); FilterBar con botones all/unread/read/favorite/recommend/deleted; TagTree consume `/api/v1/tags`; EntryList consume `/api/v1/entries?...`. Suscripción SSE: en `entry_added` → refetch lista. Click en entry → `goto('/viewer/' + id)`.
**Verificación:** dispatch manual `POST /extract` desde DevTools; la entry aparece en la lista sin reload.

#### D6. Viewer (paridad con `viewer.py`)
**Archivo:** `frontend/src/routes/viewer/[id]/+page.svelte`, `frontend/src/lib/markdown/renderer.ts`.
**Cambios:** carga el archivo `.md` vía `tauri-plugin-fs` (lectura directa al vault, no por HTTP — más rápido). Renderiza con `markdown-it` + plugins. CSS Tokyo Night en `frontend/src/lib/markdown/tokyo-night.css`. Toolbar: Back, Edit, Copy URL (`analecta://open?id=`), Open (browser), Files (file manager), VirusTotal (condicional), toggles Read/Favorite/Recommend → `PATCH /api/v1/entries/{id}`. Imágenes resueltas con base path `convertFileSrc()` de `@tauri-apps/api/core`.
**Verificación:** ver una entry existente; toggles persisten tras refresh; click en imagen del .md muestra el asset local.

#### D7. Editor (paridad con `editor.py`)
**Archivos:** `frontend/src/routes/editor/[id]/+page.svelte`, `frontend/src/lib/components/MarkdownEditor.svelte`.
**Cambios:** CodeMirror 6 con `markdown()` lang + `tokyoNight` theme. Toolbar: Back, Preview toggle (split view), Save (`Ctrl+S`), Revert. Save: escribe el archivo vía `tauri-plugin-fs` + `PATCH /api/v1/entries/{id}` con `{tags: extractHashtags(content), fts: {title, content}}`. Preview con debounce 400ms usando el mismo `markdown-it`.
**Verificación:** editar, save, refresh: contenido persiste; hashtags `#snake_case` actualizan tabla `tags`.

#### D8. Settings + first-run
**Archivos:** `frontend/src/routes/settings/+page.svelte`, `frontend/src/routes/first-run/+page.svelte`.
**Cambios:** form bindeado a `AppConfig`. Browse vault path → `tauri-plugin-dialog`. Toggle VirusTotal → modal disclaimer (CSP-compliant, no `alert()`). API key → `PUT /api/v1/security/virustotal/key`. Save → `PUT /api/v1/config`. First-run: si `GET /api/v1/config` retorna defaults sin override (heurística: vault no existe), redirect a `/first-run/`.
**Verificación:** primera ejecución arranca en `/first-run/`; tras submit, vault se crea y carga dashboard.

---

### Fase E — Integraciones OS vía plugins Tauri

#### E1. Tray
**Archivos:** `src-tauri/src/lib.rs` (extender), `src-tauri/Cargo.toml`.
**Cambios:** `TrayIconBuilder` con menú: "Add URL from clipboard", "Open Analecta", "Start with system" (toggle), "Quit". Iconos PNG generados con `tauri icon`. Double-click → emit `open-window` al frontend (que llama `WebviewWindow::show()`).
**Verificación:** ícono visible en KDE/i3; en GNOME requiere extensión AppIndicator (documentar en README).

#### E2. Notifications
**Cambios:** Comando Tauri `notify_success(title, body)` / `notify_error(title, body)` que el frontend invoca. Backend SSE → frontend → `tauri-plugin-notification`.
**Verificación:** dispatch `POST /extract` con URL inválida → notification de error visible en el OS.

#### E3. Clipboard
**Cambios:** "Add URL from clipboard" en tray → invoca comando Rust → lee clipboard → valida `^https?://` → `POST /api/v1/extract`. Copy URL del viewer → `tauri-plugin-clipboard-manager` desde el frontend.
**Verificación:** copiar URL externa, click en tray menu, entry se crea.

#### E4. Dialog (file picker)
**Cambios:** Settings vault path → `open({ directory: true })` de `tauri-plugin-dialog`.
**Verificación:** seleccionar carpeta, ruta aparece en input.

#### E5. Shell open
**Cambios:** Viewer "Open" / "Files" → `tauri-plugin-opener` con URL o file path.
**Verificación:** botones abren browser y file manager respectivamente.

#### E6. Keyring
**Decisión:** mantener acceso desde el sidecar Python (ya implementado, evita duplicar lógica en Rust). Frontend nunca toca keyring directo: pasa por `PUT /api/v1/security/virustotal/key`. Esto preserva compatibilidad con el código existente y la nota sobre `feedback_secret_naming`.
**Verificación:** Settings → guardar API key → `keyring get analecta VIRUSTOTAL_API_KEY` retorna el valor.

#### E7. Single-instance
**Cambios:** `tauri-plugin-single-instance` en `lib.rs`. Callback recibe `argv` de la 2da invocación → si trae `analecta://...`, parsea y envía evento `deep-link` al frontend de la instancia existente.
**Verificación:** abrir 2 veces consecutivas; la 2da no abre ventana, sólo enfoca la 1ra.

#### E8. Deep link `analecta://`
**Cambios:** `tauri-plugin-deep-link` registrado para esquema `analecta`. Al recibir URL, llama backend `GET /api/v1/pkm/parse-url?url=...` para obtener `entry_id`, luego frontend hace `goto('/viewer/' + entry_id)`. Registro del esquema en build (Tauri lo auto-genera en `.desktop`).
**Verificación:** `xdg-open analecta://open?id=1` abre la app en el viewer correcto.

#### E9. Autostart
**Cambios:** `tauri-plugin-autostart` con `launch_agent: true`. Toggle en tray menu llama `is_enabled()` / `enable()` / `disable()`. Reemplaza la escritura manual de `~/.config/autostart/analecta.desktop`.
**Verificación:** activar toggle, reiniciar sesión, app arranca minimizada al tray.

---

### Fase F — Build pipeline

#### F1. `backend.spec` para PyInstaller
**Archivo:** `backend/backend.spec`.
**Contenido:** ver §7 del doc. Ajustes Analecta:
- `hiddenimports` extra: `trafilatura`, `readability`, `markdownify`, `markdown_it`, `youtube_transcript_api`, `keyring.backends.SecretService`, `httpx`, `sse_starlette`.
- `datas`: `migrations/*.sql` → `analecta/migrations/`.
- `excludes`: `tkinter`, `matplotlib`, `IPython`, `PySide6` (defensivo).
- `name: "analecta-sidecar"`.
- `onedir` (no onefile).
**Verificación:** `cd backend && mise exec -- pyinstaller backend.spec --distpath ../src-tauri/binaries` produce `src-tauri/binaries/analecta-sidecar/analecta-sidecar` ejecutable que arranca y responde 200.

#### F2. `scripts/build_sidecar.py`
**Cambios:** orquesta: instalar deps en venv aislado del build (`uv pip install --target .build/...`), correr PyInstaller con el .spec, mover `dist/analecta-sidecar/` → `src-tauri/binaries/`, renombrar el ejecutable interno con `-<target-triple>` sufijo (`rustc --print host-tuple`). Idempotente: si los inputs no cambiaron (hash de `pyproject.toml` + `src/`), saltea.
**Verificación:** primera ejecución compila; segunda termina en <1s con mensaje "cached".

#### F3. Build local end-to-end
**Comando:** `npm run tauri build` desde la raíz.
**Verificación:** produce `src-tauri/target/release/bundle/{deb,appimage,rpm}/`. Instalar el `.deb`: `sudo apt install ./Analecta_*.deb`. Lanzar `analecta`: arranca, sidecar levanta, dashboard carga.

#### F4. CI: `release.yml`
**Archivo:** `.github/workflows/release.yml`.
**Cambios:** matriz inicial **sólo `ubuntu-22.04`** (Windows/macOS en fase posterior). Pasos: install system deps, setup mise, install backend deps con `uv`, install frontend deps con `npm ci`, run `python scripts/build_sidecar.py`, rename con target triple, `tauri-action@v1` con `--target x86_64-unknown-linux-gnu`. Trigger: tag `v*`. Output: GitHub Release draft con `.deb`/`.AppImage`/`.rpm`.
**Verificación:** crear tag de prueba `v0.2.0-alpha.1` en branch; workflow pasa verde y publica draft.

#### F5. CI: `ci.yml`
**Archivo:** `.github/workflows/ci.yml`.
**Cambios:** en cada PR: `uv run pytest backend/tests`, `npm run check` (svelte-check), `cargo check` en `src-tauri`. Sin build de bundle (lento).
**Verificación:** PR de prueba dispara los 3 jobs en paralelo, todos verdes en <5min.

#### F6. Tauri updater
**Archivos:** `src-tauri/tauri.conf.json` (sección `plugins.updater`), `frontend/src/lib/components/UpdateBanner.svelte`.
**Cambios:** configurar endpoint `https://github.com/E-zequiel/analecta/releases/latest/download/latest.json` (formato Tauri). Generar par de claves con `tauri signer generate` (clave pública en config, privada como GitHub secret `TAURI_SIGNING_PRIVATE_KEY`). Frontend: al arrancar, llamar `check()` del plugin; si hay update, mostrar banner persistente con botón "Install & restart".
**Verificación:** publicar release `v0.2.1`; cliente `v0.2.0` muestra banner y aplica update tras click.

---

### Fase G — Cutover

#### G1. Smoke E2E con Playwright (opcional, recomendado)
**Archivo:** `frontend/e2e/smoke.spec.ts`.
**Cambios:** Playwright en modo Tauri (`@tauri-apps/cli` expone WebDriver experimental). Test único: arrancar app, esperar dashboard, dispatch URL via `tauri-driver`, verificar entry visible.
**Verificación:** `npm run e2e` pasa.

#### G2. Eliminar `backend/src/analecta/ui/`
**Acción:** borrar el directorio completo. Eliminar imports residuales en `__main__.py` (queda sólo el shim que llama a `server.py`). Eliminar `qasync`, `PySide6`, `pytest-qt` de `backend/pyproject.toml`. Borrar `backend/tests/test_ui_*.py`.
**Verificación:** `mise exec -- uv run pytest backend/tests` pasa con todos los tests no-UI.

#### G3. Eliminar `backend/src/analecta/updater/`
**Acción:** borrar el directorio. El nuevo updater es 100% Tauri (F6).
**Verificación:** `grep -r "from analecta.updater" backend/` no devuelve resultados.

#### G4. Limpieza final de `__main__.py` y `config.py`
**Acción:** `__main__.py` reduce a `from analecta.server import main; main()`. Eliminar de `config.py` cualquier referencia a `font_variant` si la decidimos hardcodear en CSS (decisión a confirmar). `setup_logging()` se preserva pero el handler se reescribe para escribir a `analecta-sidecar.log` además del stdout (que Tauri captura).
**Verificación:** `mise exec -- uv run python -m analecta` arranca el sidecar standalone.

#### G5. Actualizar `MEMORY.md` (auto-memoria del agente)
**Acción:** marcar como obsoletas: `feedback_systemtray_teardown`, `project_state` (reescribir con el nuevo estado post-migración). Agregar nuevas memorias relevantes si surgen patrones.
**Verificación:** próxima sesión Claude carga contexto correcto.

#### G6. Regresión final + merge
**Acciones:**
1. Sesión completa de QA manual: instalar `.deb` desde cero, primera ejecución, agregar 5 entries variadas (article, youtube, substack), editar, etiquetar, scan VirusTotal, autostart, deep link, update simulado.
2. Si todo OK: `gh pr create` → review → merge a `main`.
3. Tag `v0.2.0` (major bump por arquitectura nueva).
**Verificación:** release público con bundle Linux funcional.

---

## Verificación end-to-end

Tras G6, una sesión limpia debe poder:

```bash
# Usuario nuevo en Pop!_OS 24.04
sudo dpkg -i Analecta_0.2.0_amd64.deb
sudo apt install -f  # resuelve libwebkit2gtk-4.1-0
analecta  # primera ejecución
# → first-run dialog → vault path → dashboard vacía
# → tray menu "Add URL from clipboard" con un artículo
# → entry aparece sin recargar (SSE)
# → click → viewer renderiza Markdown con imágenes locales
# → toggle "Read" persiste tras refresh
# → editor: agregar "#nuevo_tag" → save → tag aparece en sidebar
# → settings: activar VirusTotal con API key real → scan
# → cerrar ventana → tray persiste → reabrir desde tray
# → quit → cierre limpio (sin proceso huérfano: ps aux | grep analecta = vacío)
```

---

## Archivos críticos a tocar (referencia rápida)

| Categoría | Archivos |
|-----------|----------|
| Existentes a mover (A1) | todo `src/analecta/**`, `tests/**`, `pyproject.toml`, `migrations/**` |
| Existentes a preservar | `extraction/`, `markdown/`, `storage/`, `pkm/{tags,templates,url_scheme,hashtags}.py`, `security/virustotal.py`, `config.py` |
| Existentes a eliminar (G2-G3) | `ui/`, `updater/`, `test_ui_*.py` |
| Nuevos backend | `api/{deps,events}.py`, `api/routes/*.py`, `server.py`, `backend.spec` |
| Nuevos Tauri | `src-tauri/src/{lib,main,sidecar,commands}.rs`, `tauri.conf.json`, `capabilities/default.json` |
| Nuevos frontend | todo `frontend/src/**`, `package.json`, `vite.config.ts`, `svelte.config.js` |
| Nuevos infra | `scripts/{build_sidecar,dev,system_deps}.{py,sh}`, `.github/workflows/{ci,release}.yml`, `.mise.toml` (extendido), root `package.json` |
| Refactor | `CLAUDE.md` (A2), `MEMORY.md` (G5) |

---

## Funciones existentes a reusar (no reimplementar)

- `analecta.config.load_config()` / `save_config()` → `api/deps.py`.
- `analecta.config.setup_logging()` → `server.py` startup.
- `analecta.storage.index.VaultIndex` (todos los métodos: `list_entries`, `get_entry`, `add_entry`, `update_status`, `update_tags`, `update_fts_content`, `search`, `list_tags`) → llamados desde `api/routes/`.
- `analecta.storage.vault.VaultManager` → `api/routes/extract.py`.
- `analecta.extraction.core.extract` (dispatcher) → `api/routes/extract.py`.
- `analecta.extraction.assets.AssetDownloader.process()` → ídem.
- `analecta.markdown.converter.MarkdownConverter.convert()` → ídem.
- `analecta.pkm.url_scheme.parse_url()` → `api/routes/pkm.py`.
- `analecta.pkm.tags.*`, `analecta.pkm.hashtags.*`, `analecta.pkm.templates.*` → ídem.
- `analecta.security.virustotal.VirusTotalScanner` → `api/routes/security.py`.

El **pipeline completo** (`__main__.py:127-179`) se porta tal cual a `api/routes/extract.py` con el único cambio de envolver SQLite en `asyncio.to_thread()` y publicar al `EventBus` en lugar de llamar a `dashboard.refresh()`.
