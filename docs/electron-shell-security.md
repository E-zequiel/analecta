# Electron Shell: Security Model

This document specifies the security architecture of Analecta's Electron shell. It describes why explicit hardening is necessary, what the six-layer security model does, and the rules contributors must follow when extending the IPC surface.

---

## Background: Tauri vs. Electron Default Security Posture

Tauri 2.x enforces a **declarative, framework-level capability model**. Every native API a renderer can call must be explicitly listed in `capabilities/default.json`. A renderer cannot read a file, open a dialog, or access the clipboard unless the corresponding capability (`fs:allow-read-text-file`, `dialog:allow-open`, etc.) is declared. The framework enforces this at the IPC boundary — no code the developer writes can accidentally bypass it.

Electron has **no equivalent system by default**. With `nodeIntegration: true` (the historical default), a renderer has the full Node.js API surface available directly. Even with `nodeIntegration: false`, the renderer can still call anything the preload script exposes via `contextBridge`, and there is nothing preventing a developer from exposing unsafe capabilities.

The six-layer model described here replicates Tauri's guarantees in Electron's imperative model. The outcome — a renderer that can only access explicitly approved, validated capabilities — is equivalent. The mechanism is different: instead of a framework enforcing a declarative manifest, we enforce it through code at each layer.

---

## Threat Model

We assume the renderer process (the SvelteKit frontend) may be compromised by malicious content — for example, a crafted article that exploits a markdown-it XSS vulnerability or an injected script via a remote image payload. A compromised renderer should not be able to:

- Read arbitrary files from the filesystem (e.g., `~/.ssh/id_rsa`, `/etc/passwd`)
- Write to paths outside the vault directory
- Execute shell commands or spawn processes
- Open arbitrary URLs using non-HTTP schemes
- Access IPC channels not explicitly intended for renderer use

We do not assume the main process is compromised. The main process runs in the full Node.js environment and is not sandboxed — it is trusted.

---

## Security Layers

### Layer 1 — BrowserWindow Hardening

Every `BrowserWindow` is created with the following `webPreferences`:

```ts
{
  contextIsolation: true,         // Isolates renderer JS context from preload context
  sandbox: true,                  // Runs renderer in a Chromium-style OS sandbox
  nodeIntegration: false,         // No Node.js APIs in the renderer
  nodeIntegrationInWorker: false, // No Node.js in Web Workers either
  webSecurity: true,              // Never disable — enforces same-origin and CSP
  allowRunningInsecureContent: false,
  experimentalFeatures: false,
  preload: path.join(__dirname, '../preload/index.js'),
}
```

With `sandbox: true`, the renderer process runs in a Chromium OS-level sandbox — the same sandbox used by Chrome tabs. The preload script itself also runs in a restricted context: it can use `contextBridge` and `ipcRenderer`, but has no access to `fs`, `path`, `child_process`, or any other Node.js module.

**`webSecurity` must never be set to `false`.** Disabling it turns off same-origin enforcement and CSP, effectively removing the browser's core security model.

---

### Layer 2 — Vault-Scoped Filesystem Access

**This layer is the Electron equivalent of Tauri's FS capability.**

Tauri granted filesystem access via `update_vault_scope(path)`, which told the Tauri runtime to allow reads and writes only within a specific directory. In Electron, this is replicated by `vault-state.ts`, a module in the main process that tracks the authorized vault path and validates every filesystem IPC call against it.

```
electron/main/vault-state.ts
```

The vault path is set from two sources:
1. **On sidecar startup:** The Python sidecar emits `VAULT_PATH:{path}` on stdout when it initializes. The main process calls `setVaultPath(path)`.
2. **On settings change:** When the user changes the vault directory in Settings, the frontend calls `invoke('update-vault-scope', newPath)`. The IPC handler calls `setVaultPath(newPath)`.

`assertVaultPath(filePath)` is the core enforcement function. It normalizes the input path and verifies it starts with the vault directory prefix. If validation fails, it throws — the IPC handler catches the exception and the renderer receives a rejection, identical to what would happen with Tauri's capability system.

**Permitted paths for `read-file` and `write-file`:** only paths within `{vault_path}/`. Subdirectories are permitted. Symlinks are not followed (path normalization catches `..` traversal).

**`file-exists`** is permitted for any path because it returns only a boolean — no file content is exposed. This is used to verify that the vault directory itself exists on startup.

---

### Layer 3 — IPC Handler Input Validation

Every handler registered via `ipcMain.handle()` validates its arguments before acting on them. The validation rules are:

| Handler | Validation |
|---------|-----------|
| `read-file` | String, non-empty, within vault path (`assertVaultPath`) |
| `write-file` | Object with `path` (string, non-empty, within vault) and `data` (string) |
| `file-exists` | String, non-empty (`assertExistsPath`) |
| `open-dialog` | Object with expected dialog properties; no arbitrary code paths |
| `show-message-box` | Object with expected message box properties |
| `open-url` | String, valid URL, protocol must be `http:` or `https:` only |
| `reveal-in-dir` | String, non-empty, within vault path (`assertVaultPath`) |
| `notify` | Object with `title` (string) and `body` (string) |
| `update-vault-scope` | String, non-empty |
| `set-login-item` | Boolean |
| `clipboard-read` | No arguments |

The `open-url` handler explicitly blocks non-HTTP schemes. An `open-url` call with `file:///etc/passwd`, `javascript:alert(1)`, or any other non-HTTP scheme is rejected before `shell.openExternal` is invoked.

A shared type guard is used throughout:

```ts
function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}
```

---

### Layer 4 — Custom Protocol Handler Restrictions

Analecta registers two custom URL schemes:

**`app://`** — Serves the SvelteKit static build (the application frontend). This protocol is read-only and serves only files from the `frontend/build/` directory. It does not serve files from elsewhere on the filesystem.

**`analecta-file://`** — Serves vault images embedded in extracted articles. This replaces Tauri's `asset://` protocol.

The `analecta-file://` handler applies two independent restrictions:

1. **Path restriction:** The resolved file path must start with the vault directory prefix. Requests for paths outside the vault return HTTP 403.
2. **Extension allowlist:** Only image file extensions are served: `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg`, `.avif`. Requests for `.md`, `.sh`, `.py`, `.ttf`, or any other extension return HTTP 403.

```
Allowed:  analecta-file:///home/user/vault/assets/article/abc123.png  → 200
Denied:   analecta-file:///etc/passwd                                 → 403
Denied:   analecta-file:///home/user/vault/article.md                → 403
```

Both protocols are registered with `protocol.registerSchemesAsPrivileged()` before `app.ready`, with `{ standard: true, secure: true, supportFetchAPI: true }`.

---

### Layer 5 — Content Security Policy

A CSP is applied to all responses via `session.defaultSession.webRequest.onHeadersReceived`. This provides defense in depth: even if the renderer executes injected script, the CSP restricts what it can load or connect to.

```
default-src    'self' app:;
connect-src    'self' http://localhost:* app:;
img-src        'self' app: analecta-file: data: blob: https:;
style-src-elem 'self' app:;
style-src-attr 'none';
font-src       'self' app:;
script-src     'self' 'sha256-<computed at startup>' app:;
object-src     'none';
base-uri       'self';
```

Key decisions:
- `connect-src` allows `http://localhost:*` for the Python sidecar API. No external HTTP connections from the renderer.
- `img-src` allows `analecta-file:` (vault images) and `https:` (remote article images). Remote images were a deliberate decision for the article reader; they are not blocked.
- `object-src 'none'` blocks Flash and other plugin content.
- `base-uri 'self'` prevents `<base>` tag injection from changing the document base URL.
- `script-src` does **not** use `'unsafe-inline'`. SvelteKit injects one inline script per build (`__sveltekit_xyz = { base: "" }`). `protocols.ts` reads `index.html` at app startup, computes the SHA-256 hash of that script, and includes it in `script-src`. The hash is recomputed on every launch so it stays correct across builds without a separate build step.
- `style-src` is split into sub-directives per CSP Level 3: `style-src-elem` governs `<style>` tags and `<link>` stylesheets; `style-src-attr` governs `style=""` attributes on elements. `'unsafe-inline'` is absent from both. The built HTML has no inline `<style>` tags (`ssr = false` → external CSS only), so no hashes are needed for `style-src-elem`. `style-src-attr 'none'` is safe because all dynamic style bindings have been migrated from `style="template"` to Svelte's `style:property` directive, which compiles to `element.style.setProperty()` — a CSSStyleDeclaration API call not governed by `style-src-attr` per spec. Svelte 5's built-in transitions (`transition:slide`) use WAAPI (`element.animate()`), not `<style>` injection.

---

### Layer 6 — Preload Channel Whitelist

The preload script is the only bridge between the renderer and the main process. It is kept minimal: no `fs`, no `path`, no `child_process`, no Node.js modules. Only `contextBridge` and `ipcRenderer` are imported.

A channel whitelist is enforced on every `ipcRenderer.invoke` call:

```ts
const ALLOWED_CHANNELS = [
  'get-sidecar-port', 'notify', 'update-vault-scope',
  'read-file', 'write-file', 'file-exists',
  'open-dialog', 'show-message-box',
  'clipboard-read', 'reveal-in-dir', 'open-url',
  'check-update', 'download-and-install-update', 'relaunch',
  'get-login-item', 'set-login-item', 'get-initial-deep-link',
] as const;
```

If the renderer calls `window.electronAPI.invoke('arbitrary-channel')`, the preload throws before `ipcRenderer.invoke` is called. The main process never receives the call.

The `on` method (for event listeners) is similarly guarded: only `sidecar-ready` and `deep-link` events are forwarded.

---

### Render Server & URL Filtering

The Electron main process runs a lightweight HTTP server (`scraper.ts`) bound exclusively to `127.0.0.1` on an OS-assigned random port. The Python sidecar calls this server to request Tier 2 (Chromium-rendered) extraction. Two controls protect it:

**Token authentication.** The server generates a `ANALECTA_RENDER_TOKEN` via `crypto.randomBytes(32)` at startup and passes it to the sidecar via the `ANALECTA_RENDER_TOKEN` environment variable. Every request must include this token in the `X-Render-Token` header; requests without it receive HTTP 401. The token is never written to disk or logged.

**URL blocklist (`validateScrapeUrl`).** Before spawning a `BrowserWindow`, the entry URL is validated against the following blocklist:

| Category | Blocked range |
|----------|--------------|
| Non-HTTP/HTTPS protocols | Any scheme other than `http:` / `https:` |
| Loopback (IPv4) | `127.0.0.0/8` (entire block, not just `.1`) |
| Loopback (IPv6) | `::1` |
| IPv4-mapped IPv6 loopback | `::ffff:127.x.x.x` (dotted) · `::ffff:7f...` (hex) |
| Link-local (IPv4) | `169.254.0.0/16` |
| Link-local (IPv6-mapped) | `::ffff:169.254.x.x` · `::ffff:a9fe:...` |
| RFC 1918 private ranges | `10.0.0.0/8` · `172.16.0.0/12` · `192.168.0.0/16` |

**Known limitation.** The filter applies to the entry URL supplied by the sidecar. Once Chromium has loaded the initial page, server-side redirects and JavaScript-triggered navigations are not re-validated. This is an accepted residual risk: the scraping `BrowserWindow` has no preload script and no IPC surface, so it cannot call back into the main process. Its only output is a serialized HTML string returned to the sidecar — there is no mechanism for a redirect to a local service to exfiltrate data back to a remote party.

---

## Comparison with Tauri Capabilities

| Protection | Tauri 2.x | This model |
|-----------|-----------|-----------|
| FS access outside vault | Impossible — framework blocks | Throws in `assertVaultPath()` |
| Unknown IPC channel | Impossible — capabilities | Throws in preload channel whitelist |
| `file://` via `openUrl` | Impossible — capabilities | Throws in `open-url` scheme check |
| `analecta-file://` outside vault | N/A (asset:// had path validation) | Returns HTTP 403 |
| Node.js in renderer | Impossible | `nodeIntegration: false` |
| Renderer accesses main directly | Impossible | `contextIsolation: true` + `sandbox: true` |
| New handler without validation | **Framework prevents it** | **Developer discipline required** |

The one structural difference: Tauri's framework enforces permissions at the call site regardless of what the developer writes. In this model, enforcement is in the handler code. If a new handler is added without calling `assertVaultPath()`, the framework will not catch it. The mitigation is the developer guideline below.

---

## Developer Guidelines: Adding New IPC Handlers

When adding a new capability to the IPC surface, follow this checklist:

**1. Add the channel to the preload whitelist first.**  
In `electron/preload/index.ts`, add the new channel name to `ALLOWED_CHANNELS`. This makes the intent explicit and ensures the whitelist is the single source of truth for the IPC surface.

**2. Validate all inputs before acting.**  
Every argument received from the renderer is untrusted. Use `isObject()`, `typeof`, and range checks before any filesystem, shell, or system call. Never pass renderer-supplied strings directly to `fs`, `shell`, `dialog`, or `spawn` without validation.

**3. Apply `assertVaultPath()` to any filesystem path.**  
Any handler that reads or writes a file path supplied by the renderer must call `assertVaultPath(path)` or `assertExistsPath(path)`. The only exception is paths selected by the user through `dialog.showOpenDialog` — those are implicitly authorized by the user action.

**4. Use the narrowest possible permission.**  
If a handler only needs to read a file, do not also write. If a handler only needs a boolean existence check, use `assertExistsPath` (not `assertVaultPath`, which is more restrictive). Principle of least privilege applies within the main process too.

**5. Never expose:**
- `child_process.exec`, `child_process.spawn`, or `shell.exec` via IPC
- Raw `eval` or code execution of any kind
- The sidecar binary path or any internal filesystem paths

**6. Document the handler's purpose in `ipc.ts`.**  
A one-line comment above each `ipcMain.handle` block explaining what it does and what validation it applies makes security review possible.

**7. Apply loopback filtering before implementing bulk or automated extraction.**  
The current extraction flow is single-URL and user-initiated: the user explicitly submits each URL through the UI, so they are always the authorizing party. This changes if any of the following features are ever added:

- Bulk URL import (CSV, OPML, clipboard list)
- RSS / Atom / JSON Feed ingestion
- Webhook-triggered or scheduled extraction
- Any path where a URL enters the pipeline from an external or semi-trusted source without per-URL user confirmation

In all such cases, URLs arrive from sources the user does not fully control. A URL crafted to redirect to a loopback address could cause the Python sidecar's `httpx2` client to inadvertently fetch internal services, since `ArticleExtractor._fetch` uses `follow_redirects=True` without a post-redirect destination filter.

Before shipping any feature in the list above:

1. **Validate the submitted URL** against the same blocklist used in `validateScrapeUrl` (loopback, link-local, RFC 1918) before it enters `ArticleExtractor.extract()`.
2. **Add an `httpx2` response event hook** in `_fetch` that inspects the resolved URL after redirects and raises `ExtractionError` if the final destination falls within a blocked range. Example skeleton:

```python
import ipaddress

def _block_loopback_redirect(response: httpx2.Response) -> None:
    host = response.url.host.strip("[]")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return  # hostname, not an IP literal — DNS result is not re-checked here
    blocked = [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("::ffff:127.0.0.0/104"),
        ipaddress.ip_network("::ffff:169.254.0.0/112"),
    ]
    if any(addr in net for net in blocked):
        raise ExtractionError(f"Redirect to blocked address: {response.url}")
```

This is not required today because the user is always the authorizing party for each URL. It becomes required the moment that assumption no longer holds.

---

## Security Checklist for Pull Requests

Before merging any change that touches `electron/main/ipc.ts` or `electron/preload/index.ts`, verify:

- [ ] New channels are added to `ALLOWED_CHANNELS` in `electron/preload/index.ts`
- [ ] All string inputs from the renderer are validated before use
- [ ] Filesystem paths pass through `assertVaultPath()` or `assertExistsPath()`
- [ ] URL arguments pass the `http:`/`https:` scheme check
- [ ] No Node.js code-execution primitives are exposed via IPC
- [ ] The CSP in Layer 5 still covers the new capability (if it involves network or resource loading)
