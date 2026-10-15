"""Seam: translating tools to the OpenAI-compatible API.

Specification:
  - the agent describes its tools with the canonical dict
    `{"name", "description", "parameters"}`. Gemini accepts it through pydantic
    coercion, but Groq and OpenRouter do not: attribute access blew up with
    `'dict' object has no attribute 'name'` and the fallback chain dropped to
    the next provider without ever asking it anything. Two out of three
    providers broken.
  - the function must also keep accepting `types.FunctionDeclaration`, which is
    the form earlier projects declare and the one the module documents. With
    that form `parameters_json_schema` is `None` and `parameters: null` was
    sent, so the JSON Schema has to be derived from the `types.Schema`.
  - a tool without a description or schema does not break the call: it goes out
    with defaults the API accepts.
"""

from google.genai import types

from model import _to_openai_functions

TOOL_CANONICAL = {
    "name": "search_products",
    "description": "Busca productos por nombre.",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}


def _shape_openai(translated) -> dict:
    assert len(translated) == 1
    assert translated[0]["type"] == "function"
    return translated[0]["function"]


def test_translates_the_agents_canonical_dict():
    function = _shape_openai(_to_openai_functions([TOOL_CANONICAL]))

    assert function["name"] == "search_products"
    assert function["description"] == "Busca productos por nombre."
    assert function["parameters"]["required"] == ["query"]


def test_accepts_a_gemini_function_declaration():
    declaration = types.FunctionDeclaration(
        name="search_products",
        description="Busca productos por nombre.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"query": types.Schema(type=types.Type.STRING)},
            required=["query"],
        ),
    )

    function = _shape_openai(_to_openai_functions([declaration]))

    assert function["name"] == "search_products"
    assert function["parameters"]["required"] == ["query"]


def test_a_tool_without_description_does_not_break():
    function = _shape_openai(_to_openai_functions([{"name": "ping"}]))

    assert function["name"] == "ping"
    assert function["description"] == ""
    assert function["parameters"] == {"type": "object", "properties": {}}


def test_accepts_the_schema_shorthand_of_the_canonical_dict():
    """A flat `schema` of field -> type is expanded into a JSON Schema."""
    canonical = {
        "name": "search_products",
        "description": "Busca.",
        "schema": {"query": "str"},
    }

    function = _shape_openai(_to_openai_functions([canonical]))

    assert function["name"] == "search_products"
    assert function["parameters"]["required"] == ["query"]


def test_both_forms_can_be_mixed():
    declaration = types.FunctionDeclaration(name="other", description="Otra.")

    translated = _to_openai_functions([TOOL_CANONICAL, declaration])

    assert [t["function"]["name"] for t in translated] == ["search_products", "other"]
