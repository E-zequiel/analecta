# Apps de Escritorio con Tauri 2.0 + Python: Arquitectura Híbrida Profesional

> **Stack primario:** Tauri 2.0 · Python 3.12+ · FastAPI · PyInstaller · GitHub Actions  
> **Plataforma objetivo:** Linux (Wayland nativo) → Windows → macOS  
> **Fuente base:** _docs/hybrid-architecture.md_ + investigación actualizada (mayo 2026)

---

## Tabla de Contenidos

1. [1. Contexto: Por qué Tauri domina sobre las alternativas](#1.%20Contexto%20Por%20qué%20Tauri%20domina%20sobre%20las%20alternativas)
2. [2. Cómo funciona Tauri en Linux/Wayland](#2.%20Cómo%20funciona%20Tauri%20en%20Linux/Wayland)
3. [3. La arquitectura en tres capas](#3.%20La%20arquitectura%20en%20tres%20capas)
4. [4. El Sidecar Pattern: Python como proceso hijo](#4.%20El%20Sidecar%20Pattern%20Python%20como%20proceso%20hijo)
5. [5. Estrategias de IPC: elegir el canal correcto](#5.%20Estrategias%20de%20IPC%20elegir%20el%20canal%20correcto)
6. [6. Empaquetado del backend: PyInstaller vs Nuitka](#6.%20Empaquetado%20del%20backend%20PyInstaller%20vs%20Nuitka)
7. [7. Estructura de proyecto profesional](#7.%20Estructura%20de%20proyecto%20profesional)
8. [8. Configuración de Tauri 2.0](#8.%20Configuración%20de%20Tauri%202.0)
9. [9. Lifecycle del sidecar: inicio y cierre limpio](#9.%20Lifecycle%20del%20sidecar%20inicio%20y%20cierre%20limpio)
10. [10. Build pipeline multiplataforma con GitHub Actions](#10.%20Build%20pipeline%20multiplataforma%20con%20GitHub%20Actions)
11. [11. Seguridad: el sistema de Capabilities](#11.%20Seguridad%20el%20sistema%20de%20Capabilities)
12. [12. Problemas conocidos y sus soluciones](#12.%20Problemas%20conocidos%20y%20sus%20soluciones)
13. [Referencias y recursos](#Referencias%20y%20recursos)

---

## 1. Contexto: Por qué Tauri domina sobre las alternativas

El documento fuente presenta cuatro alternativas. La elección de **Tauri + Python sidecar** se justifica cuando los requisitos son:

- Producto de calidad comercial (no solo herramienta interna)
- Control total sobre la UI con tecnologías web modernas
- Lógica de negocio en Python (ML, procesamiento de datos, integración con ecosistema científico)
- Binario distribuible pequeño (< 20 MB antes de agregar el sidecar)
- Soporte real de Wayland sin XWayland

**Comparación rápida con las otras opciones:**

|Criterio|PyWebView|Flet|Eel|Tauri + Python|
|---|---|---|---|---|
|Binario final|Mediano|Grande (Flutter)|Depende del navegador|Pequeño + sidecar|
|Wayland nativo|Parcial|Sí (Flutter)|No|Sí|
|Control sobre UI|Total|Limitado a widgets|Total|Total|
|Distribución|.zip con Python|Instalador|Requiere Chrome|Instalador nativo|
|Complejidad|Baja|Muy baja|Muy baja|Alta|
|Seguridad|Básica|Básica|Mínima|Avanzada (sandboxing)|

La complejidad de Tauri no es un defecto: es el costo de obtener lo que las otras no pueden dar.

---

## 2. Cómo funciona Tauri en Linux/Wayland

### La pila de renderizado

Tauri no incluye un motor de renderizado. En cambio, **delega al WebView provisto por el sistema operativo**. Esta decisión es lo que hace que los binarios sean pequeños y que el comportamiento sea "nativo".

En Linux, la pila completa es:

```
Tu app Tauri
    └── wry  (crate de Rust, abstracción del WebView)
        └── WebKitGTK 4.1  (motor del sistema)
            └── GTK 3 / GLib
                └── Wayland compositor (ej. Mutter/GNOME, KWin/KDE)
```

**`wry`** (Web Rendering Library) es la crate de Rust desarrollada por el equipo de Tauri que actúa como adaptador entre Tauri y el WebView del sistema. En Linux, usa `WebViewExtUnix::new_gtk` con un contenedor `gtk::Fixed` para soportar simultáneamente X11 y Wayland. El soporte de Wayland es nativo: GTK 4.1 se comunica directamente con el compositor a través del protocolo Wayland, sin requerir la capa de compatibilidad XWayland.

**`tao`** es la crate que gestiona el event loop y las ventanas. Equivale a `winit` pero con soporte extendido para características del sistema operativo que Tauri necesita.

### Dependencias del sistema en Linux

Para compilar y distribuir una app con Tauri 2.0 en Linux, el sistema necesita:

```bash
# En Ubuntu 22.04+ / Debian
sudo apt-get install -y \
  libwebkit2gtk-4.1-dev \
  libappindicator3-dev \
  librsvg2-dev \
  patchelf \
  build-essential \
  curl
```

La dependencia crítica es `libwebkit2gtk-4.1-dev`. Tauri 2.0 requiere la versión 4.1 (que usa `libsoup3`) en lugar de la 4.0 (que usa `libsoup2`). Esta diferencia es importante porque el runtime de GNOME (Flatpak) usa 4.1, lo que habilita distribución vía Flatpak.

### Limitación importante: el WebView es el del sistema

A diferencia de Electron, que empaqueta Chromium completo, Tauri usa el WebKitGTK instalado en la máquina del usuario. Esto tiene consecuencias:

- La versión del motor varía entre distribuciones (Ubuntu 22.04 tiene una versión diferente que Fedora 40).
- Algunas APIs de JavaScript pueden comportarse diferente o no estar disponibles.
- WebRTC en WebKitGTK sigue siendo experimental y requiere compilación custom.

Para mitigar esto en producción: testear en las distribuciones objetivo y usar feature detection en el frontend.

---

## 3. La arquitectura en tres capas

La arquitectura correcta para esta stack tiene tres procesos distintos:

```
┌─────────────────────────────────────────────────────┐
│                  PROCESO TAURI (Rust)                │
│                                                      │
│  ┌──────────────────┐    ┌────────────────────────┐  │
│  │  WebView (GTK)   │◄──►│   Tauri Core / IPC     │  │
│  │  (Frontend JS)   │    │   (Commands, Events)   │  │
│  └──────────────────┘    └────────────┬───────────┘  │
│                                       │              │
│                              spawn / lifecycle       │
└───────────────────────────────────────┼──────────────┘
                                        │ stdin/stdout
                                        │ (control)
                              ┌─────────▼──────────────┐
                              │  PROCESO PYTHON         │
                              │  (Sidecar / FastAPI)    │
                              │                         │
                              │  localhost:8008         │◄── Frontend JS
                              │  (datos / API REST)     │    via HTTP
                              └─────────────────────────┘
```

**Dos canales de comunicación distintos con roles distintos:**

1. **stdin/stdout** entre Tauri y Python: usado exclusivamente para control de lifecycle (señales de inicio, shutdown). Es el canal "de gestión".
    
2. **HTTP a localhost** entre el frontend JS y Python: usado para todas las llamadas de datos y lógica de negocio. Es el canal "de datos".
    

**¿Por qué HTTP en lugar de solo IPC de Tauri?**

Podría usarse el sistema de `Commands` de Tauri (IPC directo entre JS y Rust) para todo, y desde Rust llamar a Python por stdin/stdout. Sin embargo, esto crea un cuello de botella en Rust. El patrón FastAPI + HTTP es preferible porque:

- FastAPI es asincrónico y puede manejar múltiples requests concurrentes de forma natural.
- El protocolo HTTP es estándar y facilita testing independiente del backend.
- El endpoint puede documentarse automáticamente con OpenAPI/Swagger.
- En desarrollo, el backend puede correrse de forma standalone para iterar rápido.

---

## 4. El Sidecar Pattern: Python como proceso hijo

### ¿Qué es un sidecar?

En terminología de Tauri, un **sidecar** es un binario externo que se empaqueta junto con la aplicación Tauri y es gestionado por ella como proceso hijo. El usuario final nunca instala Python: recibe un único instalador que contiene tanto la shell de Rust/WebKitGTK como el intérprete Python completo con todas sus dependencias.

### El problema del target triple

Tauri requiere que el nombre del binario sidecar incluya el **target triple** de la plataforma. El target triple es una cadena que identifica la arquitectura, el sistema operativo y la ABI:

```
<nombre-del-binario>-<target-triple>
```

Ejemplos:

```
fastapi-server-x86_64-unknown-linux-gnu      # Linux x86_64
fastapi-server-aarch64-apple-darwin          # macOS Apple Silicon
fastapi-server-x86_64-pc-windows-msvc.exe   # Windows x86_64
```

Para obtener el target triple del sistema actual:

```bash
# Rust 1.84+
rustc --print host-tuple

# Versiones anteriores (parsear salida de rustc -Vv)
rustc -Vv | grep host | cut -d' ' -f2
```

Este requisito existe porque Tauri compila para múltiples plataformas y necesita saber qué binario corresponde a cada una.

### Configuración en `tauri.conf.json`

```json
{
  "bundle": {
    "externalBin": ["binaries/fastapi-server"]
  }
}
```

La ruta es relativa a `src-tauri/`. Tauri buscará automáticamente `src-tauri/binaries/fastapi-server-<target-triple>` (sin extensión en Linux/macOS, con `.exe` en Windows).

---

## 5. Estrategias de IPC: elegir el canal correcto

Existen tres estrategias para comunicar el frontend con el sidecar Python. Cada una tiene trade-offs:

### 5.1 stdin/stdout (para comandos simples)

El proceso Python lee de `stdin` y escribe en `stdout`. Tauri gestiona el proceso con `Command::new_sidecar`.

**Cuándo usarlo:** para sidecars de vida corta que ejecutan una tarea y terminan (ej. procesar un CSV, ejecutar un script de análisis).

**Limitación crítica:** no puede manejar concurrencia real ni streaming de datos. Para cada operación, Tauri spawned un proceso nuevo.

### 5.2 HTTP a localhost (el patrón profesional)

El sidecar arranca un servidor FastAPI en un puerto local. El frontend hace requests HTTP a ese servidor directamente.

```
frontend (JS)  ──HTTP GET/POST──►  FastAPI (Python)  ──►  lógica de negocio
```

**Ventajas:**

- Concurrencia nativa con `async/await` en FastAPI.
- Soporte para Server-Sent Events (SSE) para streaming de respuestas.
- OpenAPI docs automáticos en desarrollo.
- Testing independiente con `httpx` o `curl`.

**El problema del puerto:** para evitar conflictos de puertos con otras apps, el sidecar puede arrancar en un puerto aleatorio y comunicarlo via stdout al proceso Tauri, que lo pasa al frontend.

```python
# main.py (sidecar Python)
import uvicorn
import socket
import sys

def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]

if __name__ == "__main__":
    port = find_free_port()
    # Comunicar el puerto a Tauri via stdout
    print(f"LISTENING_ON_PORT:{port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port)
```

### 5.3 Unix domain sockets (avanzado, solo Linux/macOS)

Para comunicación de muy alta frecuencia o baja latencia entre Rust y Python, los sockets Unix son la opción más rápida. No tienen overhead de TCP. Sin embargo, no son multiplataforma (Windows usa named pipes en su lugar), lo que complica la portabilidad.

**Recomendación:** usar HTTP para la comunicación de datos (patrón 5.2) y stdin/stdout para control de lifecycle (señales de inicio/shutdown). Es el balance entre simplicidad, portabilidad y rendimiento.

---

## 6. Empaquetado del backend: PyInstaller vs Nuitka

El backend Python debe convertirse en un binario autocontenido (sin que el usuario tenga Python instalado). Las dos herramientas principales son:

### PyInstaller

**Mecanismo:** es un _freezer_, no un compilador. Analiza el grafo de imports, empaqueta el intérprete CPython, todos los módulos y las shared libraries en un directorio o un único ejecutable. En runtime, extrae todo a un directorio temporal y luego ejecuta el código Python normal.

**Flags relevantes:**

```bash
# Modo one-folder (recomendado para sidecar de Tauri)
pyinstaller main.py --distpath src-tauri/binaries --name fastapi-server

# Modo one-file (más simple, pero con limitaciones importantes en Tauri)
pyinstaller -F main.py --distpath src-tauri/binaries --name fastapi-server
```

**Startup latency:** en modo `--onefile`, PyInstaller debe extraer todo el contenido a `/tmp` antes de arrancar. Esto introduce latencia de 1-5 segundos dependiendo del tamaño. Para servidores que deben arrancar rápido, el modo `--onedir` (carpeta en lugar de único archivo) es más rápido.

**Ventajas:**

- La herramienta más madura y con más soporte de la comunidad.
- Compatible con prácticamente todos los paquetes de PyPI.
- Build rápido (no recompila Python a C).
- Documentación extensa.

**Desventajas:**

- El rendimiento en runtime es idéntico a CPython (no hay optimización).
- Los binarios son detectados por antivirus como sospechosos (falsos positivos).
- Binarios más grandes (~94 MB para una app típica).

### Nuitka

**Mecanismo:** es un _compilador_. Transpila el código Python a C/C++ y lo compila a código máquina nativo usando GCC, Clang o MSVC. Requiere un toolchain de C instalado.

```bash
# Compilación para distribución standalone
python -m nuitka \
  --standalone \
  --output-dir=src-tauri/binaries \
  --output-filename=fastapi-server \
  main.py
```

**Ventajas:**

- Binarios más pequeños (~58 MB vs ~94 MB de PyInstaller).
- Startup time más rápido (elimina la extracción de PyInstaller).
- Mejoras de rendimiento de 2-4x en código CPU-bound.
- Mejor protección del código fuente (compilado a código máquina).

**Desventajas:**

- Build mucho más lento (minutos vs segundos de PyInstaller).
- Requiere un toolchain de C configurado en el sistema de build.
- Algunos paquetes con extensiones C complejas pueden necesitar configuración adicional.
- La curva de configuración es más pronunciada.

### Recomendación por caso de uso

|Escenario|Herramienta|
|---|---|
|Prototipo o herramienta interna|PyInstaller (`--onefile`)|
|Producto comercial con lógica CPU-bound|Nuitka|
|FastAPI server (startup frecuente)|PyInstaller (`--onedir`)|
|Protección de propiedad intelectual|Nuitka|
|CI/CD con builds rápidos|PyInstaller|

**Para el caso de Tauri + FastAPI, el consenso de la comunidad (2025-2026) es PyInstaller en modo `--onedir`**: es la opción con mejor soporte documentado, compatibilidad probada con FastAPI/uvicorn, y build reproducible en CI/CD.

---

## 7. Estructura de proyecto profesional

La estructura de directorios refleja la separación de responsabilidades del stack:

```
my-app/
│
├── frontend/                   # UI: React/Vue/Svelte + TypeScript
│   ├── src/
│   │   ├── routes/             # File-based routing (ej. TanStack Router)
│   │   ├── components/         # Componentes reutilizables
│   │   ├── hooks/              # Custom hooks (useApi, useStore, etc.)
│   │   └── api/                # Cliente HTTP tipado (generado de OpenAPI)
│   ├── package.json
│   └── vite.config.ts
│
├── backend/                    # Lógica de negocio en Python
│   ├── app/
│   │   ├── main.py             # Punto de entrada: FastAPI + uvicorn
│   │   ├── api/
│   │   │   ├── routes/         # Módulos de rutas (v1/data, v1/files, etc.)
│   │   │   └── dependencies.py # Dependencias de FastAPI (auth, db, etc.)
│   │   ├── core/
│   │   │   ├── config.py       # Settings con pydantic-settings
│   │   │   └── logging.py      # Configuración de logging
│   │   ├── services/           # Lógica de dominio (pandas, sklearn, etc.)
│   │   └── models/             # Modelos Pydantic (schemas de request/response)
│   ├── tests/                  # pytest
│   ├── pyproject.toml          # Dependencias (uv o poetry)
│   └── backend.spec            # Spec file de PyInstaller
│
├── src-tauri/                  # Shell de Rust (Tauri core)
│   ├── src/
│   │   ├── lib.rs              # Setup, lifecycle del sidecar
│   │   └── commands.rs         # Tauri Commands expuestos al frontend
│   ├── binaries/               # Sidecar compilado (gitignored)
│   │   └── .gitkeep
│   ├── icons/                  # Íconos de la app (generados con tauri icon)
│   ├── capabilities/
│   │   └── default.json        # Permisos del sistema de capabilities
│   ├── Cargo.toml
│   └── tauri.conf.json         # Configuración principal de Tauri
│
├── scripts/
│   ├── build_sidecar.py        # Automatiza la compilación del sidecar
│   └── rename_sidecar.py       # Renombra el binario con el target triple
│
├── .github/
│   └── workflows/
│       ├── ci.yml              # Build y tests en cada PR
│       └── release.yml         # Release a GitHub Releases
│
├── package.json                # Scripts raíz (tauri dev, tauri build)
└── README.md
```

### El archivo `backend.spec` (PyInstaller)

En lugar de pasar flags a PyInstaller en la CLI, un proyecto profesional usa un archivo `.spec`. Esto hace el build reproducible:

```python
# backend/backend.spec
# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["app/main.py"],
    pathex=[str(Path(".").resolve())],
    binaries=[],
    datas=[
        # Incluir archivos estáticos si los hay
        # ("app/static", "static"),
    ],
    hiddenimports=[
        # FastAPI y uvicorn suelen necesitar imports explícitos
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Excluir lo que no se usa para reducir tamaño
        "tkinter",
        "matplotlib",
        "IPython",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,        # modo --onedir
    name="fastapi-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                     # compresión UPX (opcional)
    console=True,                 # necesario para FastAPI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="fastapi-server",
)
```

---

## 8. Configuración de Tauri 2.0

### `tauri.conf.json` completo y anotado

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "MyApp",
  "version": "0.1.0",
  "identifier": "com.mycompany.myapp",
  "build": {
    "beforeDevCommand": "npm run dev",
    "beforeBuildCommand": "npm run build && python scripts/build_sidecar.py",
    "devUrl": "http://localhost:5173",
    "frontendDist": "../frontend/dist"
  },
  "app": {
    "windows": [
      {
        "label": "main",
        "title": "MyApp",
        "width": 1200,
        "height": 800,
        "resizable": true,
        "fullscreen": false
      }
    ],
    "security": {
      "csp": "default-src 'self'; connect-src 'self' http://localhost:8008; style-src 'self' 'unsafe-inline'"
    }
  },
  "bundle": {
    "active": true,
    "targets": "all",
    "icon": [
      "icons/32x32.png",
      "icons/128x128.png",
      "icons/128x128@2x.png",
      "icons/icon.icns",
      "icons/icon.ico"
    ],
    "externalBin": [
      "binaries/fastapi-server"
    ],
    "linux": {
      "deb": {
        "depends": ["libwebkit2gtk-4.1-0", "libgtk-3-0"]
      }
    }
  }
}
```

**Notas importantes:**

- **`beforeBuildCommand`:** ejecuta el script Python de compilación del sidecar antes de que Tauri construya el instalador. Esto garantiza que el binario esté actualizado.
- **`csp` (Content Security Policy):** limita las conexiones de red que puede hacer el WebView. El `connect-src` debe incluir `http://localhost:8008` para que el frontend pueda hablar con FastAPI.
- **`externalBin`:** el path es relativo a `src-tauri/` y sin el target triple (Tauri lo agrega automáticamente).

---

## 9. Lifecycle del sidecar: inicio y cierre limpio

Este es el aspecto más delicado de la arquitectura. Un cierre incorrecto del sidecar deja procesos huérfanos en la máquina del usuario.

### En `lib.rs`: gestión del sidecar

```rust
// src-tauri/src/lib.rs
use std::sync::Mutex;
use tauri::{AppHandle, Manager};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandChild;

// Estado global para almacenar el handle del proceso hijo
pub struct SidecarState(pub Mutex<Option<CommandChild>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarState(Mutex::new(None)))
        .setup(|app| {
            let handle = app.handle().clone();
            start_sidecar(handle)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Matar el sidecar cuando se cierra la ventana principal
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let state = window.state::<SidecarState>();
                let mut child_lock = state.0.lock().unwrap();
                if let Some(child) = child_lock.take() {
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

fn start_sidecar(app: AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let sidecar_command = app
        .shell()
        .sidecar("fastapi-server")
        .map_err(|e| format!("Failed to create sidecar: {}", e))?
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUTF8", "1");

    let (mut rx, child) = sidecar_command
        .spawn()
        .map_err(|e| format!("Failed to spawn sidecar: {}", e))?;

    // Guardar el handle del proceso hijo
    let state = app.state::<SidecarState>();
    *state.0.lock().unwrap() = Some(child);

    // Escuchar la salida del sidecar en un task asíncrono
    tauri::async_runtime::spawn(async move {
        use tauri_plugin_shell::process::CommandEvent;
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    let line = String::from_utf8_lossy(&bytes);
                    // El sidecar puede comunicar el puerto por stdout
                    if line.starts_with("LISTENING_ON_PORT:") {
                        let port = line.trim_start_matches("LISTENING_ON_PORT:").trim();
                        // Emitir el puerto al frontend
                        let _ = app.emit("sidecar-ready", port.to_string());
                    }
                    eprintln!("[sidecar stdout] {}", line);
                }
                CommandEvent::Stderr(bytes) => {
                    let line = String::from_utf8_lossy(&bytes);
                    eprintln!("[sidecar stderr] {}", line);
                }
                CommandEvent::Terminated(status) => {
                    eprintln!("[sidecar] terminated with status: {:?}", status);
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(())
}
```

### El problema del PID con PyInstaller `--onefile`

Este es uno de los _gotchas_ más documentados de esta arquitectura. Cuando se usa PyInstaller en modo `--onefile`, el ejecutable en realidad es un **bootloader** que:

1. Se extrae a `/tmp`.
2. Lanza un proceso hijo con el Python real.

Tauri conoce el PID del bootloader, no el del proceso Python real. Cuando llama a `child.kill()`, solo mata el bootloader, pero el servidor FastAPI puede quedar corriendo.

**Solución 1 (recomendada):** usar PyInstaller en modo `--onedir`. El ejecutable principal directamente es el proceso Python.

**Solución 2:** agregar un handler de shutdown en FastAPI que capture `SIGTERM` y cierre el servidor limpiamente:

```python
# app/main.py
import signal
import sys
import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager

app = FastAPI()

def shutdown_handler(signum, frame):
    """Graceful shutdown cuando Tauri mata el proceso."""
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("SIDECAR_READY", flush=True)
    yield
    # Shutdown

app = FastAPI(lifespan=lifespan)
```

---

## 10. Build pipeline multiplataforma con GitHub Actions

Dado que Tauri no soporta compilación cruzada (no se puede compilar el binario de Windows desde Linux), el enfoque estándar es usar runners de cada plataforma en paralelo.

### El desafío con el sidecar Python

El workflow debe compilar el sidecar Python **en cada plataforma**, porque PyInstaller genera binarios específicos de la plataforma. El `build_sidecar.py` debe ejecutarse en el runner de Linux, el de macOS y el de Windows respectivamente.

### `release.yml` completo y funcional

```yaml
name: Release

on:
  push:
    tags:
      - "v*"

concurrency:
  group: release-${{ github.ref }}
  cancel-in-progress: true

jobs:
  release:
    permissions:
      contents: write
    strategy:
      fail-fast: false
      matrix:
        include:
          - platform: ubuntu-22.04
            rust_target: x86_64-unknown-linux-gnu
            python_arch: x86_64
          - platform: macos-latest
            rust_target: aarch64-apple-darwin
            python_arch: arm64
          - platform: macos-latest
            rust_target: x86_64-apple-darwin
            python_arch: x86_64
          - platform: windows-latest
            rust_target: x86_64-pc-windows-msvc
            python_arch: x64

    runs-on: ${{ matrix.platform }}

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      # --- Dependencias del sistema (solo Linux) ---
      - name: Install Linux system deps
        if: matrix.platform == 'ubuntu-22.04'
        run: |
          sudo apt-get update
          sudo apt-get install -y \
            libwebkit2gtk-4.1-dev \
            libappindicator3-dev \
            librsvg2-dev \
            patchelf

      # --- Setup del entorno ---
      - name: Setup Node.js
        uses: actions/setup-node@v4
        with:
          node-version: "lts/*"
          cache: "npm"

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          architecture: ${{ matrix.python_arch }}

      - name: Setup Rust
        uses: dtolnay/rust-toolchain@stable
        with:
          targets: ${{ matrix.rust_target }}

      # --- Caché para acelerar builds ---
      - name: Cache Rust
        uses: swatinem/rust-cache@v2
        with:
          workspaces: "./src-tauri -> target"

      # --- Instalar dependencias ---
      - name: Install Python backend deps
        working-directory: backend
        run: |
          pip install -r requirements.txt
          pip install pyinstaller

      - name: Install frontend deps
        run: npm ci

      # --- Compilar el sidecar Python para esta plataforma ---
      - name: Build Python sidecar
        working-directory: backend
        run: pyinstaller backend.spec --distpath ../src-tauri/binaries

      # --- Renombrar el sidecar con el target triple ---
      - name: Rename sidecar (Linux/macOS)
        if: matrix.platform != 'windows-latest'
        run: |
          TRIPLE=$(rustc --print host-tuple)
          mv src-tauri/binaries/fastapi-server \
             src-tauri/binaries/fastapi-server-${TRIPLE}
        shell: bash

      - name: Rename sidecar (Windows)
        if: matrix.platform == 'windows-latest'
        run: |
          $triple = (rustc --print host-tuple).Trim()
          Rename-Item -Path "src-tauri\binaries\fastapi-server.exe" `
                      -NewName "fastapi-server-${triple}.exe"
        shell: pwsh

      # --- Build final de Tauri ---
      - name: Build Tauri app
        uses: tauri-apps/tauri-action@v1
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          tagName: ${{ github.ref_name }}
          releaseName: "MyApp ${{ github.ref_name }}"
          releaseDraft: true
          prerelease: false
          args: "--target ${{ matrix.rust_target }}"
```

**Lo que produce este workflow:**

- `.deb` y `.rpm` para Linux x86_64
- `.dmg` para macOS ARM64 y x86_64
- `.msi` y `.exe` (NSIS) para Windows x86_64

Todo subido automáticamente como assets de un GitHub Release draft, que luego se publica manualmente.

---

## 11. Seguridad: el sistema de Capabilities

Tauri 2.0 introdujo un sistema de permisos granular llamado **Capabilities**. A diferencia de Tauri 1.x (donde los permisos eran flags booleanas), en 2.0 cada permiso especifica exactamente qué comando, en qué ventana, con qué argumentos puede ejecutarse.

### `capabilities/default.json`

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "Capabilities for the main application window",
  "windows": ["main"],
  "permissions": [
    "core:default",
    "opener:default",
    {
      "identifier": "shell:allow-spawn",
      "allow": [
        {
          "name": "binaries/fastapi-server",
          "sidecar": true
        }
      ]
    },
    {
      "identifier": "shell:allow-execute",
      "allow": [
        {
          "name": "binaries/fastapi-server",
          "sidecar": true
        }
      ]
    }
  ]
}
```

**Por qué importa esto:**

El sistema de Capabilities es un modelo de _allow-list_ explícita. Si no se declara el permiso `shell:allow-spawn` con el nombre exacto del sidecar, Tauri rechaza el intento de spawn con un error de runtime. Esto previene que código malicioso inyectado en el WebView pueda ejecutar procesos arbitrarios del sistema.

### El Content Security Policy (CSP)

La CSP configura qué recursos puede cargar el WebView, análogo al mismo mecanismo en navegadores web. Para el patrón FastAPI + HTTP, la directiva más importante es `connect-src`:

```json
"security": {
  "csp": "default-src 'self'; connect-src 'self' http://localhost:8008; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'"
}
```

Si el sidecar usa puerto dinámico, la CSP debe permitir todo localhost (con la implicación de seguridad que esto tiene):

```
"connect-src 'self' http://localhost:*"
```

---

## 12. Problemas conocidos y sus soluciones

### 12.1 Inconsistencias entre versiones de WebKitGTK

**Problema:** la app funciona en Ubuntu 24.04 pero no en Ubuntu 22.04 porque la versión de WebKitGTK es diferente.

**Solución:** compilar en el runner de Ubuntu más antiguo que se quiera soportar (actualmente `ubuntu-22.04` en GitHub Actions). Los binarios compilados en un sistema más viejo son compatibles con sistemas más nuevos, pero no al revés.

### 12.2 El sidecar no termina al cerrar la app

**Problema:** al cerrar la ventana de Tauri, el proceso Python sigue corriendo en background.

**Solución:** implementar el handler `on_window_event` en Rust (ver sección 9) que llame a `child.kill()`. Adicionalmente, registrar un handler de `SIGTERM` en Python para limpieza.

### 12.3 PyInstaller en modo `--onefile` con uvicorn

**Problema:** uvicorn falla al arrancar dentro de un `--onefile` porque no puede encontrar sus workers.

**Solución:** declarar los `hiddenimports` de uvicorn en el `.spec` file (ver sección 7) y usar `--onedir` en lugar de `--onefile`.

### 12.4 Antivirus flagging en Windows

**Problema:** Windows Defender u otros antivirus marcan el sidecar como sospechoso.

**Solución:** firmar el ejecutable con un certificado de Code Signing (EV o Standard). Para distribución comercial, esto es un requisito. Tauri Action soporta signing con secrets de GitHub. Como alternativa temporal, Nuitka reduce la tasa de detección.

### 12.5 El puerto localhost está ocupado

**Problema:** si el usuario tiene otra app corriendo en el puerto 8008, el sidecar falla al arrancar.

**Solución:** implementar detección de puerto libre en Python (ver sección 5.2) y comunicar el puerto elegido al proceso Tauri via stdout antes de que el frontend haga su primer request.

### 12.6 Wayland: ventanas sin decoraciones o sin foco

**Problema:** en algunos compositors de Wayland, las ventanas de Tauri pueden aparecer sin decoraciones del servidor (CSD vs SSD).

**Solución:** las GTK apps en Wayland usan Client-Side Decorations (CSD) por defecto. Esto es comportamiento esperado y depende del tema de GTK instalado. Para GNOME, las apps con libadwaita se integran mejor visualmente.

---

## Referencias y recursos

|Recurso|URL|
|---|---|
|Documentación oficial Tauri 2.0|https://v2.tauri.app|
|Sidecar guide oficial|https://v2.tauri.app/develop/sidecar/|
|GitHub Actions guide oficial|https://v2.tauri.app/distribute/pipelines/github/|
|Ejemplo Tauri v2 + FastAPI (Next.js)|https://github.com/dieharders/example-tauri-v2-python-server-sidecar|
|Template Vue + Tauri + FastAPI|https://github.com/AlanSynn/vue-tauri-fastapi-sidecar-template|
|Template React + Tauri + FastAPI (full)|https://github.com/fudanglp/tauri-fastapi-full-stack-template|
|WebView library (wry)|https://github.com/tauri-apps/wry|
|PyInstaller docs|https://pyinstaller.org|
|Nuitka docs|https://nuitka.net|

---

_Documento generado en mayo de 2026. Las versiones de referencia son Tauri 2.9.x, WebKitGTK 4.1, Python 3.12, PyInstaller 6.x._