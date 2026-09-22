"""Structured output (JSON) for a real-world case: customer order extraction.

Takes free text (a "customer order" message) and, using structured outputs +
Pydantic, extracts a validated `Order` object ready to be inserted into a
database.

Deliverable: a function that receives text and returns a validated Python
object (Pydantic) ready to be used by another system.
"""

import os
import sys
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

if not API_KEY or API_KEY == "your_gemini_api_key_here":
    print("Error: definí GEMINI_API_KEY en tu archivo .env para llamar a la API.")
    raise SystemExit(1)

client = genai.Client(api_key=API_KEY)

# Same catalog as Project 2, used to estimate the order total.
CATALOG = {
    "wireless headphones": {"price": 12999.99, "stock": 12},
    "mechanical keyboard": {"price": 18999.00, "stock": 5},
    "gaming mouse": {"price": 7999.50, "stock": 0},
    "laptop": {"price": 549999.00, "stock": 3},
    "monitor": {"price": 249999.00, "stock": 8},
    "hd webcam": {"price": 15999.00, "stock": 20},
}

INSTRUCTION = (
    "Extract the order details from the user's message as JSON. Convert "
    "quantities written in words (e.g. 'three') to numbers, and classify the "
    "priority as low, medium or high based on the tone of the text. "
    "Don't invent products or customers that don't appear in the message. "
    "The order text may be in Spanish, English or any language: respond only "
    "with the JSON schema, using the product names exactly as the customer "
    "wrote them."
)

# --------------------------------------------------------------- the schema
# Define the schema with Pydantic: types, allowed values and rules.
class OrderLine(BaseModel):
    product: str = Field(description="Name of the ordered product.")
    quantity: int = Field(ge=1, description="Number of units. Convert 'three' to 3.")


class ExtractedOrder(BaseModel):
    customer: str = Field(description="Name of the person placing the order.")
    priority: Literal["low", "medium", "high"] = Field(
        description="Order priority based on the customer's text."
    )
    items: list[OrderLine] = Field(
        description="All order products, one per line."
    )


# The business object: the same schema, plus the total we compute ourselves.
class Order(ExtractedOrder):
    estimated_total: float | None = None


def calculate_total(items: list[OrderLine]) -> float | None:
    """Sum catalog prices per line; None if no known prices were matched."""
    total = 0.0
    found = 0
    for line in items:
        data = CATALOG.get(line.product.lower().strip())
        if data is not None:
            total += data["price"] * line.quantity
            found += 1
    if found == 0:
        return None
    return round(total, 2)


def process_order(text: str) -> Order:
    """Deliverable: takes free text and returns a Pydantic-validated Order."""
    config = types.GenerateContentConfig(
        system_instruction=INSTRUCTION,
        response_mime_type="application/json",
        response_schema=ExtractedOrder,
    )
    response = client.models.generate_content(
        model=MODEL,
        config=config,
        contents=text,
    )

    if response.parsed is not None:
        extracted = response.parsed
    else:
        # Defensive fallback: if the API returns raw text, validate it anyway.
        extracted = ExtractedOrder.model_validate_json(response.text)

    order = Order(**extracted.model_dump())
    order.estimated_total = calculate_total(order.items)
    return order


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("Extracción de pedido a JSON estructurado.")
    print("Escribí el mensaje del cliente en texto libre (o 'salir').\n")

    while True:
        text = input("Mensaje del pedido> ").strip()
        if not text:
            continue
        if text.lower() in ("salir", "exit", "quit", "q"):
            print("¡Hasta la próxima!")
            break

        try:
            order = process_order(text)
        except Exception as e:
            print(f"  No pude procesar el pedido: {e}\n")
            continue

        print("\n  Objeto Pydantic validado:")
        print(f"  Cliente  : {order.customer}")
        print(f"  Prioridad: {order.priority}")
        print("  Ítems:")
        for line in order.items:
            print(f"    - {line.quantity} x {line.product}")
        print(f"  Total estimado: {order.estimated_total}")
        print("\n  JSON listo para guardar en la base:")
        print(order.model_dump_json(indent=2))
        print()


if __name__ == "__main__":
    main()