# Plan: Analecta Pre-v0.3.0 — Unified Roadmap

## Context

E9 QA was in progress when extraction quality problems were discovered (socket.dev / AntV: footer
images instead of article content; JS-heavy SPAs producing incomplete Markdown). A two-tier
extraction pipeline is required: Tier 1 (current httpx+trafilatura+readability) plus Tier 2
(Electron's Chromium renders the JS, Defuddle extracts clean content from the live DOM).

All previously pending E9 QA items are postponed until Phases A–C complete, so QA validates
the finished product. This plan is the single source of truth for everything before merging
`feat/electron-shell` → `main` and tagging v0.3.0.

---

## All Decisions Resolved

| # | Decision | Choice |
|---|----------|--------|
| 1 | CDP driver | `webContents.debugger` (no external port; pydoll not needed) |
| 2 | Tier 2 extraction mode | Defuddle in BrowserWindow (browser mode, live DOM) + fallback to outerHTML→trafilatura |
| 3 | Properties panel UI | Obsidian-style collapsible panel above article body |
| 4 | Top bar | Frameless + custom titlebar + Ctrl+drag region + custom resize handles (8 directions) |
| 5 | Resize on GNOME/Wayland | Implement custom resize handles using `win.startResizing(edge)` |

---

## Architecture Summary

```
Python sidecar ──HTTP──► POST /render {url} ──► scraper.ts (Electron main)
                         X-Render-Token header         │
                                                        ▼
                                          URL validation (http/https only;
                                          block localhost, RFC-1918, app://, etc.)
                                                        │
                                                        ▼
                                          Hidden BrowserWindow
                                          (sandbox, scrapingSession)
                                                        │
                                          webContents.debugger.attach()
                                          Page.navigate + networkIdle
                                                        │
                                          executeJavaScript(defuddleBundle)
                                          new Defuddle(document).parseAsync()
                                                        │
                                          ┌─────────────┴────────────┐
                                          │ ok=true                  │ ok=false (fallback)
                                          │ {content, title,         │ {outerHtml}
                                          │  author, description,    │
                                          │  published}              │
                                          └──────────────────────────┘
                                                        │
Python sidecar ◄──HTTP◄─────── RenderResult (JSON, 10-50 KB or outerHtml)

  if ok:  ExtractedContent(html=result.content, metadata from Defuddle)
          → AssetDownloader → MarkdownConverter
  else:   self._parse(result.outerHtml) via readability/trafilatura
          → AssetDownloader → MarkdownConverter
```

---

## Phase A: Extraction Engine Tier 2

### A1 — `electron/main/scraper.ts` (NEW)

**Render HTTP server:**
```typescript
export async function startRenderServer(): Promise<{ port: number; token: string }>
// - Finds free port (same pattern as Python sidecar: bind socket, close, use port)
// - token = crypto.randomUUID()
// - Listens on 127.0.0.1 only
// - POST /render { url } → RenderResult
```

**URL validation:**
```typescript
function validateScrapeUrl(url: string): void {
  const parsed = new URL(url);                      // throws on malformed
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('blocked-protocol');
  const h = parsed.hostname;
  if (h === 'localhost' || h === '127.0.0.1' || h === '::1') throw new Error('blocked-local');
  // RFC-1918 ranges: 10.x, 172.16-31.x, 192.168.x
  if (/^10\.|^172\.(1[6-9]|2\d|3[01])\.|^192\.168\./.test(h)) throw new Error('blocked-rfc1918');
}
```

**BrowserWindow (scraping session):**
```typescript
const scrapingSession = session.fromPartition('persist:scraping', { cache: false });
new BrowserWindow({
  show: false,
  webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false,
                    session: scrapingSession },  // NO preload
})
```

**networkIdle via `webContents.debugger`:**
```typescript
const dbg = win.webContents.debugger;
dbg.attach('1.3');
await dbg.sendCommand('Page.enable');
await dbg.sendCommand('Network.enable');
await dbg.sendCommand('Page.setLifecycleEventsEnabled', { enabled: true });
await dbg.sendCommand('Page.navigate', { url });
await new Promise<void>((resolve, reject) => {
  const t = setTimeout(() => resolve(), 30_000);    // hard timeout
  dbg.on('message', (_e, method, params) => {
    if (method === 'Page.lifecycleEvent' && params.name === 'networkIdle') {
      clearTimeout(t); resolve();
    }
  });
  win.webContents.on('did-fail-load', (_e, code, desc) => {
    clearTimeout(t); reject(new Error(`load-failed: ${desc}`));
  });
});
```

**Defuddle injection:**
```typescript
// Bundle path: determine at module load time
// Check npm package for browser bundle: defuddle/dist/*.browser.js or defuddle/dist/index.js
// Verify and document exact path when implementing
const defuddleBundle = readFileSync(path.join(__dirname, '../defuddle-browser.js'), 'utf-8');

const script = defuddleBundle + `
;(async () => {
  try {
    const r = await new Defuddle(document).parseAsync();
    return JSON.stringify({ ok: true, content: r.content, title: r.title,
                            author: r.author, description: r.description,
                            published: r.published });
  } catch(e) {
    return JSON.stringify({ ok: false, outerHtml: document.documentElement.outerHTML,
                            error: e.message });
  }
})()`;

const resultJson = await win.webContents.executeJavaScript(script);
return JSON.parse(resultJson);
```

**Fallback:** if `ok === false`, return `{ ok: false, outerHtml }`. Python side falls back to
trafilatura on the `outerHtml`.

**Inactivity cleanup:** `setTimeout(destroy, 60_000)` reset on each request.

**Notes at implementation time:**
- Verify Defuddle's exact browser bundle export name (likely `window.Defuddle` or
  `module.exports = Defuddle`). Adjust injection script accordingly.
- Copy or symlink bundle into `electron/main/` at build time (or use `readFileSync` from
  `node_modules` path — acceptable since this is a build-time dep, not runtime external fetch).
- `defuddle` → add to `electron/package.json` dependencies; `pnpm install` in electron/.

### A2 — `electron/main/sidecar.ts`

```typescript
const { port, token } = await startRenderServer();  // called before spawnSidecar()
sidecarProcess = spawn(bin, [], {
  stdio: 'pipe',
  env: { ...process.env, ANALECTA_RENDER_PORT: String(port), ANALECTA_RENDER_TOKEN: token },
});
```

`startRenderServer()` called from `index.ts` before `spawnSidecar()`.

### A3 — `backend/src/analecta/extraction/tier2.py` (NEW)

```python
import os, json
import httpx
from dataclasses import dataclass
from analecta.extraction.core import ExtractionError

_PORT = int(os.environ.get('ANALECTA_RENDER_PORT', '0'))
_TOKEN = os.environ.get('ANALECTA_RENDER_TOKEN', '')

@dataclass
class Tier2Result:
    ok: bool
    content: str | None = None
    outer_html: str | None = None
    title: str | None = None
    author: str | None = None
    description: str | None = None
    published: str | None = None

async def render_url(url: str) -> Tier2Result:
    if not _PORT:
        raise ExtractionError('Electron render server not available (ANALECTA_RENDER_PORT not set)')
    async with httpx.AsyncClient(timeout=35.0) as client:
        resp = await client.post(
            f'http://127.0.0.1:{_PORT}/render',
            json={'url': url},
            headers={'X-Render-Token': _TOKEN},
        )
        resp.raise_for_status()
        data = resp.json()
        return Tier2Result(**{k: data.get(k) for k in Tier2Result.__dataclass_fields__})
```

### A4 — `backend/src/analecta/extraction/article.py`

**Confidence scoring:**
```python
def _is_low_confidence(raw_html: str, extracted_html: str) -> bool:
    text = BeautifulSoup(extracted_html, 'html.parser').get_text()
    if len(text.split()) < 200:
        return True
    soup = BeautifulSoup(raw_html, 'html.parser')
    all_tags = soup.find_all(True)
    if all_tags and len(soup.find_all('script')) / len(all_tags) > 0.4:
        return True
    return False
```

**`extract()` updated to async Tier 2 fallback:**
```python
async def extract(self, url: str) -> ExtractedContent:
    raw_html = await self._fetch(url)
    result = self._parse(raw_html, url)
    if _is_low_confidence(raw_html, result.html):
        try:
            tier2 = await render_url(url)
            if tier2.ok and tier2.content:
                result = _build_from_defuddle(url, tier2)
            elif tier2.outer_html:
                result = self._parse(tier2.outer_html, url)
        except ExtractionError:
            pass
    return result
```

**`_build_from_defuddle()`:**
```python
def _build_from_defuddle(url: str, t: Tier2Result) -> ExtractedContent:
    return ExtractedContent(
        title=t.title or '',
        html=t.content,
        url=url,
        source_type='article',
        metadata={
            'extractor': 'defuddle',
            **(({'author': t.author} if t.author else {})),
            **(({'description': t.description} if t.description else {})),
            **(({'published': t.published} if t.published else {})),
        },
    )
```

### A5 — Next.js hydration extraction (Tier 1 Branch B/C)

New `_try_nextjs_hydration(html: str) -> str | None` in `article.py`:

Branch B (Pages Router):
```python
tag = soup.find('script', {'id': '__NEXT_DATA__'})
if tag:
    data = json.loads(tag.string)
    page_props = data.get('props', {}).get('pageProps', {})
    # Extract text from page_props; if > 200 words, return reconstructed HTML
```

Branch C (App Router — `self.__next_f.push(...)`):
```python
pattern = re.compile(r'self\.__next_f\.push\(\[(.*?)\]\)', re.DOTALL)
# Collect and concatenate RSC chunks; return plain text if > 200 words
```

Note: Branch C is best-effort. If RSC parsing yields < 200 words, return None and fall through
to normal Tier 1 (or Tier 2 escalation). This is explicitly acceptable per the research doc.

Integration in `_parse()`: call `_try_nextjs_hydration()` before readability/trafilatura. If it
returns content, use it. Not a blocker for quality if it returns None.

### A6 — Properties metadata in frontmatter

`markdown/frontmatter.py`:
```python
for field in ('author', 'description', 'published'):
    if content.metadata.get(field):
        data[field] = content.metadata[field]
```

`extraction/article.py` — populate metadata from `trafilatura.extract_metadata()`:
```python
meta = trafilatura.extract_metadata(clean, default_url=url)
metadata: dict[str, str] = {'extractor': extractor}
if meta:
    for src, dst in [('author', 'author'), ('description', 'description'), ('date', 'published')]:
        val = getattr(meta, src, None)
        if val:
            metadata[dst] = str(val)
```

### A7 — Verification

```bash
cd /mnt/HD_ARCHIVO/HD_PROY/analecta
mise exec -- uv run python scripts/build_sidecar.py
stat binaries/analecta-sidecar/analecta-sidecar   # verify timestamp
./scripts/check.sh                                 # ruff + pyright + pytest + svelte-check + build
```

Manual: socket.dev article → article body extracted (not footer thumbnails), author/date appear
in frontmatter.

---

## Phase B: Bug Fixes

### B1 — Asset cleanup on delete (`api/routes/entries.py`)

```python
import shutil
from pathlib import Path

# In DELETE /entries/{entry_id}, after _unlink_if_exists(file_path):
vault_path = Path(request.app.state.config.vault_path)
slug = Path(file_path).stem              # e.g. "2026-05-22-my-article-slug"
assets_dir = vault_path / 'assets' / slug
if assets_dir.is_dir():
    shutil.rmtree(assets_dir)
```

### B2 — Tray clipboard focus (`electron/main/tray.ts`, lines ~21–31)

```typescript
win.show();
win.focus();   // best-effort on Wayland; show() must precede
const url = clipboard.readText().trim();
```

---

## Phase C: UI/UX Improvements

### C1 — Frameless window + custom titlebar

**`electron/main/index.ts`** — BrowserWindow options:
```typescript
new BrowserWindow({
  frame: false,
  titleBarStyle: 'hidden',   // tells Electron we're managing the title bar
  // ... existing options
})
```

**New IPC handlers** (`electron/main/ipc.ts`):
```typescript
ipcMain.handle('window-minimize',     () => mainWindow?.minimize());
ipcMain.handle('window-maximize',     () => mainWindow?.isMaximized()
                                              ? mainWindow.unmaximize()
                                              : mainWindow.maximize());
ipcMain.handle('window-close',        () => mainWindow?.close());
ipcMain.handle('window-start-move',   () => mainWindow?.startMoving());
ipcMain.handle('window-start-resize', (_e, edge: string) => mainWindow?.startResizing(edge as any));
ipcMain.handle('window-is-maximized', () => mainWindow?.isMaximized() ?? false);
```

Listen for maximize/unmaximize events on mainWindow → send to renderer:
```typescript
mainWindow.on('maximize',   () => mainWindow!.webContents.send('window-maximized',   true));
mainWindow.on('unmaximize', () => mainWindow!.webContents.send('window-maximized',   false));
```

**New IPC channels in `electron/preload/index.ts`** — add all 6 handlers + `window-maximized` event.

**New `frontend/src/lib/platform/electron.ts`** entries for window controls.

**CSS layout change** (`frontend/src/routes/+layout.svelte`):
```
Old:
  <div class="shell">
    <Sidebar />
    <main>
      <TabBar />
      {children}

New:
  <div class="shell">
    <TitleBar />          ← new component: window controls + tabs
    <div class="workspace">
      <Sidebar />
      <main>
        {children}
```

**New `frontend/src/lib/components/TitleBar.svelte`**:
```svelte
<!-- Drag region covers the full titlebar except interactive elements -->
<div class="titlebar" onmousedown={onTitlebarMouseDown}>
  <div class="window-controls">
    <button on:click={minimize}>●</button>
    <button on:click={toggleMaximize}>▬</button>
    <button on:click={closeWindow}>■</button>
  </div>
  <div class="tabs-area">
    <TabBar />
  </div>
</div>
```

Drag: `onmousedown` on `.titlebar` (not on buttons or tabs) → `invoke('window-start-move')`.
On Wayland, `-webkit-app-region` may not work; `win.startMoving()` is the Wayland-correct method.

**8-direction resize handles** (`frontend/src/lib/components/ResizeHandles.svelte`):

8 thin overlays at edges/corners of the window shell. Each on mousedown:
```typescript
invoke('window-start-resize', edge) // edge: 'top'|'bottom'|'left'|'right'|'top-left'|etc.
```

Mounted in `+layout.svelte` at the window root level (outside `.workspace`).

CSS variables from `obsidian-app.css` reference: `--header-height: 40px`, `--tab-*`, etc.

### C2 — Tab visual gaps (`TabBar.svelte`)

- Remove `gap: 2px` between tabs (line ~77)
- After titlebar migration, TabBar lives inside TitleBar; verify no double spacing

### C3 — Header font sizes (`frontend/src/app.css`)

Cross-reference `obsidian-app.css` for `--h1-size` through `--h6-size` exact values:
```css
.markdown-body h1 { font-size: 1.80em; font-weight: 700; margin: 1.6em 0 0.5em; }
.markdown-body h2 { font-size: 1.42em; font-weight: 600; margin: 1.4em 0 0.4em; }
.markdown-body h3 { font-size: 1.13em; font-weight: 600; margin: 1.2em 0 0.3em; }
.markdown-body h4, .markdown-body h5, .markdown-body h6
                 { font-size: 1em;    font-weight: 600; margin: 1em   0 0.25em; }
```

(Exact values validated visually against Obsidian during implementation.)

### C4 — Sidebar/TAGS counter order (`Sidebar.svelte`, lines ~336–358)

Move count badge to appear BEFORE the `+` (Plus) button in the TAGS section header, consistent
with count position in other sections.

### C5 — Right-click in viewer (`routes/viewer/[id]/+page.svelte`)

`oncontextmenu={handleRightClick}` on `.content`. Calls existing `showContextMenu()` with:
- Copy article URL
- Open in browser
- Archive / Unarchive
- Delete

### C6 — Ctrl+Tab navigation (`routes/+layout.svelte`)

```typescript
window.addEventListener('keydown', (e: KeyboardEvent) => {
  if (!e.ctrlKey || e.key !== 'Tab') return;
  e.preventDefault();
  const tabs = get(tabsStore);
  const idx = tabs.findIndex(t => t.id === get(activeTabId));
  const next = e.shiftKey ? (idx - 1 + tabs.length) % tabs.length
                           : (idx + 1) % tabs.length;
  setActiveTab(tabs[next].id);
});
```

### C7 — Properties panel (`routes/viewer/[id]/+page.svelte`)

Collapsible panel above `.markdown-body`. Parse YAML frontmatter from `source` string
(already loaded for rendering). Use `js-yaml` (check if present in pnpm workspace; add if not).

Fields: title, source (URL), author, published, created, description, tags, status.

```svelte
{#if propertiesExpanded}
  <div class="properties-panel">
    <div class="properties-header" on:click={() => propertiesExpanded = !propertiesExpanded}>
      <ChevronDown size={14} /> Properties
    </div>
    <div class="properties-body">
      {#each propertyFields as [key, val]}
        <div class="property-row">
          <span class="property-key">{key}</span>
          <span class="property-val">{val}</span>
        </div>
      {/each}
    </div>
  </div>
{/if}
```

Style based on Obsidian's `obsidian-app.css` `--metadata-*` variables.

---

## Phase D: E9 QA

All run AFTER Phases A–C complete and `check.sh` passes.

| ID | Test |
|----|------|
| D1 | 5 entries (2 articles, 1 YouTube, 1 Substack, 1 VT-triggered); SSE fan-out |
| D2 | Viewer: Markdown renders, analecta-file:// images, toolbar |
| D3 | Editor: save edits, hashtags update sidebar |
| D4 | Search Ctrl+K |
| D5 | Deep link: `xdg-open analecta://open?id=1` — cold-start + second-instance |
| D6 | Tray: clipboard add (after B2) + start-with-system |
| D7 | electron-updater banner + restart |
| D8 | Close → tray; Quit → no orphan sidecar |
| D9 | Security: `read-file` outside vault rejected from DevTools |
| D10 | .deb install on clean account (CI artifact) |

---

## Phase E: Release

1. `./scripts/check.sh` — final clean pass
2. Merge `feat/electron-shell` → `main`
3. Tag `v0.3.0`
4. Push tag → `release.yml` CI → `.deb` / `.rpm` / `.AppImage`
5. D10: verify `.deb` on clean account

---

## Critical files

| File | Change |
|------|--------|
| `electron/main/scraper.ts` | NEW: render HTTP server + scraping BrowserWindow + Defuddle |
| `electron/main/sidecar.ts` | Pass `ANALECTA_RENDER_PORT` + `ANALECTA_RENDER_TOKEN` at spawn |
| `electron/main/index.ts` | `frame: false`; call `startRenderServer()` before `spawnSidecar()` |
| `electron/main/ipc.ts` | 6 window-control + resize IPC handlers; maximize events |
| `electron/preload/index.ts` | Expose new window-control channels |
| `electron/package.json` | Add `defuddle` dep |
| `backend/src/analecta/extraction/tier2.py` | NEW: Tier2Result dataclass + render_url() |
| `backend/src/analecta/extraction/article.py` | Confidence scoring, Tier 2 fallback, Next.js hydration, metadata |
| `backend/src/analecta/markdown/frontmatter.py` | Add author/description/published if present |
| `backend/src/analecta/api/routes/entries.py` | `shutil.rmtree(assets_dir)` on delete |
| `electron/main/tray.ts` | `win.show(); win.focus();` before clipboard read |
| `frontend/src/lib/components/TitleBar.svelte` | NEW: custom titlebar with drag + window controls + tabs |
| `frontend/src/lib/components/ResizeHandles.svelte` | NEW: 8-direction resize handles |
| `frontend/src/lib/components/TabBar.svelte` | Remove 2px gap; moved into TitleBar |
| `frontend/src/lib/components/Sidebar.svelte` | TAGS: move count before '+' button |
| `frontend/src/lib/platform/electron.ts` | Window control + resize IPC wrappers |
| `frontend/src/routes/+layout.svelte` | New shell layout (TitleBar + workspace); Ctrl+Tab handler |
| `frontend/src/app.css` | Heading font sizes h1–h6 |
| `frontend/src/routes/viewer/[id]/+page.svelte` | Right-click menu + Properties panel |
| `backend/backend.spec` | Verify/update if new Python deps |
