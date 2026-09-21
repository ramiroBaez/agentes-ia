# Structured Outputs: JSON from Free Text

LLMs are great at understanding language and bad at reliably returning clean
data. **Structured outputs** force the API to answer following a schema, so
the result is directly usable by other systems (databases, APIs, forms).

## How it works with google-genai

Combine `response_mime_type="application/json"` with `response_schema=...`
(A Pydantic class, or a low-level JSON schema). The API returns
`response.parsed` — already an instance of your Pydantic model.

## Why Pydantic is a natural fit

- Pydantic models **are** the schema: types, default values and constraints
  are declared once in Python.
- `Field(description="...")` gives the model guidance on each field.
- `Literal["low", "medium", "high"]` restricts values to an allowed set.
- `Fileld(ge=1)` enforces numeric bounds like a minimum quantity.
- After parsing, validation is a given: `model_validate_json` can also parse
  raw JSON that arrives outside the schema path (defensive fallback).

## A real-world pattern

Given a free-text *customer order*, instruct the model to:

- convert quantities written in words ("three" -> 3),
- classify a field such as priority based on tone,
- never invent products or customers not present in the message,
- return a structured order object.

The **business object** extends the extracted schema with fields computed by
our code (e.g. totals looked up from a catalog) — never by the model.

## Benefits

- No fragile string parsing or regex on model output.
- Typed, validated data ready to insert into a database.
- The schema doubles as documentation for the rest of the team.