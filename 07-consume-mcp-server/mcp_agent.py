r"""Project 7 - Consume an existing MCP server (Postgres).

Connects to an MCP server that we did NOT write ourselves
(@modelcontextprotocol/server-postgres, a community package) and its tools
show up as just another tool of the agent, exactly like first-class function
calling. As always, the model decides when to use the store tools (from
Project 5) and when to query the real database through MCP.

The database is self-contained: the project ships a docker-compose.yml with a
Postgres and an auto-loaded seed (tiny academic system: students, courses,
subjects). No external database or credentials required. Of course, you can
point MCP_CONNECTION_STRING at any other Postgres.

The postgres MCP server exposes a single tool, `query`, that only allows
READ-ONLY SQL.

Deliverable: a working demo of your agent using at least one tool exposed by
an external MCP server (here: live queries against a real database).

Usage:
    venv\Scripts\python.exe 07-consume-mcp-server\mcp_agent.py
"""

import operator
import sys
from typing import Annotated, TypedDict

from google.genai import types
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

import mcp_client
import provider

SYSTEM_PROMPT = (
    "You are an agent that combines the online store from Project 5 with a "
    "real database (small academic system: students, courses, subjects).\n"
    "1) Store: help with products, stock, exact money calculations and coupons. "
    "For any money calculation ALWAYS use calc_total (the model makes "
    "arithmetic mistakes, the function doesn't).\n"
    "2) Database: ALWAYS use the MCP tool 'query' to run read-only SQL "
    "(SELECT). The tables are PascalCase and need double quotes in Postgres "
    "(e.g. FROM \"Estudiante\", NOT FROM Estudiante). If you don't know which "
    "tables exist, run query with "
    "SELECT table_name FROM information_schema.tables WHERE table_schema='public'.\n"
    "ALWAYS answer in Spanish, because the user is Spanish-speaking. The "
    "products and the table names stay in English, but the answer and the "
    "explanations must be in Spanish."
)

# ------------------------------------------------------ store tools (from P5)
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


def search_product(term: str) -> str:
    """Search the catalog by partial text or list the full catalog."""
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


def check_stock(name: str) -> str:
    """Return the available stock of an exact catalog product."""
    data = CATALOG.get(name.lower().strip())
    if not data:
        return f"No tengo '{name}' en el catálogo."
    return f"Stock de '{name}': {data['stock']} unidades."


def calc_total(product: str, quantity: int) -> str:
    """Calculate subtotal, VAT and total for buying a quantity of a product."""
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


def apply_coupon(code: str) -> str:
    """Validate a discount coupon code and return its discount percentage."""
    code = code.strip().upper()
    discount = COUPONS.get(code)
    if discount is None:
        return f"El cupón '{code}' no es válido o está vencido."
    return f"Cupón '{code}' válido: otorga un {discount * 100:.0f}% de descuento."


search_decl = types.FunctionDeclaration(
    name="search_product",
    description=(
        "Search products in the store catalog by partial text in the name. "
        "Use it when you don't know the exact product name or want to see "
        "what options exist. If the customer wants to see the whole catalog, "
        "pass 'all' or '*' as the search text."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "term": {
                "type": "string",
                "description": "Text to search inside the product names.",
            },
        },
        "required": ["term"],
    },
)

stock_decl = types.FunctionDeclaration(
    name="check_stock",
    description="Return the available stock of an exact catalog product.",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Exact name of the product to check.",
            },
        },
        "required": ["name"],
    },
)

total_decl = types.FunctionDeclaration(
    name="calc_total",
    description=(
        "Calculate subtotal, VAT (21%) and total for buying a quantity of a "
        "product. Use it for any money calculation: the model makes arithmetic "
        "mistakes, this function doesn't."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "product": {
                "type": "string",
                "description": "Exact catalog product name.",
            },
            "quantity": {
                "type": "integer",
                "description": "Number of units to buy.",
            },
        },
        "required": ["product", "quantity"],
    },
)

coupon_decl = types.FunctionDeclaration(
    name="apply_coupon",
    description=(
        "Validate a discount coupon code and return the discount percentage "
        "it grants."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Coupon code to validate.",
            },
        },
        "required": ["code"],
    },
)

# The store tools are declared above; the MCP 'query' tool is discovered at
# runtime by mcp_client.connect(). FUNCTIONS and TOOLS are assembled in main()
# AFTER connect(), because the MCP declarations only exist once the server is
# up.
FUNCTIONS = []
TOOLS = {}


# ------------------------------------------------------------ the state graph
class State(TypedDict):
    messages: Annotated[list, operator.add]


def summarize_turn(turn) -> str:
    """What the model decided this turn: call tools or reply in text."""
    calls = turn.get("calls", [])
    if calls:
        return "llamar herramienta(s): " + ", ".join(c["name"] for c in calls)
    return "responder en texto"


def agent_node(state: State) -> dict:
    """'Reason' node: asks the model and appends its turn to the history."""
    print(f"  [Nodo agente] Consultando al modelo (memoria: {len(state['messages'])} mensajes)...")
    turn = provider.chat(
        state["messages"],
        system=SYSTEM_PROMPT,
        funcs=FUNCTIONS,
    )
    print(f"  [Nodo agente] El modelo decidió: {summarize_turn(turn)} (proveedor: {provider.LAST_PROVIDER})")
    return {"messages": [turn]}


def tools_node(state: State) -> dict:
    """'Act' node: executes the function calls the model requested."""
    agent_turn = state["messages"][-1]
    results = []
    for call in agent_turn["calls"]:
        print(f"  [Nodo herramientas] Ejecutando {call['name']} con {call['args']}")
        func = TOOLS.get(call["name"])
        try:
            result = func(**(call["args"] or {}))
        except Exception as e:
            result = f"Error al ejecutar la herramienta {call['name']}: {e}"
        preview = str(result)
        print(f"  [Nodo herramientas] Resultado: {preview[:400]}{'...' if len(preview) > 400 else ''}")
        results.append(
            {
                "id": call["id"],
                "name": call["name"],
                "result": result,
            }
        )
    return {"messages": [provider.tool_results(results)]}


def route(state: State) -> str:
    """Conditional branching: did the model ask for tools or reply in text?"""
    last = state["messages"][-1]
    if last["role"] == "agent" and last.get("calls"):
        return "tools"
    return END


def build_graph():
    """Build the graph: nodes, edges and the conditional branching."""
    graph = StateGraph(State)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        route,
        {"tools": "tools", END: END},
    )
    graph.add_edge("tools", "agent")

    return graph.compile(checkpointer=MemorySaver())


def main():
    global FUNCTIONS, TOOLS
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # The store tools (declared above) plus the MCP 'query' tool discovered at
    # runtime. Assembled here AFTER connect(): the MCP declarations only exist
    # once the server is up.
    FUNCTIONS = [search_decl, stock_decl, total_decl, coupon_decl] + mcp_client.declarations
    TOOLS = {
        "search_product": search_product,
        "check_stock": check_stock,
        "calc_total": calc_total,
        "apply_coupon": apply_coupon,
        **mcp_client.DISPATCH,
    }

    graph = build_graph()
    print(f"Proveedores (con respaldo): {', '.join(provider.PROVIDERS)}")
    run_config = {
        "configurable": {"thread_id": "customer-session"},
        "recursion_limit": 10,
    }

    print("Agente de tienda + Postgres vía MCP (LangGraph). ¿Qué necesitás?")
    print("Ej.: '¿qué tablas existen?' · '¿cuántos estudiantes hay?' · '¿cuánto por 2 laptops?'")
    print("Escribí 'salir' para terminar la sesión.\n")

    while True:
        question = input("Vos> ").strip()
        if not question:
            continue
        if question.lower() in ("salir", "exit", "quit", "q"):
            print("¡Hasta la próxima!")
            break

        try:
            result = graph.invoke(
                {"messages": [provider.user_msg(question)]},
                config=run_config,
            )
        except Exception as e:
            print(f"  [Error] No pude procesar la pregunta: {str(e)[:160]}\n")
            continue

        messages = result["messages"]
        last_agent = next(
            (m for m in reversed(messages) if m["role"] == "agent"), None
        )
        if last_agent is not None and last_agent.get("text"):
            print(f"Agente> {last_agent['text']}\n")


if __name__ == "__main__":
    mcp_client.connect()
    try:
        main()
    finally:
        mcp_client.close()