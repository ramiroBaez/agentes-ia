# AI Agents — Progressive Hands-on Projects

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![License](https://img.shields.io/github/license/ramiroBaez/agentes-ia?style=flat-square)
![Status](https://img.shields.io/badge/status-in%20progress-orange?style=flat-square)

**🌐 Language / Idioma:** [English](#english) · [Español](#español)

---

<a id="english"></a>
## 🇬🇧 English

A progressive, hands-on collection of projects for building **LLM-powered agents** end to end: from a first script with function calling to a production-ready agent with RAG, MCP, observability and security guardrails.

Each project builds on the previous one and adds a new layer of complexity. Every project is self-contained — its own folder, its own README, its own setup — and delivers a real, runnable result rather than a toy example.

### Projects

| # | Project | Core skills covered | Status |
|---|---------|--------------------|--------|
| 1 | Function calling | Tool-use loop (model decides → code executes → model answers) | ✅ Done |
| 2 | Agent with multiple tools | Tool selection, error handling, exponential backoff | ✅ Done |
| 3 | Structured output (Pydantic) | JSON mode / structured outputs, schema validation | ✅ Done |
| 4 | Mini RAG system | Chunking, embeddings, vector DB (ChromaDB), semantic search | ✅ Done |
| 5 | First agent framework | LangGraph / Pydantic AI, ReAct loop, short-term memory, multi-provider fallback | ✅ Done |
| 6 | RAG inside a framework agent | Agent with a knowledge-retrieval tool | ✅ Done |
| 7 | Consume an MCP server | Model Context Protocol, external tools | ✅ Done |
| 8 | Custom MCP server | Build and expose your own tools via MCP | Planned |
| 9 | Multi-agent orchestration | Supervisor + specialized workers, iteration limits | Planned |
| 10 | Low-code automation (n8n) | Visual workflows, LLM nodes, real-world use case | Planned |
| 11 | Agent in production | FastAPI packaging, Docker, cloud deploy | Planned |
| 12 | Observability & security | Tracing (LangSmith/Langfuse), rate limiting, human-in-the-loop | Planned |

### Roadmap

The projects above follow a structured learning path covering LLM fundamentals, prompt engineering, working with LLM APIs from code, RAG, agent frameworks, MCP, multi-agent orchestration, low-code automation, production deployment and security guardrails.

### Getting started

Requirements: Python 3.11+ and a free [Gemini API key](https://aistudio.google.com/apikey).

```bash
git clone https://github.com/ramiroBaez/agentes-ia.git
cd agentes-ia
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
copy .env.example .env          # Windows — set your GEMINI_API_KEY
# cp .env.example .env          # macOS / Linux
```

Then open any project folder and follow its README.

> All scripts read the API key from the environment (`.env`), never from hardcoded values.

### Stack

- **Language:** Python 3.11+
- **LLM providers:** Google Gemini (`google-genai`), Groq and OpenRouter (OpenAI-compatible), with a failover chain
- **Agent framework:** LangGraph (graphs, conditional branching, checkpoints)
- **Data validation:** Pydantic
- **Vector database:** ChromaDB
- **Coming up:** FastAPI, Docker, Langfuse

### Repository layout

```
agentes-ia/
├── .env.example          # Environment variables template (API key)
├── requirements.txt      # Shared dependencies
├── 01-function-calling/
├── 02-agent-multi-tools/
├── 03-structured-output/
├── 04-sistema-rag/
├── 05-first-agent-framework/
├── 06-rag-inside-framework/
├── 07-consume-mcp-server/
├── ...
└── README.md             # This file
```

### License

[MIT](./LICENSE) © Ramiro Baez

---

<a id="español"></a>
## 🇪🇸 Español

Una colección progresiva y práctica de proyectos para construir **agentes impulsados por LLMs** de punta a punta: desde un primer script con function calling hasta un agente listo para producción con RAG, MCP, observabilidad y guardrails de seguridad.

Cada proyecto se apoya en el anterior y suma una capa nueva de complejidad. Cada proyecto es autocontenido — su propia carpeta, su propio README, su propio setup — y entrega un resultado real y ejecutable, no un ejemplo de juguete.

### Proyectos

| # | Proyecto | Habilidades cubiertas | Estado |
|---|----------|-----------------------|--------|
| 1 | Function calling | Ciclo de uso de herramientas (el modelo decide → el código ejecuta → el modelo responde) | ✅ Hecho |
| 2 | Agente con múltiples herramientas | Selección de herramientas, manejo de errores, backoff exponencial | ✅ Hecho |
| 3 | Salida estructurada (Pydantic) | Modo JSON / structured outputs, validación con esquemas | ✅ Hecho |
| 4 | Mini sistema RAG | Chunking, embeddings, base vectorial (ChromaDB), búsqueda semántica | ✅ Hecho |
| 5 | Primer framework de agentes | LangGraph / Pydantic AI, loop ReAct, memoria de corto plazo, respaldo multi-proveedor | ✅ Hecho |
| 6 | RAG dentro de un agente con framework | Agente con herramienta de recuperación de conocimiento | ✅ Hecho |
| 7 | Consumir un servidor MCP | Model Context Protocol, herramientas externas | ✅ Hecho |
| 8 | Servidor MCP propio | Construir y exponer tus propias herramientas vía MCP | Planeado |
| 9 | Orquestación multi-agente | Supervisor + workers especializados, límites de iteraciones | Planeado |
| 10 | Automatización low-code (n8n) | Flujos visuales, nodos LLM, caso de uso real | Planeado |
| 11 | Agente en producción | Empaquetado con FastAPI, Docker, deploy en la nube | Planeado |
| 12 | Observabilidad y seguridad | Trazabilidad (LangSmith/Langfuse), rate limiting, human-in-the-loop | Planeado |

### Roadmap

Los proyectos anteriores siguen un camino de aprendizaje estructurado que cubre fundamentos de LLMs, prompt engineering, uso de APIs de LLMs desde código, RAG, frameworks de agentes, MCP, orquestación multi-agente, automatización low-code, deployment en producción y guardrails de seguridad.

### Primeros pasos

Requisitos: Python 3.11+ y una [API key de Gemini](https://aistudio.google.com/apikey) gratuita.

```bash
git clone https://github.com/ramiroBaez/agentes-ia.git
cd agentes-ia
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
copy .env.example .env          # Windows — poné tu GEMINI_API_KEY
# cp .env.example .env          # macOS / Linux
```

Después entrá a la carpeta de cualquier proyecto y seguí su README.

> Todos los scripts leen la API key desde el entorno (`.env`), nunca de valores hardcodeados.

### Stack

- **Lenguaje:** Python 3.11+
- **Proveedores de LLM:** Google Gemini (`google-genai`), Groq y OpenRouter (compatibles OpenAI), con cadena de respaldo
- **Framework de agentes:** LangGraph (grafos, ramificación condicional, checkpoints)
- **Validación de datos:** Pydantic
- **Base vectorial:** ChromaDB
- **Próximamente:** FastAPI, Docker, Langfuse

### Estructura del repositorio

```
agentes-ia/
├── .env.example          # Template de variables de entorno (API key)
├── requirements.txt      # Dependencias compartidas
├── 01-function-calling/
├── 02-agent-multi-tools/
├── 03-structured-output/
├── 04-sistema-rag/
├── 05-first-agent-framework/
├── 06-rag-inside-framework/
├── 07-consume-mcp-server/
├── ...
└── README.md             # Este archivo
```

### Licencia

[MIT](./LICENSE) © Ramiro Baez