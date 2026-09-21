r"""Project 6 - RAG inside a framework agent (LangGraph).

To the Project 5 agent (the store, with memory and conditional branching) we
add Project 4 as just ANOTHER TOOL: retrieval over the repo's knowledge base.
Now the model decides when to search the catalog, when to calculate, and when
to retrieve knowledge from the notes — the framework's ReAct loop orchestrates
it all exactly like any other function call.

The index is the same `notas_agentes` collection Project 4 uses (shared
ChromaDB folder); pass --reindex to refresh it.

Deliverable: the conversational memory agent from Project 5 plus a knowledge-
retrieval (RAG) tool — "RAG inside a framework agent".

Usage:
    venv\Scripts\python.exe 06-rag-inside-framework\rag_agent.py            # existing index
    venv\Scripts\python.exe 06-rag-inside-framework\rag_agent.py --reindex  # rebuild the index
"""

import argparse
import operator
from typing import Annotated, TypedDict

from google.genai import types
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

import provider
import rag

SYSTEM_PROMPT = (
    "You are an agent that combines the online store from Project 5 with the "
    "user's AI Agents study notes.\n"
    "1) Store: help with products, stock, exact money calculations and coupons. "
    "For any money calculation ALWAYS use calc_total (the model makes arithmetic "
    "mistakes, the function doesn't).\n"
    "2) Theory: if the question is about the AI agents course notes (phases, "
    "ReAct, RAG, MCP, prompt engineering, frameworks, production, security, "
    "multi-agent...) ALWAYS use search_notes to retrieve the real information "
    "and answer citing what it returns; if the notes don't contain it, say so "
    "honestly."
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


def search_product(term: str) -> str:
    """Search the catalog by partial text or list the full catalog."""
    t = term.lower().strip()
    if t in ("", "all", "everything", "*", "list", "catalog"):
        return "Full catalog:\n" + "\n".join(
            f"- {name}: ${data['price']:,.2f} (stock: {data['stock']})"
            for name, data in CATALOG.items()
        )
    matches = [
        f"- {name}: ${data['price']:,.2f} (stock: {data['stock']})"
        for name, data in CATALOG.items()
        if t in name
    ]
    if not matches:
        return f"No products found containing '{term}'."
    return "Products found:\n" + "\n".join(matches)


def check_stock(name: str) -> str:
    """Return the available stock of an exact catalog product."""
    data = CATALOG.get(name.lower().strip())
    if not data:
        return f"I don't have '{name}' in the catalog."
    return f"Stock of '{name}': {data['stock']} units."


def calc_total(product: str, quantity: int) -> str:
    """Calculate subtotal, VAT and total for buying a quantity of a product."""
    data = CATALOG.get(product.lower().strip())
    if not data:
        return f"I don't have '{product}' in the catalog."
    if quantity <= 0:
        return "The quantity must be greater than zero."
    subtotal = data["price"] * quantity
    vat = subtotal * VAT
    total = subtotal + vat
    return (
        f"Product: {product.lower().strip()}\n"
        f"Unit price: ${data['price']:,.2f}\n"
        f"Quantity: {quantity}\n"
        f"Subtotal: ${subtotal:,.2f}\n"
        f"VAT ({VAT * 100:.0f}%): ${vat:,.2f}\n"
        f"Total: ${total:,.2f}"
    )


def apply_coupon(code: str) -> str:
    """Validate a discount coupon code and return its discount percentage."""
    code = code.strip().upper()
    discount = COUPONS.get(code)
    if discount is None:
        return f"Coupon '{code}' is not valid or has expired."
    return f"Coupon '{code}' is valid: {discount * 100:.0f}% discount."


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


# ---------------------------------------------------------------- new tool: RAG
ACTIVE_COLLECTION = None


def search_notes(query: str) -> str:
    """Search the query in the AI Agents notes and return the most relevant chunks."""
    if ACTIVE_COLLECTION is None:
        return "The notes index is not initialized."
    try:
        results = rag.retrieve(ACTIVE_COLLECTION, query, top_k=4)
    except Exception as e:
        return f"Error searching the notes: {str(e)[:150]}"
    if not results:
        return "I found nothing in the notes for that query."
    return "\n\n---\n\n".join(
        f"[{r['source']}] (distance {r['distance']:.3f})\n{r['text']}"
        for r in results
    )


notes_decl = types.FunctionDeclaration(
    name="search_notes",
    description=(
        "Search the user's AI Agents course notes and return the most relevant "
        "chunks with their source. Use it when the question is about agent "
        "theory: course phases, ReAct loop, RAG, MCP, prompt engineering, "
        "frameworks, production, security, multi-agent... "
        "(e.g. 'What is MCP?', 'explain the ReAct loop', 'what does the "
        "security phase cover?')."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The question or topic to search inside the notes.",
            },
        },
        "required": ["query"],
    },
)

# The model picks between the store tools (reused from Project 5, unchanged)
# and the new knowledge one.
FUNCTIONS = [search_decl, stock_decl, total_decl, coupon_decl, notes_decl]
TOOLS = {
    "search_product": search_product,
    "check_stock": check_stock,
    "calc_total": calc_total,
    "apply_coupon": apply_coupon,
    "search_notes": search_notes,
}


# ------------------------------------------------------------ the state graph
class State(TypedDict):
    messages: Annotated[list, operator.add]


def summarize_turn(turn) -> str:
    """What the model decided this turn: call tools or reply in text."""
    calls = turn.get("calls", [])
    if calls:
        return "call tool(s): " + ", ".join(c["name"] for c in calls)
    return "reply in text"


def agent_node(state: State) -> dict:
    """'Reason' node: asks the model and appends its turn to the history."""
    print(f"  [Agent node] Asking the model (memory: {len(state['messages'])} messages)...")
    turn = provider.chat(
        state["messages"],
        system=SYSTEM_PROMPT,
        funcs=FUNCTIONS,
    )
    print(f"  [Agent node] Model decided: {summarize_turn(turn)} (provider: {provider.LAST_PROVIDER})")
    return {"messages": [turn]}


def tools_node(state: State) -> dict:
    """'Act' node: executes the function calls the model requested."""
    agent_turn = state["messages"][-1]
    results = []
    for call in agent_turn["calls"]:
        print(f"  [Tools node] Running {call['name']} with {call['args']}")
        func = TOOLS.get(call["name"])
        try:
            result = func(**(call["args"] or {}))
        except Exception as e:
            result = f"Error executing the tool {call['name']}: {e}"
        preview = str(result)
        print(f"  [Tools node] Result: {preview[:400]}{'...' if len(preview) > 400 else ''}")
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
    global ACTIVE_COLLECTION
    parser = argparse.ArgumentParser(
        description="Framework agent + a RAG tool over the course notes."
    )
    parser.add_argument(
        "--reindex",
        action="store_true",
        help="Rebuild the notes index from scratch before chatting.",
    )
    args = parser.parse_args()

    ACTIVE_COLLECTION = rag.get_collection(args.reindex)

    graph = build_graph()
    print(f"Providers (with fallback): {', '.join(provider.PROVIDERS)}")
    run_config = {
        "configurable": {"thread_id": "customer-session"},
        "recursion_limit": 10,
    }

    print("Store + notes agent (LangGraph + RAG). What do you need?")
    print("Try: 'What is MCP?' · 'how much for 2 monitors?'")
    print("Type 'quit' to end the session.\n")

    while True:
        question = input("You> ").strip()
        if not question:
            continue
        if question.lower() in ("quit", "exit", "q", "salir"):
            print("See you next time!")
            break

        try:
            result = graph.invoke(
                {"messages": [provider.user_msg(question)]},
                config=run_config,
            )
        except Exception as e:
            print(f"  [Error] Could not process the question: {str(e)[:160]}\n")
            continue

        messages = result["messages"]
        last_agent = next(
            (m for m in reversed(messages) if m["role"] == "agent"), None
        )
        if last_agent is not None and last_agent.get("text"):
            print(f"Agent> {last_agent['text']}\n")


if __name__ == "__main__":
    main()