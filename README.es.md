# claude-mcp-debugger

[English](README.md) | [Français](README.fr.md)

Un servidor MCP de depuración para agentes IA de código. Depura **Python, Node.js, Java y JavaScript en el navegador** — como un desarrollador en VS Code.

Compatible con cualquier agente IA que soporte [MCP](https://modelcontextprotocol.io/) — optimizado para **Claude Code** con instalación en un solo comando.

> **Cualquier agente IA, cualquier lenguaje, sin IDE.** Este servidor habla el Model Context Protocol estándar — funciona con Claude Code, pero también con cualquier cliente MCP (Cursor, Windsurf, agentes personalizados, pipelines CI/CD). No necesitas VS Code, IDE, ni interfaz gráfica.

<p align="center">
<img src="assets/browser-debug-demo.gif" alt="Demo depuración navegador en tiempo real">
</p>

## Lenguajes soportados

| Lenguaje | Adaptador | Auto-setup | Requisitos |
|----------|-----------|------------|------------|
| Python | [debugpy](https://github.com/microsoft/debugpy) | `pip install` en el primer uso | Python 3.10+ |
| Node.js | [vscode-js-debug](https://github.com/microsoft/vscode-js-debug) | Descargado en el primer uso | Node.js 18+ |
| Java | [JDT LS](https://github.com/eclipse-jdtls/eclipse.jdt.ls) + [java-debug](https://github.com/microsoft/java-debug) | Descargado en el primer uso (~55 MB) | JDK 17+ |
| JS navegador | vscode-js-debug (pwa-chrome) | Compartido con Node.js | Chrome/Chromium |

## Características

- **22 herramientas de depuración**: ciclo completo — lanzamiento, breakpoints, ejecución paso a paso, inspección, modificación de variables, y más
- **Multi-lenguaje**: Python, Node.js, Java y JavaScript en el navegador a través de una interfaz unificada
- **Depuración en el navegador**: depura JS del lado cliente en Chrome/Chromium — breakpoints, captura de clics, inspección del DOM. Funciona con servidores de desarrollo locales y URLs remotas
- **Autónomo**: no requiere IDE — funciona en modo headless, en CI/CD, en cualquier lugar donde un cliente MCP se ejecute
- **Auto-setup**: todos los adaptadores y dependencias se descargan automáticamente en el primer uso
- **Detección inteligente**: auto-detección del lenguaje por extensión, detección del venv del proyecto (Python), compilación con info de debug (Java)
- **Breakpoints avanzados**: condicionales, por contador, logpoints, y por nombre de función
- **Expansión de variables**: exploración de dicts, listas y objetos con profundidad configurable y filtrado de internals
- **Modificación en vivo**: cambiar valores de variables durante la ejecución
- **Detalles de excepción**: visualización automática del traceback al detenerse en una excepción
- **Multiplataforma**: funciona en Linux, macOS y Windows

## Instalación

### Claude Code (recomendado)

**Linux / macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/bastiencb/claude-mcp-debugger/main/install.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/bastiencb/claude-mcp-debugger/main/install.ps1 | iex
```

> Si aparece un error de política de ejecución, ejecuta primero `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

> **¿Qué hace este comando?** El script copia el servidor en `~/.claude/mcp_debugger/` y agrega una entrada en tu configuración MCP de Claude Code. Eso es todo — puedes revisar el código fuente de [install.sh](install.sh) / [install.ps1](install.ps1) antes de ejecutarlo.

**Después de instalar, reinicia Claude Code.** El depurador estará disponible en todos tus proyectos.

<details>
<summary><b>Claude Code — instalación manual</b></summary>

**1. Copiar archivos:**

Linux / macOS:
```bash
git clone https://github.com/bastiencb/claude-mcp-debugger.git
cp -r claude-mcp-debugger/mcp_debugger ~/.claude/mcp_debugger
```

Windows (PowerShell):
```powershell
git clone https://github.com/bastiencb/claude-mcp-debugger.git
Copy-Item -Recurse claude-mcp-debugger\mcp_debugger $env:USERPROFILE\.claude\mcp_debugger
```

**2. Crear venv e instalar dependencias:**

Linux / macOS:
```bash
python3 -m venv ~/.claude/mcp_debugger/.venv
~/.claude/mcp_debugger/.venv/bin/python3 -m pip install "mcp[cli]>=1.0" debugpy
```

Windows (PowerShell):
```powershell
python -m venv $env:USERPROFILE\.claude\mcp_debugger\.venv
& $env:USERPROFILE\.claude\mcp_debugger\.venv\Scripts\python.exe -m pip install "mcp[cli]>=1.0" debugpy
```

**3. Registrar en Claude Code:**

Linux / macOS:
```bash
claude mcp add -s user -t stdio debugger -e "PYTHONPATH=$HOME/.claude" -- ~/.claude/mcp_debugger/.venv/bin/python3 -m mcp_debugger
```

Windows (PowerShell):
```powershell
claude mcp add -s user -t stdio debugger -e "PYTHONPATH=$($env:USERPROFILE -replace '\\','/')/.claude" -- $env:USERPROFILE\.claude\mcp_debugger\.venv\Scripts\python.exe -m mcp_debugger
```

> Esto escribe en `~/.claude.json` (la config de Claude Code). Verifica con `claude mcp list`.

Luego reinicia Claude Code.

</details>

<details>
<summary><b>Otros clientes MCP (Cursor, Windsurf, agentes personalizados...)</b></summary>

Clona el repositorio donde quieras y apunta tu cliente MCP al servidor:

```bash
git clone https://github.com/bastiencb/claude-mcp-debugger.git /path/to/claude-mcp-debugger
```

Agrega en la configuración MCP de tu cliente:

```json
{
  "command": "python3",
  "args": ["-m", "mcp_debugger"],
  "cwd": "/path/to/claude-mcp-debugger",
  "env": { "PYTHONPATH": "/path/to/claude-mcp-debugger" }
}
```

El servidor expone 22 herramientas con prefijo `debug_` — cualquier cliente MCP puede usarlas.

</details>

### Requisitos

- Python 3.10+ (requerido — el servidor está escrito en Python)
- Node.js 18+ (opcional, para depuración JavaScript/TypeScript)
- JDK 17+ (opcional, para depuración Java)
- Chrome o Chromium (opcional, para depuración JavaScript en el navegador)

## Uso

Una vez instalado, tu agente IA puede depurar código. Estos ejemplos muestran Claude Code, pero las mismas herramientas funcionan idénticamente desde cualquier cliente MCP.

**Python:**
```
Tú: Depura mi script app.py — falla en la línea 42

Claude: [usa debug_launch para iniciar app.py]
        [coloca un breakpoint en la línea 42]
        [continúa la ejecución]
        [inspecciona las variables cuando se alcanza el breakpoint]
        [encuentra el bug y lo explica]
```

**Node.js:**
```
Tú: Depura server.js — el endpoint /api/users devuelve datos incorrectos

Claude: [usa debug_launch con language="node" en server.js]
        [coloca un breakpoint en el handler de la ruta]
        [inspecciona los objetos request y response]
        [identifica el bug en la lógica de consulta]
```

**Java:**
```
Tú: Depura Main.java — el algoritmo de ordenamiento produce un resultado incorrecto

Claude: [usa debug_launch en Main.java — compila automáticamente con javac -g]
        [coloca un breakpoint en el método de ordenamiento]
        [inspecciona el contenido del array y las variables del bucle]
        [evalúa expresiones: names.size(), scores.get("Alice")]
```

**Navegador (Chrome):**
```
Tú: Depura mi frontend — la validación del formulario falla al enviar

Claude: [usa debug_launch en http://localhost:3000]
        [coloca un breakpoint en validator.js]
        ... haces clic en "Enviar" en Chrome ...
        [captura el clic, inspecciona los datos del formulario y los errores]
        [encuentra el bug en la lógica de validación]
```

## Herramientas

| Herramienta | Descripción |
|-------------|-------------|
| **Sesión** | |
| `debug_launch` | Lanzar un programa bajo el depurador (Python, Node.js, Java, navegador) |
| `debug_stop` | Detener la sesión inmediatamente (SIGTERM) |
| `debug_terminate` | Terminación graceful (KeyboardInterrupt, los handlers de limpieza se ejecutan) |
| `debug_status` | Verificar el estado de la sesión, la ubicación y las capacidades |
| **Breakpoints** | |
| `debug_set_breakpoints` | Colocar breakpoints con condiciones, contadores o logpoints |
| `debug_set_function_breakpoints` | Detenerse cuando se llama a una función por nombre |
| `debug_set_exception_breakpoints` | Detenerse en excepciones lanzadas/no capturadas |
| **Ejecución** | |
| `debug_pause` | Pausar un hilo en ejecución (ej. bucle infinito) |
| `debug_continue` | Reanudar hasta el siguiente breakpoint o el final |
| `debug_step_over` | Ejecutar la línea actual, detenerse en la siguiente |
| `debug_step_into` | Entrar en la llamada de función de la línea actual |
| `debug_step_out` | Ejecutar hasta que retorne la función actual |
| `debug_goto` | Saltar a una línea sin ejecutar el código intermedio |
| **Inspección** | |
| `debug_stacktrace` | Obtener la pila de llamadas |
| `debug_variables` | Inspeccionar variables locales/globales (con marcadores expandibles) |
| `debug_expand_variable` | Explorar el contenido de dicts, listas, objetos |
| `debug_evaluate` | Evaluar una expresión en contexto |
| `debug_exception_info` | Obtener tipo, mensaje y traceback de una excepción |
| `debug_source_context` | Mostrar el código fuente alrededor de la línea actual |
| `debug_modules` | Listar los módulos cargados |
| **Modificación** | |
| `debug_set_variable` | Cambiar el valor de una variable durante la ejecución |
| **Salida** | |
| `debug_output` | Obtener la salida stdout/stderr (subprocess y/o eventos DAP) |

## Cómo funciona

**Python:**
1. `debug_launch` inicia tu script bajo [debugpy](https://github.com/microsoft/debugpy) en modo `--wait-for-client`
2. El servidor MCP se conecta como cliente DAP a través de TCP
3. `stop_on_entry` se simula colocando un breakpoint en la primera línea ejecutable (detección basada en AST)

**Node.js:**
1. `debug_launch` inicia [vscode-js-debug](https://github.com/microsoft/vscode-js-debug) como servidor DAP
2. El adaptador lanza tu script y gestiona la depuración multi-sesión (padre + hijo)
3. `stop_on_entry` es manejado nativamente por js-debug

**Java:**
1. `debug_launch` compila automáticamente tu archivo `.java` con `javac -g` (info de debug)
2. [Eclipse JDT LS](https://github.com/eclipse-jdtls/eclipse.jdt.ls) se inicia en modo headless con el plugin [java-debug](https://github.com/microsoft/java-debug)
3. El launcher se comunica vía LSP para resolver la clase main, el classpath, e iniciar una sesión DAP
4. La evaluación de expresiones está completamente soportada (JDT LS compila las expresiones al vuelo)

**Navegador (Chrome):**
1. `debug_launch` inicia vscode-js-debug en modo `pwa-chrome`
2. Chrome abre la URL objetivo (local o remota)
3. Coloca breakpoints por nombre de archivo (ej. `app.js`) — resueltos automáticamente contra los scripts cargados
4. El usuario interactúa con la página, el depurador captura los breakpoints en tiempo real

Los cuatro modos comparten el mismo cliente DAP y la misma interfaz MCP — la experiencia es idéntica.

## Arquitectura

```
mcp_debugger/
├── __init__.py              # Metadatos del paquete
├── __main__.py              # Punto de entrada con auto-setup del venv
├── server.py                # Servidor MCP — 22 herramientas de depuración
├── session.py               # Ciclo de vida de la sesión (agnóstico al lenguaje)
├── dap_client.py            # Cliente DAP (soporte multi-sesión)
└── launchers/
    ├── base.py              # BaseLauncher ABC + LaunchResult
    ├── python_launcher.py   # Integración debugpy
    ├── node_launcher.py     # vscode-js-debug (pwa-node)
    ├── browser_launcher.py  # vscode-js-debug (pwa-chrome)
    ├── java_launcher.py     # JDT LS + java-debug
    └── lsp_client.py        # Cliente LSP/JSON-RPC para JDT LS
```

Agregar un nuevo lenguaje solo requiere un nuevo launcher — el cliente DAP, el gestor de sesión y las herramientas MCP son completamente reutilizables.

## Licencia

MIT
