# Function Calling: Tool Use in Practice

**Function calling** (a.k.a. tool use) is the mechanism that lets an LLM say
"I need this tool to answer that" — and lets *our code* actually execute it.
It is the fundamental building block of every agent.

## The 6-step cycle

1. **We describe** the tool to the model: name, description and JSON Schema of
   its parameters (the *contract*).
2. **We send** the user's message to the API.
3. **The model returns** a structured request instead of text: which tool to
   call and with which arguments.
4. **Our code executes** the real function.
5. **We feed** the real result back to the model as a function response.
6. **The model composes** the final natural-language answer using the result.

The model never runs code and never sees the function source: it only sees
the declaration.

## Implementing it with google-genai

Describe the tool with `FunctionDeclaration` (name, description,
`parameters_json_schema`) and wrap it in a `Tool`; pass it to
`GenerateContentConfig(tools=[...])`.

After the call, check `response.function_calls`. To continue the
conversation, rebuild it with `Content` parts: the user message, the model's
`function_call`, and the `from_function_response` with our result.

## Key ideas

- The clearer the tool description, the better the model decides when to use
  it.
- **Dispatch**: map the tool name to the real Python function in a dictionary.
- Handwritten loops with a turn limit are exactly what agent frameworks
  (like LangGraph) automate for you.
- Handle API failures with **exponential backoff**: retry at 1s, 2s, 4s...
  instead of crashing.