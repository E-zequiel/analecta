# Virus Total

La integración es viable siempre y cuando se gestione correctamente la interacción con los *Terms of Service* (ToS) de VirusTotal y se delegue la responsabilidad del acceso al usuario final.

A continuación, el detalle exacto de las implicancias.

## Punto de vista legal y *Terms of Service*

1.  **Uso estrictamente no comercial:** La *Public API* de VirusTotal es gratuita, pero sus ToS prohíben de manera explícita su uso en aplicaciones, productos o servicios comerciales. Si la aplicación *open source* es completamente gratuita, el uso está permitido. Sin embargo, si se planea monetizar la aplicación de cualquier manera (modelo *freemium*, donaciones forzadas, publicidad, o soporte comercial), se estarían violando los términos y se necesitaría adquirir una *Premium API*.
2.  **Competencia de mercado:** Los ToS establecen que la API no puede ser utilizada como un sustituto de productos antivirus ni integrarse en proyectos que puedan perjudicar directa o indirectamente a la industria de los antivirus.
3.  **Límites de uso (*Rate Limits*):** La *Public API* impone un límite estricto de 4 *requests* por minuto y 500 *requests* por día. Si una API Key supera estos límites, la cuenta asociada será bloqueada.
4.  **Privacidad de la información:** Desde un punto de vista legal de privacidad de datos, cualquier URL o archivo enviado a VirusTotal pasa a estar disponible para su base de datos y comunidad de investigadores. Se debe incluir un *disclaimer* legal en la app informando a los usuarios sobre esto, para evitar que envíen URLs que contengan *tokens* de acceso, credenciales en texto plano o *PII* (Personal Identifiable Information).

## Punto de vista de la licencia (*Licensing*)

1.  **Compatibilidad de licencias:** VirusTotal no obliga a utilizar una licencia *open source* en particular. Debido a que la aplicación consumirá la API a través de peticiones HTTP/REST, no se está enlazando (*linking*) bibliotecas o código propietario en el binario. Esto significa que se puede elegir libremente licencias permisivas (como MIT o Apache 2.0) o licencias como GPLv3 sin generar conflictos de compatibilidad.
2.  **Gestión de la *API Key* (El punto crítico):** **Nunca** se debe incluir (*hardcode*) una *API Key* en el código fuente del repositorio público. Hacerlo no sólo expone esa credencial, sino que provocará un abuso inmediato de los *rate limits* compartidos entre todos los usuarios de la app, resultando en el baneo permanente de la cuenta asociada.
3.  **Arquitectura recomendada:** La práctica estándar y legalmente segura en desarrollos *open source* es construir la integración de la API, pero dejar el campo de la *API Key* vacío en la configuración. El usuario final es quien debe registrarse en la plataforma de VirusTotal, obtener su propia *Public API Key* y colocarla en la aplicación. De esta manera, cada usuario acepta los ToS de VirusTotal por su cuenta y asume la responsabilidad de sus propios límites de uso.

## Disclaimer

El *disclaimer* es un buen punto de partida, pero presenta carencias importantes en la mitigación de riesgos y en la claridad sobre las responsabilidades del usuario. 

Si bien aborda la privacidad de manera general y advierte sobre el uso comercial, asume que el usuario entiende las implicancias técnicas de enviar una URL y no lo vincula directamente con los términos legales de la plataforma.

A continuación, el análisis detallado de los puntos a mejorar y propuestas de redacción.

### Análisis crítico del *Disclaimer*

1.  **Falta de advertencia sobre datos sensibles (PII / Credentials):**
    Decir que la URL es indexada en una base de datos pública no es suficiente. El usuario promedio de herramientas de línea de comandos o aplicaciones de escritorio no siempre advierte que una URL puede contener *query parameters* con información confidencial, como *tokens* de sesión (`?token=...`), claves de API, o datos personales. Debés indicarle explícitamente qué **no** enviar.
2.  **Delegación legal incompleta:**
    El texto actual pregunta al usuario si acepta "estos términos" (los de tu mensaje), pero legalmente el usuario debe aceptar los *Terms of Service* (ToS) y la *Privacy Policy* de VirusTotal. Tu aplicación debe actuar únicamente como un canal.
3.  **Ambigüedad sobre la *API Key*:**
    Como determinamos anteriormente, la aplicación no debe incluir tu propia *API Key*. El usuario debe proporcionar la suya. El *disclaimer* debería dejar en claro que habilitar la función implica usar una credencial personal, lo cual hace al usuario responsable de los *rate limits* y del cumplimiento de la licencia.

---

### Propuestas de redacción

Las siguientes opciones están redactadas en inglés, listas para ser integradas en la interfaz de usuario de tu aplicación, siguiendo estándares convencionales para herramientas técnicas.

#### Opción 1: Exhaustiva (Recomendada)
Esta versión es ideal si este es el paso previo a que el usuario ingrese su *API Key*. Cubre todas las bases legales y técnicas.

```text
"VirusTotal is a free service provided by Google that analyses URLs using over 70 antivirus scanners and URL/domain blocklisting services.\n\n"
"⚠ PRIVACY WARNING: Every URL submitted is indexed in VirusTotal's public database and shared with the global security community. NEVER submit URLs containing sensitive information, session tokens, passwords, or Personally Identifiable Information (PII) in their query parameters.\n\n"
"By proceeding, you acknowledge that you are providing your own Personal API Key. You agree that your use of this integration is strictly non-commercial and that you are solely responsible for complying with VirusTotal's Terms of Service and API rate limits.\n\n"
"Do you accept these terms and wish to proceed to configure your API Key?"
```

#### Opción 2: Concisa
Si el espacio en la interfaz es muy limitado o se trata de un *prompt* en la terminal, esta versión condensa la información manteniendo las advertencias críticas.

```text
"⚠ VIRUSTOTAL INTEGRATION\n"
"URLs submitted are stored publicly. Do NOT send URLs containing private data, tokens, or credentials.\n\n"
"This feature requires your own Public API Key and is for non-commercial use only. By enabling this, you agree to VirusTotal's Terms of Service.\n\n"
"Enable VirusTotal scanning and configure API Key?"
```

### Recomendación de implementación

Independientemente del texto que elijas, es una buena práctica incluir hipervínculos (si tu UI lo permite) o URLs en texto plano apuntando directamente a los ToS de VirusTotal, de manera que no haya duda sobre las condiciones que el usuario está aceptando.

```Markdown
By submitting data above, you are agreeing to our [Terms of Service](https://cloud.google.com/terms) and [Privacy Notice](https://cloud.google.com/terms/secops/privacy-notice), and to the sharing of your URL submission with the security community. Please do not submit any personal information; we are not responsible for the contents of your submission. Learn more.
```