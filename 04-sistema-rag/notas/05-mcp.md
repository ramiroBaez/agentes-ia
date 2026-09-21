# MCP: Model Context Protocol

**MCP (Model Context Protocol)** is an open standard, originated by Anthropic,
for connecting AI agents with external tools and data. Think of it as a
"USB-C" for agents: instead of every agent wiring every tool by hand, a
standardized protocol handles discovery, transport and invocation.

## Why it matters

Before MCP, every integration was custom: each tool had its own SDK, protocol
and auth. MCP standardizes how an agent talks to *any* MCP server, so a tool
or data source written by the community works with any compatible client.

## How it is structured

- **MCP server**: exposes tools, resources and prompts over a standard JSON-RPC
  transport (stdio or HTTP/SSE).
- **MCP client**: the agent application that discovers and calls those tools.
- **Tools map to real functions** on the server side; the client only sees
  their contracts (name, description, parameters).

## Typical workflow

1. **Consume** an existing community server (filesystem, PostgreSQL, web
   retrieval...) from an agent or desktop client — use its tools without
   writing them.
2. **Create your own server**: wrap one of your own functions (a knowledge
   search, a database query) in the official Python SDK and expose it, so any
   MCP-compatible client can use it.

## Skills this maps to

- Understanding JSON-RPC and standardized tool contracts.
- Building small, reusable services instead of monolithic agents.
- Designing tools (clear names and descriptions) that external clients can
  discover and use correctly.