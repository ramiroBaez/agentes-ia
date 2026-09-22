# Proyecto 1 — Function calling / Project 1 — Function Calling

**🌐 Idioma / Language:** [Español](#español) · [English](#english)

---

<a id="español"></a>
## 🇪🇸 Español

### Resumen

Le damos al modelo **una sola herramienta real** y completamos, desde nuestro código, el ciclo completo de **6 pasos del function calling**: el modelo pide la herramienta → nuestro código la ejecuta → el modelo arma la respuesta final.

Este es el esqueleto de **todo agente**: este mismo loop, repetido con más herramientas, es lo que los frameworks de agentes (LangGraph, Pydantic AI…) automatizan en los proyectos posteriores.

### Cómo funciona

| Paso | Quién actúa | Qué pasa |
|------|-------------|----------|
| 1 | Nosotros | Le describimos la herramienta al modelo: nombre, descripción y parámetros (su **contrato**) |
| 2 | Nosotros | Mandamos el mensaje: *"¿Cuál es el clima en La Plata?"* |
| 3 | El modelo | En vez de texto, devuelve una estructura: *usar `get_weather` con `{"city": "La Plata"}`* |
| 4 | **Nosotros** | Ejecutamos de verdad `get_weather("La Plata")` |
| 5 | **Nosotros** | Le devolvemos el resultado real al modelo |
| 6 | El modelo | Arma la respuesta final: *"El clima en La Plata es de 18°C, nublado."* |

> **Clave:** el modelo **nunca ejecuta código**. Solo decide y pide; quien ejecuta somos nosotros. La declaración de la herramienta es el único contrato que ve el modelo — nunca la función en sí.

### Cómo ejecutarlo

Desde la raíz del repo (tras el setup inicial del `README.md` raíz):

```bash
venv\Scripts\python.exe 01-function-calling\function_calling.py   # Windows
# source venv/bin/activate && python 01-function-calling/function_calling.py  # macOS / Linux
```

Salida esperada:

```
El modelo pidió la herramienta: get_weather con {'city': 'La Plata'}
El código ejecutó get_weather() y devolvió: 18°C, cloudy

Respuesta final del modelo:
El clima en La Plata es de 18°C, nublado.
```

### Probá distintos caminos

Cambiá la variable `question` en `function_calling.py`:

| Pregunta | Qué debería pasar |
|----------|-------------------|
| `"¿Cuál es el clima en La Plata?"` | Usa la herramienta y arma la respuesta final |
| `"Contame un chiste"` | Responde directo (entra al `else`) |
| `"¿Cuál es el clima en Tucumán?"` | Usa la herramienta, pero como Tucumán no está en el dataset, la respuesta del modelo toma nuestro "No data available for Tucumán." |

### Habilidades cubiertas

- Declaración de herramientas con `FunctionDeclaration` (parámetros en JSON Schema)
- El ciclo de 6 pasos del function calling con `google-genai`
- Rearmar el historial de la conversación con partes `function_call` / `function_response`
- Leer la API key de forma segura desde `.env`

---

<a id="english"></a>
## 🇬🇧 English

### Overview

Give the model **one real tool** and complete the full **6-step tool-use cycle** by hand: the model requests the tool → our code executes it → the model composes the final answer.

This is the skeleton of **every agent**: this same loop, repeated with more tools, is what agent frameworks (LangGraph, Pydantic AI…) automate in later projects.

### How it works

| Step | Who acts | What happens |
|------|----------|--------------|
| 1 | Us | Describe the tool to the model: name, description and parameters (its **contract**) |
| 2 | Us | Send the message: *"What is the weather in La Plata?"* |
| 3 | Model | Instead of text, returns a structure: *use `get_weather` with `{"city": "La Plata"}`* |
| 4 | **Us** | Execute the real `get_weather("La Plata")` |
| 5 | **Us** | Give the real result back to the model |
| 6 | Model | Compose the final answer: *"The weather in La Plata is 18°C, cloudy."* |

> **Key idea:** the model **never runs code**. It only decides and asks; we execute. The tool declaration is the only contract the model sees — never the function source.

### Getting started

From the repo root (after the initial setup in the root `README.md`):

```bash
venv\Scripts\python.exe 01-function-calling\function_calling.py   # Windows
# source venv/bin/activate && python 01-function-calling/function_calling.py  # macOS / Linux
```

Expected output:

```
El modelo pidió la herramienta: get_weather con {'city': 'La Plata'}
El código ejecutó get_weather() y devolvió: 18°C, cloudy

Respuesta final del modelo:
El clima en La Plata es de 18°C, nublado.
```

> Note: the demo console output is in Spanish (the repo targets Spanish-speaking audiences), but the code, identifiers and docstrings stay in English — the industry standard.

### Try it

Change the `question` variable in `function_calling.py`:

| Question | Expected behavior |
|----------|-------------------|
| `"What is the weather in La Plata?"` | Uses the tool and composes the final answer |
| `"Tell me a joke"` | Answers directly (goes to the `else` branch) |
| `"What is the weather in Tucumán?"` | Uses the tool, but as Tucumán is not in the dataset, the model picks up our "No data available for Tucumán." |

### Skills covered

- Tool declarations with `FunctionDeclaration` (JSON Schema parameters)
- The 6-step function-calling cycle with `google-genai`
- Rebuilding the conversation history with `function_call` / `function_response` parts
- Reading API keys safely from `.env`