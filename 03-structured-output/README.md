# Proyecto 3 — Salida estructurada con Pydantic / Project 3 — Structured Output with Pydantic

**🌐 Idioma / Language:** [Español](#español) · [English](#english)

---

<a id="español"></a>
## 🇪🇸 Español

### Resumen

Toma texto libre — un **mensaje de pedido de cliente** — y, usando **structured outputs** (JSON schema) más **Pydantic**, extrae un objeto `Order` validado, listo para insertar en una base de datos.

El modelo nunca devuelve texto suelto: la API está forzada a responder con un esquema, y Pydantic lo valida en objetos Python tipados.

### Cómo funciona

1. **Definís el esquema con Pydantic** — `OrderLine` (product + quantity ≥ 1), `ExtractedOrder` (customer, priority como `Literal["low","medium","high"]`, items). Pydantic ES el JSON schema que se manda a la API.
2. **Mandás una instrucción de sistema** — le dice al modelo cómo normalizar el texto: convertir *"three" → 3*, clasificar la urgencia por el tono, no inventar productos, y extraer igual si el pedido viene en español o en inglés.
3. **Pedís salida estructurada** — `response_mime_type="application/json"` + `response_schema=ExtractedOrder`.
4. **Usás el parseo validado** — `response.parsed` ya es un `ExtractedOrder`; si la API devuelve texto crudo, un fallback defensivo lo valida con `model_validate_json`.
5. **Objeto de negocio** — `Order(ExtractedOrder)` agrega `estimated_total`, calculado por **nuestro código** contra el catálogo de la tienda (el mismo del Proyecto 2), nunca por el modelo.

### Cómo ejecutarlo

Desde la raíz del repo (tras el setup inicial del `README.md` raíz):

```bash
venv\Scripts\python.exe 03-structured-output\structured_output.py   # Windows
```

Ejemplo:

```
Extracción de pedido a JSON estructurado.
Escribí el mensaje del cliente en texto libre (o 'salir').

Mensaje del pedido> Soy Juan, necesito 2 wireless headphones y 1 monitor, con urgencia.

  Objeto Pydantic validado:
  Cliente  : Juan
  Prioridad: high
  Ítems:
    - 2 x wireless headphones
    - 1 x monitor
  Total estimado: 275998.98

  JSON listo para guardar en la base:
{
  "customer": "Juan",
  "priority": "high",
  "items": [
    { "product": "wireless headphones", "quantity": 2 },
    { "product": "monitor", "quantity": 1 }
  ],
  "estimated_total": 275998.98
}
```

> **Dato importante:** si el mensaje dice *"three mechanical keyboards"*, la extracción lo convierte a `3` correctamente, pero `estimated_total` devuelve `None` cuando el producto extraído no matchea exacto contra el catálogo (ej. plural *"keyboards"* vs la clave *"keyboard"*). Extracción y resolución del catálogo son dos pasos separados — a propósito.

### Habilidades cubiertas

- Structured outputs: `response_mime_type` + `response_schema` (modo JSON)
- Esquemas Pydantic: `BaseModel`, `Field` (description, `ge=1`), `Literal` para valores permitidos
- Fallback defensivo: validar texto crudo de la API con `model_validate_json`
- Composición de objeto de negocio (esquema + campo calculado)
- Normalización numérica en la instrucción de sistema ("three" → 3)

---

<a id="english"></a>
## 🇬🇧 English

### Overview

Takes free text — a **customer order message** — and, using **structured outputs** (JSON schema) plus **Pydantic**, extracts a validated `Order` object ready to be inserted into a database.

The model never returns loose text: the API is forced to answer with a schema, and Pydantic validates it into typed Python objects.

### How it works

1. **Define the schema with Pydantic** — `OrderLine` (product + quantity ≥ 1), `ExtractedOrder` (customer, priority as `Literal["low","medium","high"]`, items). Pydantic IS the JSON schema sent to the API.
2. **Send a system instruction** — tells the model how to normalize the text: convert *"three" → 3*, classify the priority from tone, don't invent products, and extract correctly whether the order comes in Spanish or English.
3. **Ask for structured output** — `response_mime_type="application/json"` + `response_schema=ExtractedOrder`.
4. **Use the validated parse** — `response.parsed` is already an `ExtractedOrder`; if the API returns raw text instead, a defensive fallback validates it with `model_validate_json`.
5. **Business object** — `Order(ExtractedOrder)` adds `estimated_total`, computed by **our code** against the store catalog (same one from Project 2), never by the model.

### Getting started

From the repo root (after the initial setup in the root `README.md`):

```bash
venv\Scripts\python.exe 03-structured-output\structured_output.py   # Windows
```

Example:

```
Extracción de pedido a JSON estructurado.
Escribí el mensaje del cliente en texto libre (o 'salir').

Mensaje del pedido> Soy Juan, necesito 2 wireless headphones y 1 monitor, con urgencia.

  Objeto Pydantic validado:
  Cliente  : Juan
  Prioridad: high
  Ítems:
    - 2 x wireless headphones
    - 1 x monitor
  Total estimado: 275998.98

  JSON listo para guardar en la base:
{
  "customer": "Juan",
  "priority": "high",
  "items": [
    { "product": "wireless headphones", "quantity": 2 },
    { "product": "monitor", "quantity": 1 }
  ],
  "estimated_total": 275998.98
}
```

> Note: the demo console output is in Spanish (the repo targets Spanish-speaking audiences), but the code, identifiers and docstrings stay in English — the industry standard.

> **Nice detail:** if the message says *"three mechanical keyboards"*, extraction converts it to `3` correctly, but `estimated_total` returns `None` when the extracted product doesn't match the catalog exactly (e.g. plural *"keyboards"* vs catalog key *"keyboard"*). Extraction and catalog resolution are two separate steps — by design.

### Skills covered

- Structured outputs: `response_mime_type` + `response_schema` (JSON mode)
- Pydantic schemas: `BaseModel`, `Field` (description, `ge=1`), `Literal` for allowed values
- Defensive fallback: validating raw API text with `model_validate_json`
- Business object composition (schema + computed field)
- Numeric normalization in the system instruction ("three" → 3)