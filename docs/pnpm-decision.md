# Decisión: pnpm como package manager

**Estado:** Aceptada  
**Fecha:** 2026-05-05  
**Contexto:** Bloque A3 de la migración híbrida (Tauri + FastAPI + SvelteKit)

---

## Contexto

La migración a arquitectura híbrida incorpora un frontend SvelteKit con dependencias JS/TS (CodeMirror 6, markdown-it, plugins Tauri). Se evaluaron las alternativas al package manager por defecto (npm) antes de iniciar el bloque A3.

---

## Problema con npm

npm instala dependencias en un `node_modules` **flat**: el resolvedor coloca todos los paquetes —directos y transitivos— al mismo nivel. Esto permite que cualquier paquete acceda mediante `require()` a dependencias que no declaró explícitamente en su `package.json` ("phantom dependencies").

Este comportamiento es un vector real de ataques de supply-chain:

- Un paquete malicioso anidado puede importar módulos que "no debería ver".
- La superficie de ataque crece con cada dependencia transitiva.
- El problema no es hipotético: varios CVEs recientes en el ecosistema npm explotan exactamente este mecanismo.

---

## Solución: pnpm

pnpm resuelve esto con una arquitectura de `node_modules` **no-flat**:

- Cada paquete recibe su propio `node_modules/` con symlinks únicamente a sus dependencias declaradas.
- Las dependencias transitivas viven en un store global content-addressable (`~/.pnpm-store/`) y se vinculan sin duplicación.
- Un paquete que intente acceder a una dependencia no declarada recibe un error en lugar de acceso silencioso.

### Beneficios adicionales

| Aspecto | npm | pnpm |
|---------|-----|------|
| Seguridad (phantom deps) | ✗ flat, acceso irrestricto | ✓ strict, aislamiento por paquete |
| Velocidad de instalación | base | más rápido (store + hard links) |
| Uso de disco | duplica deps entre proyectos | store global compartido |
| Lockfile | `package-lock.json` | `pnpm-lock.yaml` (más legible) |
| Workspaces | soportado | soportado, sintaxis `--filter` |

---

## Compatibilidad con el stack

- **Tauri 2.0**: detecta `pnpm-lock.yaml` automáticamente y usa pnpm en `beforeDevCommand`/`beforeBuildCommand`.
- **SvelteKit**: soporte de primera clase; `pnpm create svelte@latest` es el método recomendado.
- **mise**: pnpm se gestiona como tool nativo (`pnpm = "latest"` en `.mise.toml`), sin depender de `npm install -g` ni corepack.
- **GitHub Actions**: `pnpm/action-setup` es una action oficial y ampliamente adoptada.

---

## Decisión

**Usar pnpm exclusivamente. Nunca npm.**

Gestión vía mise:

```toml
# .mise.toml
[tools]
python = "3.13"
node   = "lts"     # runtime requerido por pnpm
rust   = "stable"
pnpm   = "latest"
```

Workspace declaration en raíz:

```yaml
# pnpm-workspace.yaml
packages:
  - frontend
```

---

## Impacto en el plan de migración

| Bloque | Cambio |
|--------|--------|
| A3 | `.mise.toml`: agregar `pnpm = "latest"` |
| C2 | `tauri.conf.json`: `pnpm --filter frontend dev` / `pnpm --filter frontend build` |
| D1 | Scaffold: `pnpm create svelte@latest` |
| F4 | CI: `pnpm install --frozen-lockfile` |
| F5 | CI: `pnpm run check` |

Todos los comandos Node se invocan como `mise exec -- pnpm <cmd>`.

---

## Alternativas descartadas

- **npm**: descartado por el problema de phantom dependencies descrito.
- **yarn**: equivalente a npm en términos de seguridad para este caso; no aporta ventaja diferencial.
- **bun**: más rápido, pero menos maduro para producción y con integración Tauri menos probada.
