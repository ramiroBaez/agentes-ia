"""Seam 1: the token usage each provider reports and its estimated cost.

Specification:
  - usage is normalized to the same canonical shape whether it comes from
    Gemini or from an OpenAI-compatible API (input, output and total tokens);
  - `last_usage()` returns the usage of the last call *of this thread* (two
    concurrent threads do not step on each other: FastAPI serves sync requests
    in a threadpool and a global would be a source of races);
  - `cost_estimated()` converts that usage to dollars using the per-model price
    table, and gives 0 when the model is not in the table (we do not invent a
    price: it is reported as not costable).
  - the module imports even when no provider has an API key. That is the cold
    start of a freshly built container: if it blows up here, the API does not
    come up and the error shows in the container log, far from the code that
    caused it.
"""

import importlib
import threading

import pytest
from google.genai import types

import model


def _response_gemini(input: int, output: int):
    return types.GenerateContentResponse(
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=input,
            candidates_token_count=output,
            total_token_count=input + output,
        )
    )


class _CompletionStub:
    """Minimal response shaped like openai.chat.completions.create()."""

    def __init__(self, input: int, output: int):
        self.usage = type(
            "Usage",
            (),
            {
                "prompt_tokens": input,
                "completion_tokens": output,
                "total_tokens": input + output,
            },
        )()


def test_gemini_usage_is_normalized_to_the_canonical_shape():
    usage = model.usage_of_response(_response_gemini(120, 45), sdk="gemini")

    assert usage == {
        "tokens_input": 120,
        "tokens_output": 45,
        "tokens_total": 165,
    }


def test_openai_compatible_usage_is_normalized_to_the_canonical_shape():
    usage = model.usage_of_response(_CompletionStub(200, 60), sdk="openai")

    assert usage == {
        "tokens_input": 200,
        "tokens_output": 60,
        "tokens_total": 260,
    }


def test_missing_usage_metadata_yields_no_usage_instead_of_inventing_one():
    response = types.GenerateContentResponse()
    response.usage_metadata = None

    assert model.usage_of_response(response, sdk="gemini") is None


def test_last_usage_is_scoped_to_the_current_thread():
    model.record_usage(
        {"tokens_input": 10, "tokens_output": 2, "tokens_total": 12},
        provider="groq",
        model_name="openai/gpt-oss-120b",
    )
    errors: list[BaseException] = []

    def other_thread() -> None:
        try:
            # The new thread does not inherit the main thread's usage.
            if model.last_usage() is not None:
                raise AssertionError("the new thread should not see another thread's usage")
            model.record_usage(
                {"tokens_input": 99, "tokens_output": 1, "tokens_total": 100},
                provider="gemini",
                model_name="gemini-2.5-flash",
            )
        except BaseException as e:  # the thread reports its failure to the main thread
            errors.append(e)

    thread = threading.Thread(target=other_thread)
    thread.start()
    thread.join()

    assert errors == []
    # The main thread still sees its own usage, not the other thread's.
    assert model.last_usage() == {
        "tokens_input": 10,
        "tokens_output": 2,
        "tokens_total": 12,
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
    }


def test_cost_is_derived_from_the_price_table_of_the_model():
    """A million input tokens on gemini-2.5-flash cost what the table says."""
    price_input, price_output = model.price_per_million("gemini", "gemini-2.5-flash")

    cost_input = model.cost_estimated(
        {
            "tokens_input": 1_000_000,
            "tokens_output": 0,
            "tokens_total": 1_000_000,
            "provider": "gemini",
            "model": "gemini-2.5-flash",
        }
    )
    cost_output = model.cost_estimated(
        {
            "tokens_input": 0,
            "tokens_output": 1_000_000,
            "tokens_total": 1_000_000,
            "provider": "gemini",
            "model": "gemini-2.5-flash",
        }
    )

    assert cost_input == pytest.approx(price_input)
    assert cost_output == pytest.approx(price_output)


def test_free_models_report_zero_cost():
    usage = {
        "tokens_input": 5000,
        "tokens_output": 500,
        "tokens_total": 5500,
        "provider": "openrouter",
        "model": "qwen/qwen3.8-27b:free",
    }

    assert model.price_per_million("openrouter", usage["model"]) == (0.0, 0.0)
    assert model.cost_estimated(usage) == 0.0
    assert model.is_costable(usage) is True


def test_unknown_model_has_no_price_instead_of_a_guess():
    assert model.price_per_million("groq", "openai/gpt-oss-120b") == (None, None)
    usage = {
        "tokens_input": 1000,
        "tokens_output": 1000,
        "tokens_total": 2000,
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
    }
    assert model.cost_estimated(usage) == 0.0
    assert model.is_costable(usage) is False


def test_usage_is_attributed_to_the_provider_of_that_call():
    """Each call is attributed to its own provider, with no shared state.

    With concurrent requests a global would mix one request's cost into
    another's trace; that is why the provider travels as a parameter.
    """
    model.clean_usage()
    model._request_openai(_client_openai_stub(), "openai/gpt-oss-120b", [], "", None, "groq")

    assert model.last_usage() == {
        "tokens_input": 700,
        "tokens_output": 30,
        "tokens_total": 730,
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
    }


def test_imports_without_any_api_key(monkeypatch):
    """Without keys, the module must import and warn, not break.

    The startup loop `del`s the names it used; if the first provider has no
    key, they were never even defined and the `del` blows up with NameError.
    Locally it goes unnoticed because the environment has keys, and on top of
    that `model` calls `load_dotenv()` on import, which reads the parent
    directory's `.env` and puts them back. In the container none of that exists.

    That is why the keys are emptied instead of deleted: `load_dotenv()` does
    not override a variable that already exists, and an empty one counts as
    "no key" for the module.
    """
    for cfg in model._CONFIG.values():
        for name in (cfg["key"], cfg["key_legacy"]):
            # Optional names are None on purpose.
            if name:
                monkeypatch.setenv(name, "")
    monkeypatch.setattr(model, "PROVIDERS", tuple(model._CONFIG))

    try:
        reloaded = importlib.reload(model)
        assert reloaded._clients == {}
        assert any("sin API key" in warning for warning in reloaded._warnings)
    finally:
        # reload reuses the same module object, so after the monkeypatches are
        # undone one more reload restores the real state.
        importlib.reload(model)


def _client_openai_stub():
    """Client shaped the way `_request_openai` expects."""

    class _Response:
        def __init__(self):
            self.usage = type(
                "Usage",
                (),
                {"prompt_tokens": 700, "completion_tokens": 30, "total_tokens": 730},
            )()
            message = type("Msg", (), {"content": "hola", "tool_calls": None})()
            self.choices = [type("Choice", (), {"message": message})()]

    return type(
        "ClientOpenAIStub",
        (),
        {
            "chat": type(
                "Chat",
                (),
                {
                    "completions": type(
                        "Completions",
                        (),
                        {"create": staticmethod(lambda **params: _Response())},
                    )(),
                },
            )(),
        },
    )()
