r"""Project 5 - Your first agent with a framework (LangGraph).

Recreates the store agent from Project 2, but instead of hand-writing the
ReAct loop, it is delegated to a framework: we define LangGraph's state graph
(nodes + conditional branching) and the framework handles the loop, the step
limit and the short-term memory (MemorySaver checkpointer).

The model call goes through the provider.py adapter: PROVIDERS builds a
failover chain (gemini,groq,openrouter). If a provider fails (quota, network,
auth), chat() moves to the next one without touching this code.

Deliverable: a conversational agent with memory, 4 tools and a flow that
branches depending on what the model decides.

Usage:
    venv\Scripts\python.exe 05-first-agent-framework\langgraph_agent.py
"""

import operator
from typing import Annotated, TypedDict

from google.genai import types
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

import provider

SYSTEM_PROMPT = (
    "You are the support agent of an online store. Help customers with "
    "products, stock, exact money calculations and coupons. For any money "
    "calculation ALWAYS use calc_total (the model makes arithmetic mistakes, "
    "the function doesn't). If you don't know a product or a coupon, say so "
    "honestly."
)

# ---------------------------------------------------------------- tools
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


# ---------------------------------------------------- declare the contract of each one
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

FUNCTIONS = [
    search_decl,
    stock_decl,
    total_decl,
    coupon_decl,
]

# Dispatch: maps the name the model uses to the real function executed by the
# 'tools' node. The framework only orchestrates; who executes is us.
TOOLS = {
    "search_product": search_product,
    "check_stock": check_stock,
    "calc_total": calc_total,
    "apply_coupon": apply_coupon,
}


# ------------------------------------------------------------ the state graph
class State(TypedDict):
    # The agent's 'memory': the full conversation history in the adapter's
    # canonical format (dicts). Annotated + operator.add tells LangGraph how
    # to merge each node's messages: it accumulates (list addition), it does
    # not replace them.
    messages: Annotated[list, operator.add]


def summarize_turn(turn) -> str:
    """What the model decided this turn: call tools or reply in text."""
    calls = turn.get("calls", [])
    if calls:
        return "call tool(s): " + ", ".join(c["name"] for c in calls)
    return "reply in text"


def agent_node(state: State) -> dict:
    """'Reason' node: asks the model and appends its turn to the history.

    The whole history (state['messages']) travels in the call (provider.py
    adapter). If the model decides to use tools it returns 'calls'; LangGraph
    then routes based on what we return in 'route'.
    """
    print(f"  [Agent node] Asking the model (memory: {len(state['messages'])} messages)...")
    turn = provider.chat(
        state["messages"],
        system=SYSTEM_PROMPT,
        funcs=FUNCTIONS,
    )
    print(f"  [Agent node] Model decided: {summarize_turn(turn)} (provider: {provider.LAST_PROVIDER})")
    return {"messages": [turn]}


def tools_node(state: State) -> dict:
    """'Act' node: executes the function calls the model requested.

    The last message is the agent's turn with its 'calls'. Each one is
    executed through the dispatch and the results are returned (tool_results).
    """
    agent_turn = state["messages"][-1]
    results = []
    for call in agent_turn["calls"]:
        print(f"  [Tools node] Running {call['name']} with {call['args']}")
        func = TOOLS.get(call["name"])
        try:
            result = func(**(call["args"] or {}))
        except Exception as e:
            result = f"Error executing the tool {call['name']}: {e}"
        print(f"  [Tools node] Result: {result}")
        results.append(
            {
                "id": call["id"],
                "name": call["name"],
                "result": result,
            }
        )
    return {"messages": [provider.tool_results(results)]}


def route(state: State) -> str:
    """Conditional branching: did the model ask for tools or reply in text?

    This is 'the flow that branches depending on what the model decides':
    LangGraph looks at the key we return and sends the execution to that node.
    """
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

    # Checkpointer: short-term memory. The history is stored per thread_id, so
    # the agent 'remembers' the conversation between turns without us having to
    # rebuild anything by hand (the framework does it).
    return graph.compile(checkpointer=MemorySaver())


def main():
    graph = build_graph()
    print(f"Providers (with fallback): {', '.join(provider.PROVIDERS)}")
    # Fixed thread_id = one continuous conversation during the whole run.
    # recursion_limit controls the max steps per question (anti infinite loop).
    run_config = {
        "configurable": {"thread_id": "customer-session"},
        "recursion_limit": 10,
    }

    print("Online store agent (LangGraph). What do you need?")
    print("Type 'quit' to end the session.\n")

    while True:
        question = input("You> ").strip()
        if not question:
            continue
        if question.lower() in ("quit", "exit", "q", "salir"):
            print("See you next time!")
            break

        try:
            # A new user message enters the graph; LangGraph merges it with the
            # history stored by the checkpointer (memory).
            result = graph.invoke(
                {"messages": [provider.user_msg(question)]},
                config=run_config,
            )
        except Exception as e:
            print(f"  [Error] Could not process the question: {str(e)[:160]}\n")
            continue

        # The last agent turn is the final answer (we reached END).
        messages = result["messages"]
        last_agent = next(
            (m for m in reversed(messages) if m["role"] == "agent"), None
        )
        if last_agent is not None and last_agent.get("text"):
            print(f"Agent> {last_agent['text']}\n")


if __name__ == "__main__":
    main()