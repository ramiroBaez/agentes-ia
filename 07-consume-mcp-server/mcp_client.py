r"""Project 7 - MCP client (consume an existing Postgres server).

Launches the community MCP server @modelcontextprotocol/server-postgres as a
subprocess (via npx) and connects over stdio with the official `mcp` SDK. The
tools that server exposes become available to the agent without writing them
by hand: they are converted to FunctionDeclarations and executed through
`session.call_tool`.

The `mcp` SDK is 100% async, but the LangGraph nodes are sync. To bridge
them, this module runs an event loop on a background thread. ALL of the MCP
lifecycle (opening the session, serving requests, teardown) lives inside ONE
asyncio task: that way the anyio scope of stdio_client is entered and exited
from the same task (otherwise anyio raises "Attempted to exit cancel scope in
a different task" and the connection dies). The sync layer sends requests to
that task through an asyncio.Queue scheduled with call_soon_threadsafe
(put_nowait from another thread is not thread-safe) and waits on a
concurrent.futures.Future.

MCP_CONNECTION_STRING comes from the env; if unset it defaults to the
self-contained demo database this project ships (see docker-compose.yml), so
you can run the agent without configuring anything. Set the env var to point
at any other Postgres.

Usage:
    import mcp_client

    mcp_client.connect()                        # launch the MCP server
    for d in mcp_client.declarations: ...       # FunctionDeclaration for the model
    text = mcp_client.execute("query", sql="SELECT ...")
    mcp_client.close()

Quick check:
    venv\Scripts\python.exe 07-consume-mcp-server\mcp_client.py --tools
"""

import asyncio
import concurrent.futures
import json
import os
import shutil
import threading

from dotenv import load_dotenv
from google.genai import types
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

load_dotenv()

# Connection string to a real Postgres. Never hardcoded into the logic: it is
# read from the env, defaulting to the project's own demo database so the
# agent works out of the box.
DEFAULT_CONNECTION_STRING = "postgresql://postgres:postgres@localhost:5432/demo_libreria"
CONNECTION_STRING = (os.getenv("MCP_CONNECTION_STRING") or "").strip() or DEFAULT_CONNECTION_STRING

# npm package for the community MCP server (Anthropic / reference servers).
SERVER_NPM = "@modelcontextprotocol/server-postgres"

# Write tools of the server: we don't expose them to the agent in this project
# (READ-ONLY demo against the database). Remove them if you want to allow it.
BLOCKED = {"write_query", "create_table", "append_insight"}

# Shared state (populated by connect()).
_thread = None              # daemon thread running the loop
_loop = None                # event loop of the background thread
_session = None             # MCP ClientSession (lives inside the MCP task)
_flow = None                # async CM stdio_client (scope closed from ITS task)
_ready = threading.Event()  # set once the MCP task is up (or failed)
_error = None               # if the async startup failed, the message lands here
_queue = None               # asyncio.Queue[(future, name, arguments)] | CLOSE
mcp_tools = []              # discovered mcp.types.Tool (minus the blocked ones)
declarations = []           # FunctionDeclarations ready for the model
DISPATCH = {}               # tool_name -> callable (public alias of _dispatch)
_dispatch = {}              # tool_name -> callable of this module


def _find_npx() -> str:
    """Locate the npx executable (Windows uses the .cmd extension)."""
    path = (
        shutil.which("npx")
        or shutil.which("npx.cmd")
        or shutil.which("npx.exe")
    )
    if not path:
        raise RuntimeError(
            "Could not find npx/Node.js. Install it from https://nodejs.org "
            "and try again."
        )
    return path


# --------------------------------------------------------- the MCP task (lifecycle)
async def _loop_main():
    """Launch the subprocess + session and serve requests until close.

    Runs as the ONLY task of the background loop. Opening and closing the
    stdio_client scope from here guarantees anyio never complains about
    crossed tasks and the connection stays alive while the agent runs.
    """
    global _session, _flow, _queue, _error
    try:
        params = StdioServerParameters(
            command=_find_npx(),
            args=[SERVER_NPM, CONNECTION_STRING],
            errlog=None,  # silence npx stderr (download/progress noise)
        )
        _flow = stdio_client(params, errlog=None)
        _read, _write = await _flow.__aenter__()
        _session = await ClientSession(_read, _write).__aenter__()
        await _session.initialize()
    except Exception as e:
        _error = str(e)
        _ready.set()
        try:
            if _flow is not None:
                await _flow.__aexit__(None, None, None)
        except Exception:
            pass
        return

    _queue = asyncio.Queue()
    _ready.set()
    try:
        while True:
            request = await _queue.get()
            if request is None:          # close sentinel
                break
            future, name, arguments = request
            try:
                if name == "__list__":
                    result = await _session.list_tools()
                    value = [t for t in result.tools if t.name not in BLOCKED]
                    future.set_result(value)
                    continue

                result = await _session.call_tool(name, arguments or {})
                parts = [p.text for p in result.content if hasattr(p, "text")]
                text = "\n".join(parts).strip()
                if result.is_error:
                    future.set_result(f"MCP error in '{name}': {text[:300]}")
                else:
                    future.set_result(text or "(no response from the MCP server)")
            except Exception as e:
                future.set_exception(e)
    finally:
        # All teardown in this same task, in reverse order.
        try:
            if _session is not None:
                await _session.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            if _flow is not None:
                await _flow.__aexit__(None, None, None)
        except Exception:
            pass


def _background_thread():
    """Run the loop until the MCP task finishes (close arrives)."""
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    try:
        _loop.run_until_complete(_loop_main())
    finally:
        _loop.close()


# ------------------------------------------------------------ the sync layer
def _request(name: str, arguments: dict | None):
    """Queue a request on the MCP task and wait for the result (sync)."""
    if _queue is None:
        raise RuntimeError("The MCP server is not up. Call connect() first.")
    future = concurrent.futures.Future()
    _loop.call_soon_threadsafe(_queue.put_nowait, (future, name, arguments))
    return future.result(timeout=120 if name == "__list__" else 180)


def connect():
    """Launch the MCP server and discover its tools (idempotent)."""
    global _thread, _loop, _session, _queue, _ready, _error
    global mcp_tools, declarations, _dispatch, DISPATCH
    if _loop is not None:
        return

    _ready.clear()
    _error = None
    _thread = threading.Thread(target=_background_thread, daemon=True)
    _thread.start()
    if not _ready.wait(120):
        raise RuntimeError(
            "The MCP server did not respond within 120s. Is the demo database "
            "up (docker compose up -d from 07-consume-mcp-server)? Is "
            "MCP_CONNECTION_STRING correct? Check that 'npx "
            + SERVER_NPM + "' downloads fine on the first run."
        )
    if _error is not None:
        raise RuntimeError(f"Failed to start the MCP server: {_error}")

    mcp_tools = _request("__list__", None)

    declarations = []
    _dispatch = {}
    for tool in mcp_tools:
        schema = dict(tool.input_schema or {"type": "object", "properties": {}})
        schema.pop("$schema", None)  # google-genai rejects that extra key
        declarations.append(
            types.FunctionDeclaration(
                name=tool.name,
                description=tool.description or "",
                parameters_json_schema=schema,
            )
        )
        _dispatch[tool.name] = _wrap_tool(tool.name)
    DISPATCH.clear()
    DISPATCH.update(_dispatch)
    print(f"  [MCP] Server {SERVER_NPM} · db: {CONNECTION_STRING.split('@')[-1]}")
    print(f"  [MCP] Tools available: {', '.join(t.name for t in mcp_tools)}")


def _wrap_tool(name: str):
    """Close over the tool name so it can be a dispatch callable."""

    def runner(**arguments):
        return execute(name, arguments)

    return runner


def execute(name: str, arguments: dict | None = None) -> str:
    """Run a tool of the MCP server and return its output as text."""
    if _loop is None:
        raise RuntimeError("Call mcp_client.connect() before running tools.")
    return _request(name, arguments)


def close():
    """Send the close sentinel to the MCP task and wait for the thread."""
    global _loop, _session, _queue
    if _queue is not None:
        try:
            _loop.call_soon_threadsafe(_queue.put_nowait, None)  # sentinel
            _thread.join(timeout=10)
        except Exception:
            pass
    _loop = None
    _queue = None
    _session = None


# ------------------------------------------------------------- demo utilities
def tools_summary() -> str:
    """List the MCP tools with their description, handy for the SYSTEM_PROMPT."""
    return "\n".join(f"- {t.name}: {t.description}" for t in mcp_tools)


if __name__ == "__main__":
    import sys

    connect()
    if "--tools" in sys.argv:
        for t in mcp_tools:
            print(f"\n- {t.name}")
            print(f"  {t.description[:120]}")
            print(f"  schema: {json.dumps(t.input_schema, ensure_ascii=False)[:160]}")
    close()