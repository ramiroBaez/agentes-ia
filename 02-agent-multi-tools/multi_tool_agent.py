"""Multi-tool agent: one agent, several tools, resilient to API failures.

Extends Project 1 to 4 tools of a fictional online store and adds error
handling with retries (exponential backoff) for failed API calls: the agent
decides which tool fits the user request and never crashes when the network
or the API misbehaves.

To simulate API failures without cutting the network:
    set SIMULATE_FAILURES=1   (PowerShell: $env:SIMULATE_FAILURES = "1")
    python 02-agent-multi-tools/multi_tool_agent.py
"""

import os
import random
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

if not API_KEY or API_KEY == "your_gemini_api_key_here":
    print("Error: set GEMINI_API_KEY in your .env file to call the API.")
    raise SystemExit(1)

client = genai.Client(api_key=API_KEY)

MAX_TURNS = 5
MAX_RETRIES = 5
SIMULATE_FAILURES = os.getenv("SIMULATE_FAILURES") == "1"

# ---------------------------------------------------------------- tools
CATALOG = {
    "wireless headphones": {"price": 12999.99, "stock": 12},
    "mechanical keyboard": {"price": 18999.00, "stock": 5},
    "gaming mouse": {"price": 7999.50, "stock": 0},
    "laptop": {"price": 549999.00, "stock": 3},
    "monitor": {"price": 249999.00, "stock": 8},
    "hd webcam": {"price": 15999.00, "stock": 20},
}

COUPONS = {"SAVE10": 0.10, "HALF": 0.50}


def search_product(term: str) -> str:
    """Search products in the catalog by partial text or list the full catalog."""
    t = term.lower().strip()
    if t in ("", "all", "everything", "*", "list"):
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
        return f"I found no products matching '{term}'."
    return "Products found:\n" + "\n".join(matches)


def check_stock(product: str) -> str:
    """Return the available stock of an exact catalog product."""
    data = CATALOG.get(product.lower().strip())
    if not data:
        return f"I don't have the product '{product}' in the catalog."
    return f"Stock of '{product}': {data['stock']} units."


def calculate_total(product: str, quantity: int) -> str:
    """Calculate subtotal, VAT (21%) and total for buying a product quantity."""
    data = CATALOG.get(product.lower().strip())
    if not data:
        return f"I don't have the product '{product}' in the catalog."
    if quantity <= 0:
        return "The quantity must be greater than zero."
    subtotal = data["price"] * quantity
    vat = subtotal * 0.21
    total = subtotal + vat
    return (
        f"Product: {product.lower().strip()}\n"
        f"Unit price: ${data['price']:,.2f}\n"
        f"Quantity: {quantity}\n"
        f"Subtotal: ${subtotal:,.2f}\n"
        f"VAT (21%): ${vat:,.2f}\n"
        f"Total: ${total:,.2f}"
    )


def apply_coupon(code: str) -> str:
    """Validate a discount coupon code and return its discount percentage."""
    code = code.strip().upper()
    discount = COUPONS.get(code)
    if discount is None:
        return f"The coupon '{code}' is invalid or has expired."
    return f"Coupon '{code}' is valid: it grants a {discount * 100:.0f}% discount."


# ---------------------------------------------------- declare each tool contract
search_declaration = types.FunctionDeclaration(
    name="search_product",
    description=(
        "Search products in the store catalog by partial text in the name. Use "
        "it when you don't know the exact product name or want to see the "
        "available options. If the customer wants to see the whole catalog, "
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

stock_declaration = types.FunctionDeclaration(
    name="check_stock",
    description="Return the available stock of an exact catalog product.",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "product": {
                "type": "string",
                "description": "Exact product name to check.",
            },
        },
        "required": ["product"],
    },
)

total_declaration = types.FunctionDeclaration(
    name="calculate_total",
    description=(
        "Calculate the subtotal, VAT (21%) and total of buying a quantity of a "
        "product. Use it for any money math: models make mistakes with "
        "calculations, this function doesn't."
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

coupon_declaration = types.FunctionDeclaration(
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

# Dispatch: maps the tool name the model uses to the real function we execute.
TOOLS = {
    "search_product": search_product,
    "check_stock": check_stock,
    "calculate_total": calculate_total,
    "apply_coupon": apply_coupon,
}

store_tool = types.Tool(
    function_declarations=[
        search_declaration,
        stock_declaration,
        total_declaration,
        coupon_declaration,
    ]
)
config = types.GenerateContentConfig(tools=[store_tool])


# --------------------------------------------------- API error handling
def call_with_retries(func, *args, **kwargs):
    """Call func with retries and exponential backoff: 1s, 2s, 4s, 8s..."""
    delay = 1
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if SIMULATE_FAILURES and random.random() < 0.5:
                raise TimeoutError("(simulated) the API did not respond in time")
            return func(*args, **kwargs)
        except Exception as e:
            print(f"  [Error on call (attempt {attempt}/{MAX_RETRIES}): {e}]")
            if attempt == MAX_RETRIES:
                raise
            print(f"  Retrying in {delay}s...")
            time.sleep(delay)
            delay *= 2


def execute_tools(calls, conversation):
    """Execute every tool requested by the model and append its turns."""
    for call in calls:
        print(f"  Model requested: {call.name} with {call.args}")
        conversation.append(
            types.Content(role="model", parts=[types.Part(function_call=call)])
        )
        tool = TOOLS.get(call.name)
        try:
            result = tool(**(call.args or {}))
        except Exception as e:
            result = f"Error running tool {call.name}: {e}"
        print(f"  Result of {call.name}: {result}")
        conversation.append(
            types.Content(
                role="user",
                parts=[
                    types.Part.from_function_response(
                        name=call.name, response={"result": result}
                    )
                ],
            )
        )


# ------------------------------------------------------------- interactive console
def main():
    print("Online store agent. Tell me what you need.")
    print("I can help you with these actions:")
    for name in TOOLS:
        print(f"  - {name}")
    print("Type 'exit' to quit.\n")

    conversation = []

    while True:
        user_input = input("You> ").strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            print("See you next time!")
            break

        conversation.append(types.Content(role="user", parts=[types.Part(text=user_input)]))

        for turn in range(1, MAX_TURNS + 1):
            print(f"  [Turn {turn}/{MAX_TURNS}] Querying the model...")
            try:
                response = call_with_retries(
                    client.models.generate_content,
                    model=MODEL,
                    config=config,
                    contents=conversation,
                )
            except Exception:
                print("  This call ran out of retries.")
                conversation.pop()
                break

            if response.function_calls:
                execute_tools(response.function_calls, conversation)
                continue

            text = (response.text or "").strip()
            print(f"Agent> {text}")
            conversation.append(types.Content(role="model", parts=[types.Part(text=text)]))
            break
        else:
            print("  I reached the turn limit for this question.")


if __name__ == "__main__":
    main()