# Hybrid architecture

La arquitectura híbrida para aplicaciones de escritorio surge como respuesta a la necesidad de crear interfaces de usuario (UI) modernas, dinámicas y altamente personalizables, sin el costo de desarrollo que implica dominar _toolkits_ nativos complejos como Qt o GTK.

El paradigma se basa en una separación estricta de responsabilidades (_Separation of Concerns_):

1. **Frontend (UI):** Se construye utilizando tecnologías web estándar (HTML, CSS, JavaScript/TypeScript) o motores de renderizado multiplataforma (como Flutter o React Native for Web). Se ejecuta dentro de un contenedor web embebido (**WebView**) provisto por el sistema operativo. En Linux (Wayland), este motor subyacente suele ser `WebKitGTK` o integraciones con Chromium.
    
2. **Backend (Lógica):** Se desarrolla íntegramente en Python. Maneja el acceso al sistema de archivos, llamadas al sistema operativo, bases de datos locales (ej. SQLite o conexiones a tu infraestructura PostgreSQL), criptografía y tareas intensivas.
    
3. **Puente de Comunicación:** Ambos procesos se comunican mediante mecanismos de comunicación entre procesos (IPC, por sus siglas en inglés), WebSockets o un servidor HTTP local embebido.
    

## Ventajas del Enfoque Híbrido

- **Reaprovechamiento de Ecosistemas:** Permite utilizar bibliotecas de UI web (React, Vue, Tailwind CSS) o bibliotecas de visualización de datos avanzadas (D3.js, Three.js) que no tienen equivalentes nativos fáciles de implementar en Python.
    
- **Separación de Equipos:** En entornos corporativos, permite que ingenieros _frontend_ trabajen en la interfaz mientras los ingenieros _backend_ optimizan la lógica en Python.
    
- **Estilo Unificado:** Garantiza que la aplicación se vea exactamente igual en Linux, Windows y macOS, superando las inconsistencias de los temas de cada gestor de ventanas.
    

---

## Las Mejores Alternativas en el Ecosistema Python

La elección de la herramienta depende de si deseás escribir la interfaz web explícitamente (HTML/JS) o si preferís que un _framework_ traduzca código Python a una UI web.

### 1. PyWebView (El estándar minimalista BYOF - Bring Your Own Frontend)

Es una biblioteca ligera que simplemente abre una ventana nativa con un _WebView_ y provee una API para inyectar un puente de comunicación bidireccional entre JavaScript y Python.

- **Cómo funciona:** Vos construís tu _frontend_ (ej. una Single Page Application en Vue o React) y tu _backend_ en Python. PyWebView los une.
    
- **Ventaja:** Control total sobre el _stack_ tecnológico. Es agnóstico respecto al _framework_ web que uses.
    
- **En Wayland:** Utiliza `WebKitGTK` nativamente, integrándose bien con el compositor sin requerir XWayland.
    

### 2. Flet (La alternativa orientada a Python)

Flet permite construir interfaces de usuario interactivas para web, escritorio y dispositivos móviles usando **exclusivamente Python**. Está impulsado por Flutter bajo el capó.

- **Cómo funciona:** Escribís código Python definiendo controles (`ft.Text`, `ft.Container`, `ft.ElevatedButton`). Flet se encarga de enviar este estado a un motor Flutter alojado localmente que dibuja la UI.
    
- **Ventaja:** Cero conocimientos de HTML/CSS/JS requeridos. Excelente para herramientas internas, paneles de administración y desarrolladores estrictamente enfocados en Python.
    
- **Desventaja:** Estás limitado a los _widgets_ que Flet/Flutter soporten. Modificar el comportamiento a bajo nivel de la UI es complejo.
    

### 3. Eel

Es una biblioteca pequeña que lanza un servidor web local y abre un navegador (prefiriendo Chrome/Chromium o Edge) en modo "App" (sin barra de direcciones ni pestañas).

- **Cómo funciona:** Expone funciones de Python a JavaScript usando un decorador `@eel.expose`, y viceversa.
    
- **Ventaja:** Extremadamente fácil de configurar para prototipos rápidos.
    
- **Desventaja:** Depende de que el usuario tenga un navegador compatible instalado. A diferencia de PyWebView, no usa el _WebView_ a nivel de sistema (OS-level), sino que invoca el binario del navegador. No es ideal para distribución comercial.
    

### 4. Tauri con "Sidecar" en Python (El estándar moderno de alto rendimiento)

Aunque **Tauri** es un _framework_ de Rust, se ha convertido en el estándar de la industria (reemplazando a Electron) debido a su bajo consumo de memoria y binarios minúsculos.

- **Cómo funciona:** Se utiliza Tauri para gestionar la ventana del sistema operativo y el _WebView_. Tauri expone una funcionalidad llamada _Sidecar_, que permite empaquetar y ejecutar binarios externos (en este caso, un ejecutable de tu aplicación Python creado con PyInstaller o Nuitka).
    
- **Ventaja:** Seguridad extrema, rendimiento inigualable en la gestión de ventanas y tamaños de distribución mínimos.
    
- **Desventaja:** Introduce complejidad en el ciclo de construcción (_build pipeline_) y requiere configuración en Rust, aunque la lógica de negocio siga estando en Python.
    

## Resumen Arquitectónico para Toma de Decisiones

|**Requisito del Proyecto**|**Arquitectura / Framework Recomendado**|
|---|---|
|**Control total del diseño web + Lógica en Python**|**PyWebView** + React/Vue/Svelte|
|**Desarrollo rápido exclusivo en Python sin tocar JS**|**Flet**|
|**Prototipo rápido / Herramienta personal CLI a GUI**|**Eel**|
|**Producto comercial con máxima seguridad y rendimiento**|**Tauri** (Rust UI Shell) + Python (Sidecar backend)|