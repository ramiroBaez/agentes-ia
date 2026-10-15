r"""Provider adapter for the agent projects (Project 5+).

Copy of the Project 5 adapter (the one in the parent folder) **extended with the
token accounting** Project 12 needs: every model call reports how many tokens
it used and what it cost, so each request can be rendered in the trace with its
real cost.

It exposes a single API to chat with the model, whatever the provider. The
provider order is chosen in the .env file and works as a FALLBACK CHAIN: they
are tried in order until one answers.

    PROVIDERS=gemini,groq,openrouter     # recommended (with fallback)
    # or legacy single-provider mode:
    # PROVIDER=groq

    - "gemini"     -> Google API (google-genai, GEMINI_API_KEY).
    - "groq"       -> Groq's OpenAI-compatible API  (GROQ_API_KEY, free).
    - "openrouter" -> OpenRouter's OpenAI-compatible API (OPENROUTER_API_KEY,
                      use ':free' models to avoid spending credit).

If a provider fails (429 quota, network error, auth...), the next one is tried.
Messages use our own canonical format (flat dicts) so project code is not
coupled to any SDK and so the LangGraph checkpointer stores them without
surprises:

    - user:  {"role": "user", "text": "..."}
    - agent: {"role": "agent", "text": "...", "calls": [{"id", "name", "args"}]}
    - tools: {"role": "tools", "results": [{"id", "name", "result"}]}

Tools are still declared with google-genai's types.FunctionDeclaration (plain
JSON schema) and each backend translates them to its own format.

Usage:
    import model

    turn = model.chat(
        [model.m_user("hola")],
        system=SIM_PROMPT,
        funcs=[declaration_search, ...],
    )
    if turn["calls"]:
        ...  # run the tools and build model.m_results(...) for the next turn
"""

import json
import os
import threading
import time
import uuid

from dotenv import load_dotenv
from google.genai import types

from settings import settings

load_dotenv()

# ---------------------------------------------------------------- configuration
# Fallback chain. PROVIDERS wins over PROVIDER (legacy).
_chain = (os.getenv("PROVIDERS") or os.getenv("PROVIDER", "gemini")).strip()
PROVIDERS = [p.strip().lower() for p in _chain.split(",") if p.strip()]

# Updated on every successful response: used to log who answered.
LAST_PROVIDER = "(ninguno)"

_CONFIG = {
    "gemini": {
        "key": "GEMINI_API_KEY",
        "key_legacy": None,
        "model": "GEMINI_MODEL",
        "model_legacy": None,
        "model_default": "gemini-2.5-flash",
        "base": None,
        "sdk": "gemini",
    },
    "groq": {
        "key": "GROQ_API_KEY",
        "key_legacy": "OPENAI_API_KEY",
        "model": "GROQ_MODEL",
        "model_legacy": "OPENAI_MODEL",
        "model_default": "openai/gpt-oss-120b",
        "base": "https://api.groq.com/openai/v1",
        "sdk": "openai",
    },
    "openrouter": {
        "key": "OPENROUTER_API_KEY",
        "key_legacy": "OPENAI_API_KEY",
        "model": "OPENROUTER_MODEL",
        "model_legacy": "OPENAI_MODEL",
        "model_default": "openai/gpt-oss-20b:free",
        "base": "https://openrouter.ai/api/v1",
        "sdk": "openai",
    },
}

_clients = {}
_models = {}
_warnings = []


def _getenv(name):
    return os.getenv(name) if name else None


for _name in PROVIDERS:
    _cfg = _CONFIG.get(_name)
    if not _cfg:
        _warnings.append(
            f"proveedor '{_name}' desconocido (opciones: gemini, groq, openrouter)"
        )
        continue
    _key = os.getenv(_cfg["key"]) or _getenv(_cfg["key_legacy"])
    if not _key or _key == "tu_api_key_acá":
        _warnings.append(
            f"{_name}: sin API key (fijala en '{_cfg['key']}'); queda fuera del respaldo"
        )
        continue
    _model = (
        _getenv(_cfg["model"]) or _getenv(_cfg["model_legacy"]) or _cfg["model_default"]
    )
    if _cfg["sdk"] == "gemini":
        from google import genai

        _clients[_name] = genai.Client(api_key=_key)
    else:
        from openai import OpenAI

        _base = _getenv(f"{_name.upper()}_BASE_URL") or _cfg["base"]
        _clients[_name] = OpenAI(api_key=_key, base_url=_base)
    _models[_name] = _model

# Keep the loop variables out of the module namespace. Careful: if a provider
# was skipped before reaching `_key` or `_model`, those names do not even exist
# and an explicit `del` blows up with NameError. With zero keys in a row (the
# startup of a freshly built container) the API would not come up.
for _garbage in ("_name", "_cfg", "_key", "_model", "_base"):
    globals().pop(_garbage, None)

# Compatibility with the module's previous API.
PROVIDER = ",".join(PROVIDERS)
MODEL_ACTIVE = _models.get(PROVIDERS[0], "(sin proveedor)") if PROVIDERS else "(sin proveedor)"


def diagnosis() -> str:
    """Summary of which providers ended up configured (for debugging)."""
    line = "Proveedores configurados: " + (
        ", ".join(f"{p} ({_models[p]})" for p in PROVIDERS if p in _clients)
        or "(ninguno)"
    )
    return line + (" | " + " | ".join(_warnings) if _warnings else "")


# ---------------------------------------------------------------- canonical helpers
def m_user(text: str) -> dict:
    """Build the canonical message for a user turn."""
    return {"role": "user", "text": text}


def m_results(results: list) -> dict:
    """Build the canonical message with the tool results.

    `results` is a list of {"id", "name", "result"}: one per call the model
    asked for, in the same order.
    """
    return {"role": "tools", "results": results}


# --------------------------------------------------------------- usage and cost
# The token usage of the last call lives in a THREAD-LOCAL and not in a global
# variable: FastAPI serves sync endpoints in a threadpool, so two simultaneous
# requests would step on each other and one conversation's cost would be
# attributed to another.
_usage_local = threading.local()


def record_usage(usage: dict, provider: str, model_name: str) -> None:
    """Store the token usage of the call that just answered."""
    _usage_local.last = {**usage, "provider": provider, "model": model_name}


def last_usage() -> dict | None:
    """Token usage of the last call *of this thread*, or None if there was none."""
    return getattr(_usage_local, "last", None)


def clean_usage() -> None:
    """Forget the recorded usage (the tracer calls this when opening a new trace)."""
    _usage_local.last = None


def usage_of_response(response, sdk: str) -> dict | None:
    """Normalize any provider's 'usage' to the project's canonical format.

        {"tokens_input": int, "tokens_output": int, "tokens_total": int}

    If the response carries no accounting (some models or plans do not return
    it) it returns None: we prefer "I don't know" to inventing a zero.
    """
    if sdk == "gemini":
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return None
        input = int(usage.prompt_token_count or 0)
        output = int(usage.candidates_token_count or 0)
    else:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        input = int(usage.prompt_tokens or 0)
        output = int(usage.completion_tokens or 0)
    return {
        "tokens_input": input,
        "tokens_output": output,
        "tokens_total": input + output,
    }


def price_per_million(provider: str, model_name: str) -> tuple[float | None, float | None]:
    """Price (USD per million tokens) of a model's input and output.

    Looks up the "provider:model" key first, then the bare model name.
    OpenRouter ':free' models have no cost. If there is no known price it
    returns (None, None): the cost is reported as unavailable instead of being
    estimated by eye.
    """
    prices = settings.PRICES_USD_PER_MILLION
    for key in (f"{provider}:{model_name}", model_name):
        if key in prices:
            found = prices[key]
            return float(found["input"]), float(found["output"])
    if model_name.endswith(":free"):
        return 0.0, 0.0
    return None, None


def is_costable(usage: dict | None) -> bool:
    """Do we know the price of the model that produced this usage?"""
    if not usage:
        return False
    input, output = price_per_million(usage.get("provider", ""), usage.get("model", ""))
    return input is not None and output is not None


def cost_estimated(usage: dict | None) -> float:
    """Cost in USD of a token usage, or 0.0 if there is no known price."""
    if not usage:
        return 0.0
    input, output = price_per_million(usage.get("provider", ""), usage.get("model", ""))
    if input is None or output is None:
        return 0.0
    return (
        usage.get("tokens_input", 0) * input
        + usage.get("tokens_output", 0) * output
    ) / 1_000_000


# ---------------------------------------------------------------- conversion to Gemini
def _to_gemini_message(m: dict) -> types.Content:
    role = m["role"]
    if role == "user":
        return types.Content(role="user", parts=[types.Part(text=m["text"])])
    if role == "agent":
        parts = []
        if m.get("text"):
            parts.append(types.Part(text=m["text"]))
        for call in m.get("calls", []):
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(
                        id=call["id"],
                        name=call["name"],
                        args=call["args"],
                    )
                )
            )
        return types.Content(role="model", parts=parts)
    # tools
    return types.Content(
        role="user",
        parts=[
            types.Part.from_function_response(
                name=result["name"], response={"result": result["result"]}
            )
            for result in m["results"]
        ],
    )


# ---------------------------------------------------------------- conversion to OpenAI
# Types that google-genai writes in uppercase and the OpenAI API does not accept.
_TYPES_GEMINI = {
    "OBJECT", "STRING", "NUMBER", "INTEGER", "BOOLEAN", "ARRAY", "NULL",
    "TYPE_UNSPECIFIED",
}


def _lower_types(value):
    """Lowercase the types of a JSON Schema coming from Gemini."""
    if isinstance(value, dict):
        return {
            (k.lower() if k == "type" else k): _lower_types(v) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_lower_types(v) for v in value]
    if isinstance(value, str):
        return value.lower() if value in _TYPES_GEMINI else value
    return value


def _json_schema_from_gemini(schema) -> dict:
    """`types.Schema` -> OpenAI JSON Schema.

    `to_json_dict()` leaves the types in uppercase ("OBJECT", "STRING"), which
    an OpenAI-compatible API rejects. `parameters_json_schema` is no use here:
    with a plain `types.Schema` it is `None`.
    """
    return _lower_types(schema.to_json_dict())


def _to_openai_function(f) -> dict:
    """One tool in the OpenAI-compatible API format.

    Accepts the two forms that coexist across the projects: the agent's
    canonical dict (`{"name", "description", "parameters"}`, or `{"name",
    "description", "schema"}` with a flat field -> type map) and google-genai's
    `types.FunctionDeclaration`. Groq and OpenRouter coerce nothing: if a dict
    arrived and was accessed with `.name`, it blew up with `'dict' object has
    no attribute 'name'` and the fallback dropped to the next provider without
    ever asking it anything.
    """
    if isinstance(f, dict):
        schema = f.get("schema") or {}
        params = f.get("parameters")
        if not params:
            params = {
                "type": "object",
                "properties": {field: {"type": "string"} for field in schema},
            }
            # Not every compatible API accepts `required: []`: if there are no
            # required fields, the key is left out.
            if schema:
                params["required"] = list(schema)
        return {
            "name": f.get("name") or "",
            "description": f.get("description") or "",
            "parameters": params,
        }
    return {
        "name": f.name,
        "description": f.description or "",
        "parameters": f.parameters_json_schema
        or (_json_schema_from_gemini(f.parameters) if f.parameters else {"type": "object", "properties": {}}),
    }


def _to_openai_functions(funcs) -> list:
    return [{"type": "function", "function": _to_openai_function(f)} for f in funcs]


def _to_openai_tool_messages(messages: list, system: str) -> list:
    out = [{"role": "system", "content": system}] if system else []
    for m in messages:
        role = m["role"]
        if role == "user":
            out.append({"role": "user", "content": m["text"]})
        elif role == "agent":
            calls = m.get("calls", [])
            if calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": m.get("text") or None,
                        "tool_calls": [
                            {
                                "id": ll["id"],
                                "type": "function",
                                "function": {
                                    "name": ll["name"],
                                    "arguments": json.dumps(
                                        ll.get("args") or {}, ensure_ascii=False
                                    ),
                                },
                            }
                            for ll in calls
                        ],
                    }
                )
            else:
                out.append({"role": "assistant", "content": m.get("text")})
        else:  # tools
            for result in m["results"]:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": result["id"],
                        "content": json.dumps(
                            {"result": result["result"]}, ensure_ascii=False
                        ),
                    }
                )
    return out


# ---------------------------------------------------------------- per-backend calls
def _request_gemini(client, model, messages: list, system: str, funcs) -> dict:
    config = types.GenerateContentConfig(system_instruction=system or None)
    if funcs:
        config.tools = [types.Tool(function_declarations=list(funcs))]
    response = client.models.generate_content(
        model=model,
        config=config,
        contents=[_to_gemini_message(m) for m in messages],
    )
    turn = response.candidates[0].content
    text = "".join(p.text or "" for p in turn.parts)
    calls = [
        {
            "id": p.function_call.id or str(uuid.uuid4()),
            "name": p.function_call.name,
            "args": p.function_call.args or {},
        }
        for p in turn.parts
        if p.function_call
    ]
    _record_response_usage(response, sdk="gemini", provider="gemini", model_name=model)
    return {"role": "agent", "text": text.strip(), "calls": calls}


def _request_openai(client, model, messages: list, system: str, funcs, provider: str) -> dict:
    params = {
        "model": model,
        "messages": _to_openai_tool_messages(messages, system),
    }
    if funcs:
        params["tools"] = _to_openai_functions(funcs)
        params["tool_choice"] = "auto"
    response = client.chat.completions.create(**params)
    message = response.choices[0].message
    calls = []
    for tc in message.tool_calls or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append({"id": tc.id, "name": tc.function.name, "args": args})
    # The provider is passed as a parameter (not through a global): with several
    # requests in parallel, a global would attribute cost and tokens to the
    # neighboring request.
    _record_response_usage(response, sdk="openai", provider=provider, model_name=model)
    return {"role": "agent", "text": (message.content or "").strip(), "calls": calls}


def _record_response_usage(response, sdk: str, provider: str, model_name: str) -> None:
    """Translate the provider's accounting and make it available to the trace."""
    usage = usage_of_response(response, sdk)
    if usage is not None:
        record_usage(usage, provider=provider, model_name=model_name)


# ---------------------------------------------------------------- the public API
_RETRY_WAITS = [5, 15, 40]  # waits (s) between retry rounds after a transient failure


def _is_transient(e: Exception) -> bool:
    """Is the error recoverable by waiting (quota/rate-limit/503/network), or permanent?"""
    text = str(e).lower()
    markers_transient = (
        "429", "503", "rate limit", "rate_limit", "quota",
        "resource_exhausted", "retry in", "too many requests",
        "temporarily", "unavailable", "high demand", "busy",
        "timed out", "timeout", "connection reset",
        "failed to template", "harmony", "provider error", "origin",
    )
    return any(m in text for m in markers_transient)


def chat(messages: list, system: str = "", funcs=None) -> dict:
    """Send the history through the provider chain and return the agent's turn.

    Tries each provider in PROVIDERS in order; if one fails (429 quota, network,
    auth...), it moves on to the next. If all fail, it raises a RuntimeError
    with the detail of each error.

    Quota protection: if ALL failed with transient errors (429/503/rate-limit),
    it retries the whole chain with growing waits (_RETRY_WAITS) for up to 3
    extra rounds, so a quota spike does not take down a long run (an agent loop
    or a multi-agent system). If the error is permanent (auth, schema), it
    fails right away.
    """
    global LAST_PROVIDER
    errors = []
    rounds = len(_RETRY_WAITS) + 1  # 1 initial attempt + N retries
    for round in range(1, rounds + 1):
        attempt_errors = []
        for name in PROVIDERS:
            if name not in _clients:
                continue
            try:
                if name == "gemini":
                    turn = _request_gemini(_clients[name], _models[name], messages, system, funcs)
                else:
                    turn = _request_openai(
                        _clients[name], _models[name], messages, system, funcs, name
                    )
                LAST_PROVIDER = name
                return turn
            except Exception as e:
                attempt_errors.append(f"{name}: {str(e)[:150]}")
        errors = attempt_errors
        if not errors:
            break
        # Worth retrying? Only if every failure was transient.
        if round == rounds or not all(_is_transient(e) for e in errors):
            break
        wait = _RETRY_WAITS[round - 1]
        print(f"  [modelo] Todos los proveedores fallaron (transitorio); reintento en "
              f"{wait}s... (intento {round + 1}/{rounds})")
        time.sleep(wait)
    if errors:
        raise RuntimeError(
            "Todos los proveedores fallaron (" + "; ".join(errors) + ")"
        )
    raise RuntimeError("No hay proveedores configurados. " + " | ".join(_warnings))
