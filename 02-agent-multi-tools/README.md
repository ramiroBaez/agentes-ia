# Project 2 — Agent with Multiple Tools / Proyecto 2 — Agente con múltiples herramientas

**🌐 Language / Idioma:** [English](#english) · [Español](#español)

---

<a id="english"></a>
## 🇬🇧 English

### Overview

Extends Project 1 from **one** tool to **four tools** of a fictional online store, and adds **error handling with retries (exponential backoff)** so the agent survives network / API failures. An interactive console lets you talk to the agent and watch it decide which tool to use.

The dispatch table (`TOOLS`) maps the tool name the model emits to the real function we execute — the same pattern agent frameworks automate later.

### The tools

| Tool | What it does |
|------|--------------|
| `search_product(term)` | Partial-text search over the catalog, or list the whole catalog (`"all"`) |
| `check_stock(product)` | Returns the available stock of an exact product |
| `calculate_total(product, quantity)` | Subtotal, VAT (21%) and total — exact money math our code does, not the model |
| `apply_coupon(code)` | Validates a discount coupon and returns the discount % |

### Error handling: exponential backoff

Every API call goes through `call_with_retries`: on failure it retries waiting **1s, 2s, 4s, 8s…** up to `MAX_RETRIES` attempts. If all retries fail, the agent tells you it ran out of retries instead of crashing.

To **simulate API failures** without cutting the network:

```bash
$env:SIMULATE_FAILURES = "1"                       # PowerShell
# set SIMULATE_FAILURES=1                           # cmd
venv\Scripts\python.exe 02-agent-multi-tools\multi_tool_agent.py
```

You'll see simulated timeouts and the retries kicking in.

### Getting started

From the repo root (after the initial setup in the root `README.md`):

```bash
venv\Scripts\python.exe 02-agent-multi-tools\multi_tool_agent.py   # Windows
```

Example session:

```
Online store agent. Tell me what you need.
I can help you with these actions:
  - search_product
  - check_stock
  - calculate_total
  - apply_coupon
Type 'exit' to quit.

You> What are the store's products?
  [Turn 1/5] Querying the model...
  Model requested: search_product with {'term': 'all'}
  Result of search_product: Full catalog:
- wireless headphones: $12,999.99 (stock: 12)
- mechanical keyboard: $18,999.00 (stock: 5)
- gaming mouse: $7,999.50 (stock: 0)
- laptop: $549,999.00 (stock: 3)
- monitor: $249,999.00 (stock: 8)
- hd webcam: $15,999.00 (stock: 20)
Agent> ...
```

### Skills covered

- Multiple `FunctionDeclaration`s and a shared `Tool`
- Dispatch map: model tool name → real Python function
- Rebuilding the conversation with `function_call` / `function_response` parts
- Turn loop with a `MAX_TURNS` limit to prevent runaway loops
- Retries with exponential backoff and a simulation switch for testing
- Interactive REPL console (`input()` loop)

---

<a id="español"></a>
## 🇪🇸 Español

### Resumen

Amplía el Proyecto 1 de **una** herramienta a **cuatro herramientas** de una tienda online ficticia, y suma **manejo de errores con reintentos (backoff exponencial)** para que el agente sobreviva a fallos de red/API. Una consola interactiva te deja hablar con el agente y ver cómo decide qué herramienta usar.

La tabla de dispatch (`TOOLS`) mapea el nombre de herramienta que emite el modelo con la función real que ejecutamos nosotros — el mismo patrón que luego automatizan los frameworks de agentes.

### Las herramientas

| Herramienta | Qué hace |
|-------------|----------|
| `search_product(term)` | Búsqueda por texto parcial en el catálogo, o listar el catálogo completo (`"all"`) |
| `check_stock(product)` | Devuelve el stock de un producto exacto |
| `calculate_total(product, quantity)` | Subtotal, IVA (21%) y total — las cuentas exactas las hace nuestro código, no el modelo |
| `apply_coupon(code)` | Valida un cupón de descuento y devuelve el % de descuento |

### Manejo de errores: backoff exponencial

Cada llamada a la API pasa por `call_with_retries`: en caso de fallo reintenta esperando **1s, 2s, 4s, 8s…** hasta `MAX_RETRIES` intentos. Si se agotan, el agente avisa que se quedó sin reintentos en vez de crashear.

Para **simular fallos de API** sin cortar la red:

```bash
$env:SIMULATE_FAILURES = "1"                       # PowerShell
# set SIMULATE_FAILURES=1                           # cmd
venv\Scripts\python.exe 02-agent-multi-tools\multi_tool_agent.py
```

Vas a ver timeouts simulados y cómo entran en juego los reintentos.

### Cómo ejecutarlo

Desde la raíz del repo (tras el setup inicial del `README.md` raíz):

```bash
venv\Scripts\python.exe 02-agent-multi-tools\multi_tool_agent.py   # Windows
```

### Habilidades cubiertas

- Varios `FunctionDeclaration`s y un `Tool` compartido
- Mapa de dispatch: nombre de herramienta del modelo → función Python real
- Rearmar la conversación con partes `function_call` / `function_response`
- Loop de turnos con límite `MAX_TURNS` para evitar loops infinitos
- Reintentos con backoff exponencial y switch de simulación para probar
- Consola REPL interactiva (`input()` loop)