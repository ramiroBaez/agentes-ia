# AI Agents — Progressive Hands-on Projects

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)
![License](https://img.shields.io/github/license/ramiroBaez/agentes-ia?style=flat-square)
![Status](https://img.shields.io/badge/status-in%20progress-orange?style=flat-square)

A progressive, hands-on collection of projects for building **LLM-powered agents** end to end: from a first script with function calling to a production-ready agent with RAG, MCP, observability and security guardrails.

Each project builds on the previous one and adds a new layer of complexity. Every project is self-contained — its own folder, its own README, its own setup — and delivers a real, runnable result rather than a toy example.

## Projects

| # | Project | Core skills covered | Status |
|---|---------|--------------------|--------|
| 1 | Function calling | Tool-use loop (model decides → code executes → model answers) | Planned |
| 2 | Agent with multiple tools | Tool selection, error handling, exponential backoff | Planned |
| 3 | Structured output (Pydantic) | JSON mode / structured outputs, schema validation | Planned |
| 4 | Mini RAG system | Chunking, embeddings, vector DB (ChromaDB), semantic search | Planned |
| 5 | First agent framework | LangGraph / Pydantic AI, ReAct loop, short-term memory | Planned |
| 6 | RAG inside a framework agent | Agent with a knowledge-retrieval tool | Planned |
| 7 | Consume an MCP server | Model Context Protocol, external tools | Planned |
| 8 | Custom MCP server | Build and expose your own tools via MCP | Planned |
| 9 | Multi-agent orchestration | Supervisor + specialized workers, iteration limits | Planned |
| 10 | Low-code automation (n8n) | Visual workflows, LLM nodes, real-world use case | Planned |
| 11 | Agent in production | FastAPI packaging, Docker, cloud deploy | Planned |
| 12 | Observability & security | Tracing (LangSmith/Langfuse), rate limiting, human-in-the-loop | Planned |

## Roadmap

The projects above follow a structured learning path covering LLM fundamentals, prompt engineering, working with LLM APIs from code, RAG, agent frameworks, MCP, multi-agent orchestration, low-code automation, production deployment and security guardrails.

## Getting started

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

## Stack

- **Language:** Python 3.11+
- **LLM provider:** Google Gemini (`google-genai`)
- **Data validation:** Pydantic
- **Vector database:** ChromaDB
- **Coming up:** LangGraph / Pydantic AI, FastAPI, Docker, Langfuse

## Repository layout

```
agentes-ia/
├── .env.example          # Environment variables template (API key)
├── requirements.txt      # Shared dependencies
├── 01-function-calling/
├── 02-agent-multi-tools/
├── ...
└── README.md             # This file
```

## License

[MIT](./LICENSE) © Ramiro Baez