r"""Project 8 - Build your own MCP server.

In Project 7 we CONSUMED an existing MCP server (@modelcontextprotocol/
server-postgres): we launched it with npx and its tools showed up as agent
tools without writing a single line of that integration. This project flips
the roles: HERE WE ARE the server. It is our own MCP server, built with the
official Python SDK (mcp 2.x, MCPServer class), exposing over the MCP standard
tools we already wrote in previous projects:

  - the 4 store tools from Project 5 (reused as-is),
  - the knowledge-base search (RAG from Project 4/6, via the shared rag.py).

Any compatible client (Claude Desktop, a LangGraph agent, a script using the
mcp SDK, n8n...) connects over stdio, lists the tools with tools/list and runs
them with tools/call. No integration rewrite per client: everyone speaks the
same protocol — that is the whole promise of MCP.

Deliverable: an MCP server of your own, running locally, consumable by any
compatible client.

Usage:
    venv\Scripts\python.exe 08-custom-mcp-server\mcp_server.py            # runs as the server (stdio)
    venv\Scripts\python.exe 08-custom-mcp-server\mcp_server.py --selftest # connects to itself and tests the tools
"""

import asyncio
import json
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

import rag

SERVER_PATH = Path(__file__).resolve()

# ------------------------------------------------------------------ the server
# MCPServer (mcp 2.x) is the evolution of FastMCP. Declaring a tool is just a
# decorator @SERVER.tool() over a plain function: the name, parameter types
# and docstring are converted automatically into the JSON schema the protocol
# requires. Then run_stdio_async() serves tools/list and tools/call over
# stdin/stdout to whichever client connects.
SERVER = MCPServer(
    name="agentes-ia-projects",
    version="0.1.0",
    instructions=(
        "MCP server built in Project 8. It exposes the online store tools "
        "(Project 5) and the knowledge-base search over the AI Agents notes "
        "(Projects 4/6, RAG)."
    ),
)


# ---------------------------------------------------------------- store tools (from P5)
CATALOG = {
    "bluetooth headphones": {"price": 12999.99, "stock": 12},
    "mechanical keyboard": {"price": 18999.00, "stock": 5},
    "gaming mouse": {"price": 7999.50, "stock": 0},
    "laptop": {"price": 549999.00, "stock": 3},
    "monitor": {"price": 249999.00, "stock": 8},
    "hd webcam": {"price": 15999.00, "stock": 20},
}

COUPONS = {"SAVE10": 0.10, "HALF": 0.50}

VAT = 0.21


@SERVER.tool()
def search_product(term: str) -> str:
    """Search the store catalog by partial text in the name or list the full catalog."""
    t = term.lower().strip()
    if t in ("", "all", "everything", "*", "list", "catalog"):
        return "Catálogo completo:\n" + "\n".join(
            f"- {name}: ${data['price']:,.2f} (stock: {data['stock']})"
            for name, data in CATALOG.items()
        )
    matches = [
        f"- {name}: ${data['price']:,.2f} (stock: {data['stock']})"
        for name, data in CATALOG.items()
        if t in name
    ]
    if not matches:
        return f"No encontré productos que contengan '{term}'."
    return "Productos encontrados:\n" + "\n".join(matches)


@SERVER.tool()
def check_stock(name: str) -> str:
    """Return the available stock of an exact catalog product."""
    data = CATALOG.get(name.lower().strip())
    if not data:
        return f"No tengo '{name}' en el catálogo."
    return f"Stock de '{name}': {data['stock']} unidades."


@SERVER.tool()
def calc_total(product: str, quantity: int) -> str:
    """Calculate subtotal, VAT (21%) and total for buying a quantity of a product."""
    data = CATALOG.get(product.lower().strip())
    if not data:
        return f"No tengo '{product}' en el catálogo."
    if quantity <= 0:
        return "La cantidad debe ser mayor que cero."
    subtotal = data["price"] * quantity
    vat = subtotal * VAT
    total = subtotal + vat
    return (
        f"Producto: {product.lower().strip()}\n"
        f"Precio unitario: ${data['price']:,.2f}\n"
        f"Cantidad: {quantity}\n"
        f"Subtotal: ${subtotal:,.2f}\n"
        f"IVA ({VAT * 100:.0f}%): ${vat:,.2f}\n"
        f"Total: ${total:,.2f}"
    )


@SERVER.tool()
def apply_coupon(code: str) -> str:
    """Validate a discount coupon code and return its discount percentage."""
    code = code.strip().upper()
    discount = COUPONS.get(code)
    if discount is None:
        return f"El cupón '{code}' no es válido o está vencido."
    return f"Cupón '{code}' válido: otorga un {discount * 100:.0f}% de descuento."


# ---------------------------------------------------------------- RAG tool (from P4/6)
# Chroma collection loaded once (lazy) and reused between calls.
_COLLECTION = None


@SERVER.tool()
def search_notes(query: str) -> str:
    """Search the AI Agents course notes and return the most relevant chunks (RAG)."""
    global _COLLECTION
    if _COLLECTION is None:
        _COLLECTION = rag.get_collection()
    try:
        results = rag.retrieve(_COLLECTION, query, top_k=4)
    except Exception as e:
        return f"Error al buscar en las notas: {str(e)[:200]}"
    if not results:
        return "No encontré nada en las notas para esa consulta."
    return "\n\n---\n\n".join(
        f"[{r['source']}] (distancia {r['distance']:.3f})\n{r['text']}"
        for r in results
    )


# -------------------------------------------------------------- self-check (--selftest)
async def _selftest():
    """Connect to this very server as an external client would.

    Spawns this same file as a subprocess and connects over stdio, lists the
    available tools and executes a store one and a RAG one, showing the output.
    This is the same wiring any external client (Claude Desktop, another
    agent, n8n) uses behind the scenes.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print(f"Servidor MCP propio levantado. Herramientas disponibles ({len(tools.tools)}):")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description}")

            for name, arguments in [
                ("search_product", {"term": "monitor"}),
                ("calc_total", {"product": "monitor", "quantity": 2}),
                ("search_notes", {"query": "What is MCP?"}),
            ]:
                print(f"\n> {name}({json.dumps(arguments, ensure_ascii=False)})")
                result = await session.call_tool(name, arguments)
                text = "\n".join(
                    p.text for p in result.content if getattr(p, "type", None) == "text"
                )
                print(text[:700] + ("..." if len(text) > 700 else ""))


def main():
    if "--selftest" in sys.argv:
        asyncio.run(_selftest())
    else:
        asyncio.run(SERVER.run_stdio_async())


if __name__ == "__main__":
    main()