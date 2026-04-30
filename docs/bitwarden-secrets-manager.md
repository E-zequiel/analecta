# Bitwarden Secrets Manager integration

The user wants Claude Code to:
- Never handle secrets directly.
- Alert the user whenever a secret needs to be created/managed.
- Include instructions on what to do in BSM.

This means adding a section to CLAUDE.md that:
- Defines the secret management policy.
- Lists known secrets for Analecta (VirusTotal API key, potentially others).
- Instructs Claude Code to emit a structured warning when a secret is encountered.

The BSM workflow typically involves:
- bws secret create to create a secret.
- bws secret get `<id>` to retrieve it.
- Injecting via bws run -- command or env injection.

For Analecta specifically, the user mentioned the VirusTotal API key stored in system keyring. 
But the user now wants to use Bitwarden Secrets Manager. These can coexist: BSM handles secret 
storage/retrieval at the developer level, and the app uses the system keyring at runtime for the end-user.

Actually, wait — BSM is typically for developer/CI secrets. The system keyring library is for runtime user 
secrets in the app. These serve different purposes:
- BSM: developer manages API keys during development
- keyring: the app stores the user's API key at runtime

---

## Creación de Secretos vía CLI local

Contexto: mi `Pop!_OS` tiene está configurada como 'machine account' en BSM con permisos sólo lectura, por motivos de seguridad. Por tanto, sólo yo puedo crear secretos y debo hacerlo desde la web app de BSM.

Abajo integro más contexto. Si encontrás algo que no es correcto, indicámelo.

### 1. El Conflicto de Permisos (RBAC)
Si en mi entorno local (`Pop!_OS`) tengo exportada la variable `BWS_ACCESS_TOKEN` correspondiente a la *Machine account* configurada como **Read-only**, el comando `bws secret create --name "analecta/<secret_name>"` fallará.

El CLI `bws` actúa en representación del token activo. Al ejecutar una operación mutacional (como `create`, `edit` o `delete`), el servidor de Bitwarden evalúa los permisos de esa cuenta. Al ser de sólo lectura, la API rechazará la solicitud (típicamente con un error HTTP 403 Forbidden o Unauthorized). 

Para crear un secreto desde la terminal, necesitarías un token vinculado a una *Machine account* con permisos `Read and write`, lo cual rompería la regla de menor privilegio que establecí para mi entorno de consumo diario.

### 2. El Error de Sintaxis del CLI
El comando que Claude Code propone en la línea 205 (`bws secret create --name "analecta/<secret_name>"`) es sintácticamente incorrecto para la versión actual de BSM. 

El CLI de Bitwarden no usa el flag `--name` para la creación. Requiere argumentos posicionales obligatorios, y lo más importante: **requiere el ID del proyecto**.
La sintaxis real es:

```zsh
bws secret create <KEY> <VALUE> <PROJECT_ID>
```

---

### 3. Solución: Refactorización del `CLAUDE.md`

La mejor política para interactuar con Claude Code es que el asistente te indique que vayas a la Web App a crearlo, ya que es la única vía donde vos, como humano, tenés los permisos de escritura asegurados sin comprometer la terminal local.

Aquí tenés el bloque corregido listo para ser copiado:

```markdown
## Secret Management 

Two-layer model:
- **BSM** (Web App / `bws`): Developer-level single source of truth.
- **System keyring** (`keyring` library): Runtime user-level. The app reads secrets from here at runtime.

### Policy for Claude Code
- Never generate, log, print, or hardcode secret values.
- The local `bws` CLI environment operates with a Read-only Machine Account. Therefore, do not attempt to run `bws secret create` automatically.
- When code requires a secret to exist, emit this exact warning block to the developer and halt execution:

   ````
⚠️  SECRET REQUIRED
    Name   : analecta/<secret_name>
    Purpose: <what it's used for>
    Action : 1. Open Bitwarden Secrets Manager Web App.
            2. Create a new secret with Key: "<secret_name>" in the active Project.
            3. (Optional) If testing locally without BSM injection, add to local keyring via Python:
                `import keyring; keyring.set_password("analecta", "<secret_name>", "<VALUE>")`
    Runtime: App reads via `keyring.get_password("analecta", "<secret_name>")`
    ````
### Known secrets

| Secret | BSM Key | Runtime storage |
|--------|---------|-----------------|
| VirusTotal API key | `virustotal_api_key` | `keyring.get_password("analecta", "virustotal_api_key")` |
```

Con esta corrección, le quitás a Claude Code la presunción de que el CLI local tiene permisos de escritura y alineás la instrucción con la realidad de tu infraestructura de seguridad. 
