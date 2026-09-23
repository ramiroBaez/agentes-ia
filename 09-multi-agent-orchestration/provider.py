r"""Multi-provider adapter for the agent projects (Project 5+).

Exposes a single API to chat with a model, no matter which provider backs it.
The provider list lives in the .env file and acts as a FAILOVER CHAIN: they are
tried in order until one answers.

    PROVIDERS=gemini,groq,openrouter     # recommended (with fallback)

    - "gemini"     -> Google API (google-genai, GEMINI_API_KEY).
    - "groq"       -> OpenAI-compatible Groq API (GROQ_API_KEY, free tier).
    - "openrouter" -> OpenAI-compatible OpenRouter API (OPENROUTER_API_KEY,
                      use ':free' models so you never spend credits).

If a provider fails (429 quota, network error, auth...), the next one is used.
Messages use a self-owned canonical format (plain dicts) so the project code is
not coupled to any SDK and LangGraph's checkpointer serializes them safely:

    - user:      {"role": "user", "text": "..."}
    - agent:     {"role": "agent", "text": "...", "calls": [{"id", "name", "args"}]}
    - tools:     {"role": "tools", "results": [{"id", "name", "result"}]}

Tools are still declared with google-genai's FunctionDeclaration (pure JSON
schema) and every backend translates them to its own format internally.

Anti-quota (Project 9): if EVERY provider in the chain fails during a single
turn, chat() does not give up right away — it distinguishes TRANSIENT failures
(429 rate-limit, 503, "retry in Xs", Groq's intermittent 400 render errors)
from PERMANENT ones (bad API key, invalid schema). If all failures are
transient, it retries the whole chain with increasing waits (5s -> 15s -> 40s)
up to 3 rounds, so a quota spike does not kill a long multi-agent run mid-loop.
Permanent errors abort immediately, without waiting.

Usage:
    import provider

    turn = provider.chat(
        [provider.user_msg("hello")],
        system=SYSTEM_PROMPT,
        funcs=[search_decl, ...],
    )
    if turn["calls"]:
        ...  # execute them, then feed provider.tool_results(...) back
"""

import json
import os
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from google.genai import types

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")
load_dotenv()

# ---------------------------------------------------------------- config
# Provider fallback chain. PROVIDERS wins over PROVIDER (legacy).
_chain = (os.getenv("PROVIDERS") or os.getenv("PROVIDER", "gemini")).strip()
PROVIDERS = [p.strip().lower() for p in _chain.split(",") if p.strip()]

# Updated on every successful reply: handy to log who answered.
LAST_PROVIDER = "(none)"

_CONFIG = {
    "gemini": {
        "key": "GEMINI_API_KEY",
        "model": "GEMINI_MODEL",
        "default_model": "gemini-2.5-flash",
        "base": None,
        "sdk": "gemini",
    },
    "groq": {
        "key": "GROQ_API_KEY",
        "model": "GROQ_MODEL",
        "default_model": "openai/gpt-oss-120b",
        "base": "https://api.groq.com/openai/v1",
        "sdk": "openai",
    },
    "openrouter": {
        "key": "OPENROUTER_API_KEY",
        "model": "OPENROUTER_MODEL",
        "default_model": "qwen/qwen3.8-27b:free",
        "base": "https://openrouter.ai/api/v1",
        "sdk": "openai",
    },
}

_clients = {}
_models = {}
_warnings = []


def _env(name):
    return os.getenv(name) if name else None


for _name in PROVIDERS:
    _cfg = _CONFIG.get(_name)
    if not _cfg:
        _warnings.append(
            f"unknown provider '{_name}' (options: gemini, groq, openrouter)"
        )
        continue
    _key = os.getenv(_cfg["key"])
    if not _key or _key == "your_api_key_here":
        _warnings.append(
            f"{_name}: no API key (set it in '{_cfg['key']}'); left out of the fallback chain"
        )
        continue
    _model = os.getenv(_cfg["model"]) or _cfg["default_model"]
    if _cfg["sdk"] == "gemini":
        from google import genai

        _clients[_name] = genai.Client(api_key=_key)
    else:
        from openai import OpenAI

        _base = _env(f"{_name.upper()}_BASE_URL") or _cfg["base"]
        _clients[_name] = OpenAI(api_key=_key, base_url=_base)
    _models[_name] = _model

del _name, _cfg, _key, _model

# Compatibility with the module's previous API.
PROVIDER = ",".join(PROVIDERS)
ACTIVE_MODEL = _models.get(PROVIDERS[0], "(no provider)") if PROVIDERS else "(no provider)"


def diagnostics() -> str:
    """Summary of which providers ended up configured (for debugging)."""
    line = "Configured providers: " + (
        ", ".join(f"{p} ({_models[p]})" for p in PROVIDERS if p in _clients)
        or "(none)"
    )
    return line + (" | " + " | ".join(_warnings) if _warnings else "")


# ---------------------------------------------------------------- canonical helpers
def user_msg(text: str) -> dict:
    """Build the canonical message for a user turn."""
    return {"role": "user", "text": text}


def tool_results(results: list) -> dict:
    """Build the canonical message with the tools' outcomes.

    'results' is a list of {"id", "name", "result"}, one per call the model
    requested, in the same order.
    """
    return {"role": "tools", "results": results}


# ---------------------------------------------------------------- Gemini conversion
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


# ---------------------------------------------------------------- OpenAI conversion
def _to_openai_functions(funcs) -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": f.name,
                "description": f.description,
                "parameters": f.parameters_json_schema,
            },
        }
        for f in funcs
    ]


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
                                "id": c["id"],
                                "type": "function",
                                "function": {
                                    "name": c["name"],
                                    "arguments": json.dumps(
                                        c.get("args") or {}, ensure_ascii=False
                                    ),
                                },
                            }
                            for c in calls
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
def _ask_gemini(client, model, messages: list, system: str, funcs) -> dict:
    config = types.GenerateContentConfig(system_instruction=system or None)
    if funcs:
        config.tools = [types.Tool(function_declarations=list(funcs))]
    turn = client.models.generate_content(
        model=model,
        config=config,
        contents=[_to_gemini_message(m) for m in messages],
    ).candidates[0].content
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
    return {"role": "agent", "text": text.strip(), "calls": calls}


def _ask_openai(client, model, messages: list, system: str, funcs) -> dict:
    params = {
        "model": model,
        "messages": _to_openai_tool_messages(messages, system),
    }
    if funcs:
        params["tools"] = _to_openai_functions(funcs)
        params["tool_choice"] = "auto"
    message = client.chat.completions.create(**params).choices[0].message
    calls = []
    for tc in message.tool_calls or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        calls.append({"id": tc.id, "name": tc.function.name, "args": args})
    return {"role": "agent", "text": (message.content or "").strip(), "calls": calls}


# ---------------------------------------------------------------- the public API
_RETRY_SECONDS = [5, 15, 40]  # waits (s) between retry rounds on transient failure


def _is_transient(e: Exception) -> bool:
    """Is the error recoverable with a wait (quota/rate-limit/503/network), or permanent?"""
    text = str(e).lower()
    markers = (
        "429", "503", "rate limit", "rate_limit", "quota",
        "resource_exhausted", "retry in", "too many requests",
        "temporarily", "unavailable", "high demand", "busy",
        "timed out", "timeout", "connection reset",
        "failed to template", "harmony", "provider error", "origin",
    )
    return any(m in text for m in markers)


def chat(messages: list, system: str = "", funcs=None) -> dict:
    """Send the history to the provider chain and return the agent's turn.

    Tries each provider in PROVIDERS, in order; if one fails (429 quota,
    network, auth...), it moves to the next. If all fail, raises a RuntimeError
    with the detail of every error.

    Anti-quota (Project 9): if ALL providers failed with TRANSIENT errors
    (429/503/rate-limit), the whole chain is retried with increasing waits
    (_RETRY_SECONDS) up to 3 rounds, so a quota spike does not kill a long
    multi-agent run mid-loop. Permanent errors (auth, schema) abort at once.
    """
    global LAST_PROVIDER
    errors = []
    rounds = len(_RETRY_SECONDS) + 1  # 1 initial attempt + N retries
    for round_no in range(1, rounds + 1):
        round_errors = []
        for name in PROVIDERS:
            if name not in _clients:
                continue
            try:
                if name == "gemini":
                    turn = _ask_gemini(_clients[name], _models[name], messages, system, funcs)
                else:
                    turn = _ask_openai(_clients[name], _models[name], messages, system, funcs)
                LAST_PROVIDER = name
                return turn
            except Exception as e:
                round_errors.append(f"{name}: {str(e)[:150]}")
        errors = round_errors
        if not errors:
            break
        # Worth retrying? Only if every failure was transient.
        if round_no == rounds or not all(_is_transient(e) for e in errors):
            break
        wait = _RETRY_SECONDS[round_no - 1]
        print(f"  [provider] Todos los proveedores fallaron (transitorio); reintento en "
              f"{wait}s... (intento {round_no + 1}/{rounds})")
        time.sleep(wait)
    if errors:
        raise RuntimeError(
            "All providers failed (" + "; ".join(errors) + ")"
        )
    raise RuntimeError("No providers configured. " + " | ".join(_warnings))