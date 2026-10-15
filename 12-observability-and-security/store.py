r"""Online store tools (adapted from Project 5).

This module is the **business tool** that the Project 12 agent inherits from
Project 5: same catalog, same functions and same contract (function
declarations). It is copied here so the project is self-contained (the
`12-observability-and-security/` folder runs on its own, like every folder in
the public repo) and so it can be extended without touching the original P5.

What is NOT included: the P5 graph and its console `main()`. That lives in
Project 5; here there are only the tools the P12 agent can call.
"""

from google.genai import types

# Catalog and coupons: the store "database" (in memory, it is a demo).
CATALOG = {
    "auriculares bluetooth": {"price": 12999.99, "stock": 12},
    "teclado mecánico": {"price": 18999.00, "stock": 5},
    "mouse gamer": {"price": 7999.50, "stock": 0},
    "notebook": {"price": 549999.00, "stock": 3},
    "monitor": {"price": 249999.00, "stock": 8},
    "webcam hd": {"price": 15999.00, "stock": 20},
}

COUPONS = {"AHORRO10": 0.10, "MITAD": 0.50}

ORDERS = {  # sample orders, for status queries
    "A-1042": {"client": "García, Ana", "product": "monitor", "quantity": 1, "status": "enviado"},
    "A-1043": {"client": "Rossi, Luis", "product": "auriculares bluetooth", "quantity": 2, "status": "pendiente"},
}


# ---------------------------------------------------------------------- functions
def search_product(term: str) -> str:
    """Search catalog products by partial text, or list the whole catalog."""
    t = term.lower().strip()
    if t in ("", "todos", "todo", "all", "*", "list", "lista"):
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


def query_stock(name: str) -> str:
    """Return the available stock of a catalog product."""
    data = CATALOG.get(name.lower().strip())
    if not data:
        return f"No tengo el producto '{name}' en el catálogo."
    return f"Stock de '{name}': {data['stock']} unidades."


def calculate_total(product: str, quantity: int) -> str:
    """Compute subtotal, VAT (21%) and total for buying a quantity of a product."""
    data = CATALOG.get(product.lower().strip())
    if not data:
        return f"No tengo el producto '{product}' en el catálogo."
    if quantity <= 0:
        return "La cantidad tiene que ser mayor a cero."
    subtotal = data["price"] * quantity
    vat = subtotal * 0.21
    total = subtotal + vat
    return (
        f"Producto: {product.lower().strip()}\n"
        f"Precio unitario: ${data['price']:,.2f}\n"
        f"Cantidad: {quantity}\n"
        f"Subtotal: ${subtotal:,.2f}\n"
        f"IVA (21%): ${vat:,.2f}\n"
        f"Total: ${total:,.2f}"
    )


def apply_coupon(code: str) -> str:
    """Validate a discount coupon code and return the discount percentage."""
    code = code.strip().upper()
    discount = COUPONS.get(code)
    if discount is None:
        return f"El cupón '{code}' no es válido o ya expiró."
    return f"Cupón '{code}' válido: otorga un {discount * 100:.0f}% de descuento."


def query_order(code: str) -> str:
    """Return the status of an order from its code."""
    order = ORDERS.get(code.strip().upper())
    if not order:
        return f"No encontré el pedido '{code}'."
    return (
        f"Pedido {code.strip().upper()}\n"
        f"Cliente: {order['client']}\n"
        f"Producto: {order['product']} x{order['quantity']}\n"
        f"Estado: {order['status']}"
    )


# ------------------------------------------------------- contract for the model
declaration_search = types.FunctionDeclaration(
    name="search_product",
    description=(
        "Busca productos en el catálogo de la tienda por texto parcial en el "
        "nombre. Úsala cuando no sepas el nombre exacto del producto o quieras "
        "ver qué opciones hay. Si el cliente quiere ver todo el catálogo, "
        "pasale 'todos' o '*' como texto de búsqueda."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "term": {
                "type": "string",
                "description": "Texto a buscar dentro del nombre de los productos.",
            },
        },
        "required": ["term"],
    },
)

declaration_stock = types.FunctionDeclaration(
    name="query_stock",
    description="Devuelve el stock disponible de un producto exacto del catálogo.",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Nombre exacto del producto a consultar.",
            },
        },
        "required": ["name"],
    },
)

declaration_total = types.FunctionDeclaration(
    name="calculate_total",
    description=(
        "Calcula el subtotal, el IVA (21%) y el total de comprar una cantidad "
        "de un producto. Úsala para cualquier cuenta de dinero: el modelo se "
        "equivoca en las cuentas, esta función no."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "product": {
                "type": "string",
                "description": "Nombre exacto del producto del catálogo.",
            },
            "quantity": {
                "type": "integer",
                "description": "Cantidad de unidades a comprar.",
            },
        },
        "required": ["product", "quantity"],
    },
)

declaration_coupon = types.FunctionDeclaration(
    name="apply_coupon",
    description=(
        "Valida un código de cupón de descuento y devuelve el porcentaje de "
        "descuento que otorga."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Código del cupón a validar.",
            },
        },
        "required": ["code"],
    },
)

declaration_order = types.FunctionDeclaration(
    name="query_order",
    description="Devuelve el estado de un pedido a partir de su código (ej. 'A-1042').",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Código del pedido a consultar.",
            },
        },
        "required": ["code"],
    },
)

#: Declarations the model can see (passed on every call).
FUNCTIONS = [
    declaration_search,
    declaration_stock,
    declaration_total,
    declaration_coupon,
    declaration_order,
]

#: Dispatch: name the model uses -> real function our code runs.
TOOLS = {
    "search_product": search_product,
    "query_stock": query_stock,
    "calculate_total": calculate_total,
    "apply_coupon": apply_coupon,
    "query_order": query_order,
}
