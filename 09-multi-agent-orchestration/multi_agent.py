r"""Project 9 - Multi-agent orchestration (supervisor + workers) with LangGraph.

Use case: SALES REPORT. You ask the system for a sales analysis (e.g. "build a
Q1 report") and a team of specialized agents collaborates to produce it:

  supervisor  -> receives the request, delegates one step at a time, decides
                 when to close
  data worker -> translates the request into queries and returns RAW DATA
  analyst     -> interprets the data and draws CONCLUSIONS
  writer      -> assembles the FINAL REPORT in Markdown

Each worker runs its own encapsulated ReAct loop (like Project 5), with its own
system prompt and its own tools. The supervisor does NOT do the work: it
coordinates, following the "manager-workers" pattern.

Communication is hierarchical (everything goes through the supervisor) and the
state is shared (results and decisions accumulate in the graph state).

Anti-infinite-loop controls (the three from the roadmap):
  1) bounded recursion: invoke's recursion_limit (global graph cap).
  2) delegation cap: the supervisor knows how many times it delegated (a
     counter in the state) and is ordered to close; plus a hard cap.
  3) per-worker cap: each worker cuts its own loop with MAX_WORKER_STEPS.

Deliverable: a functional multi-agent system for a multi-step task, with clear
logs of what each agent did.

Usage:
    venv\Scripts\python.exe 09-multi-agent-orchestration\multi_agent.py --info
    venv\Scripts\python.exe 09-multi-agent-orchestration\multi_agent.py --test
    venv\Scripts\python.exe 09-multi-agent-orchestration\multi_agent.py
"""

import argparse
import operator
import sys
from typing import Annotated, TypedDict

from google.genai import types
from langgraph.graph import END, START, StateGraph

import provider

# ---------------------------------------------------------------- config
MAX_DELEGATIONS = 6  # hard cap: how many times the supervisor may delegate at most
MAX_WORKER_STEPS = 4  # internal cap: how many ReAct steps each worker runs at most
RECURSION_LIMIT = 60  # global graph cap (supervisor + workers counted by LangGraph)

WORKERS = ("data", "analyst", "writer")  # node ids / delegate() targets


# ---------------------------------------------------------------- embedded dataset (rich)
# 6 months (Jan-June, Q1 and Q2) x 8 products in 4 categories. Trend: Q1 flat to
# down in peripherals, Q2 with peaks in monitors and notebooks; audio stable.
MONTHS = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio"]

QUARTERS = {
    "q1": MONTHS[:3],
    "q2": MONTHS[3:],
    "s1": MONTHS[:],
}

CATEGORIES = {
    "Notebooks": ["Notebook 14\"", "Ultrabook 13\""],
    "Periféricos": ["Teclado mecánico", "Mouse gamer", "Webcam hd"],
    "Monitores": ["Monitor 24\"", "Monitor 27\""],
    "Audio": ["Auriculares bluetooth", "Parlante inalámbrico"],
}

# (month, category, product, units, amount_in_pesos)
_DATA = [
    # Q1 - January
    ("Enero", "Notebooks", "Notebook 14\"", 12, 4188000),
    ("Enero", "Notebooks", "Ultrabook 13\"", 8, 5232000),
    ("Enero", "Periféricos", "Teclado mecánico", 30, 840000),
    ("Enero", "Periféricos", "Mouse gamer", 45, 450000),
    ("Enero", "Periféricos", "Webcam hd", 22, 616000),
    ("Enero", "Monitores", "Monitor 24\"", 15, 525000),
    ("Enero", "Monitores", "Monitor 27\"", 10, 750000),
    ("Enero", "Audio", "Auriculares bluetooth", 35, 910000),
    ("Enero", "Audio", "Parlante inalámbrico", 18, 630000),
    # Q1 - February
    ("Febrero", "Notebooks", "Notebook 14\"", 15, 5235000),
    ("Febrero", "Notebooks", "Ultrabook 13\"", 9, 5886000),
    ("Febrero", "Periféricos", "Teclado mecánico", 28, 784000),
    ("Febrero", "Periféricos", "Mouse gamer", 40, 400000),
    ("Febrero", "Periféricos", "Webcam hd", 25, 700000),
    ("Febrero", "Monitores", "Monitor 24\"", 18, 630000),
    ("Febrero", "Monitores", "Monitor 27\"", 12, 900000),
    ("Febrero", "Audio", "Auriculares bluetooth", 33, 858000),
    ("Febrero", "Audio", "Parlante inalámbrico", 20, 700000),
    # Q1 - March
    ("Marzo", "Notebooks", "Notebook 14\"", 13, 4537000),
    ("Marzo", "Notebooks", "Ultrabook 13\"", 7, 4578000),
    ("Marzo", "Periféricos", "Teclado mecánico", 22, 616000),
    ("Marzo", "Periféricos", "Mouse gamer", 31, 310000),
    ("Marzo", "Periféricos", "Webcam hd", 16, 448000),
    ("Marzo", "Monitores", "Monitor 24\"", 20, 700000),
    ("Marzo", "Monitores", "Monitor 27\"", 11, 825000),
    ("Marzo", "Audio", "Auriculares bluetooth", 38, 988000),
    ("Marzo", "Audio", "Parlante inalámbrico", 22, 770000),
    # Q2 - April
    ("Abril", "Notebooks", "Notebook 14\"", 17, 5933000),
    ("Abril", "Notebooks", "Ultrabook 13\"", 12, 7848000),
    ("Abril", "Periféricos", "Teclado mecánico", 26, 728000),
    ("Abril", "Periféricos", "Mouse gamer", 38, 380000),
    ("Abril", "Periféricos", "Webcam hd", 19, 532000),
    ("Abril", "Monitores", "Monitor 24\"", 24, 840000),
    ("Abril", "Monitores", "Monitor 27\"", 16, 1200000),
    ("Abril", "Audio", "Auriculares bluetooth", 40, 1040000),
    ("Abril", "Audio", "Parlante inalámbrico", 24, 840000),
    # Q2 - May
    ("Mayo", "Notebooks", "Notebook 14\"", 14, 4886000),
    ("Mayo", "Notebooks", "Ultrabook 13\"", 11, 7194000),
    ("Mayo", "Periféricos", "Teclado mecánico", 35, 980000),
    ("Mayo", "Periféricos", "Mouse gamer", 47, 470000),
    ("Mayo", "Periféricos", "Webcam hd", 21, 588000),
    ("Mayo", "Monitores", "Monitor 24\"", 22, 770000),
    ("Mayo", "Monitores", "Monitor 27\"", 19, 1425000),
    ("Mayo", "Audio", "Auriculares bluetooth", 42, 1092000),
    ("Mayo", "Audio", "Parlante inalámbrico", 26, 910000),
    # Q2 - June
    ("Junio", "Notebooks", "Notebook 14\"", 20, 6980000),
    ("Junio", "Notebooks", "Ultrabook 13\"", 13, 8502000),
    ("Junio", "Periféricos", "Teclado mecánico", 40, 1120000),
    ("Junio", "Periféricos", "Mouse gamer", 52, 520000),
    ("Junio", "Periféricos", "Webcam hd", 24, 672000),
    ("Junio", "Monitores", "Monitor 24\"", 28, 980000),
    ("Junio", "Monitores", "Monitor 27\"", 22, 1650000),
    ("Junio", "Audio", "Auriculares bluetooth", 46, 1196000),
    ("Junio", "Audio", "Parlante inalámbrico", 28, 980000),
]

SALES = [
    {"month": m, "category": c, "product": p, "units": u, "amount": amt}
    for (m, c, p, u, amt) in _DATA
]


# ---------------------------------------------------------------- simulated data store
def _months_of_segment(segment: str) -> list:
    """Resolve a segment ('all', 'Q1', 'Q2', 'S1' or a month) into real months."""
    s = segment.strip().lower()
    if s in ("", "todo", "todos", "todas", "*", "completo", "semestre", "all"):
        return list(MONTHS)
    if s in QUARTERS:
        return QUARTERS[s]
    for m in MONTHS:
        if m.lower() == s or m.lower() in s:
            return [m]
    return list(MONTHS)


def query_sales(segment: str = "todo", category: str = "") -> str:
    """Query the simulated sales store and return the matching records.

    'segment' accepts: 'todo', a month ('Enero', 'Febrero'...), 'Q1', 'Q2' or
    'S1' (full semester). 'category' accepts: 'Notebooks', 'Periféricos',
    'Monitores', 'Audio' or empty for all.
    """
    months = _months_of_segment(segment)
    cat = category.strip().title()
    rows = [
        v for v in SALES
        if v["month"] in months and (not cat or v["category"] == cat)
    ]
    if not rows:
        return f"Sin registros para segmento='{segment}' y categoria='{category}'."
    total_amt = sum(v["amount"] for v in rows)
    total_units = sum(v["units"] for v in rows)
    summary = (
        f"Registros: {len(rows)} | Unidades: {total_units} | Importe total: ${total_amt:,.0f}\n"
    )
    lines = [
        f"- {v['month']} | {v['category']} | {v['product']} | {v['units']} u | ${v['amount']:,.0f}"
        for v in rows
    ]
    return summary + "\n".join(lines)


def sales_schema() -> str:
    """Return the store schema: available months, quarters and categories."""
    return (
        f"Meses: {', '.join(MONTHS)}\n"
        f"Trimestres: Q1 = {', '.join(QUARTERS['q1'])}, Q2 = {', '.join(QUARTERS['q2'])}, "
        f"S1 = semestre completo\n"
        f"Categorías: {', '.join(CATEGORIES)}\n"
        f"Productos: " + ", ".join(p for ps in CATEGORIES.values() for p in ps)
    )


# ------------------------------------------------------------- function declarations
query_sales_decl = types.FunctionDeclaration(
    name="query_sales",
    description=(
        "Consulta la base de ventas y devuelve los registros que coinciden con el "
        "segmento y la categoría. Úsala para obtener datos crudos de ventas (nunca "
        "inventes cifras: consultá). 'segmento' admite 'todo', un mes ('Enero', "
        "'Febrero'...), 'Q1', 'Q2' o 'S1' (semestre). 'categoria' admite "
        "'Notebooks', 'Periféricos', 'Monitores', 'Audio' o vacío para todas."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "segment": {
                "type": "string",
                "description": "Segmento temporal: 'todo', un mes exacto, 'Q1', 'Q2' o 'S1'.",
            },
            "category": {
                "type": "string",
                "description": "Categoría de productos, o vacío para todas.",
            },
        },
        "required": [],
    },
)

sales_schema_decl = types.FunctionDeclaration(
    name="sales_schema",
    description=(
        "Devuelve la estructura de la base de ventas: qué meses, trimestres, "
        "categorías y productos existen. Úsala al principio si no estás seguro de "
        "qué hay disponible."
    ),
    parameters_json_schema={"type": "object", "properties": {}},
)

delegate_decl = types.FunctionDeclaration(
    name="delegate",
    description=(
        "Delega una sub-tarea a uno de los workers del equipo. 'worker' puede ser "
        "'data' (datos crudos), 'analyst' (conclusiones y análisis), "
        "'writer' (informe final en Markdown), o 'finalize' cuando el informe "
        "esté completo. 'message' es la orden concreta para ese worker, con todo el "
        "contexto que necesite. Responde siempre en español."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "worker": {
                "type": "string",
                "enum": ["data", "analyst", "writer", "finalize"],
                "description": "A qué worker delegar (o 'finalize' para cerrar el pedido).",
            },
            "message": {
                "type": "string",
                "description": "Orden concreta para el worker, con el contexto necesario.",
            },
        },
        "required": ["worker", "message"],
    },
)


# ----------------------------------------------------------------------- system prompts
SUPERVISOR_PROMPT = (
    "Sos el SUPERVISOR de un equipo de agentes que produce informes de ventas. "
    "NO hacés el trabajo vos: coordinás. Tu equipo tiene 3 workers especializados:\n"
    "  - worker data: traduce pedidos a consultas y devuelve los DATOS CRUDOS.\n"
    "  - worker analyst: interpreta los datos y produce CONCLUSIONES (totales, top, tendencias).\n"
    "  - worker writer: con datos y conclusiones arma el INFORME FINAL en Markdown.\n"
    "Reglas:\n"
    "1) Delegá de a UN paso por vez: primero data (o sales_schema si "
    "hace falta), esperás el resultado, y recién después analyst y writer.\n"
    "2) En cada delegación, pasale en el 'message' todo el contexto que ya tengas "
    "y qué exactamente querés que produzca. Si ya hay datos disponibles, preferí "
    "pasárselos en el mensaje antes que pedir que vuelva a consultar.\n"
    "3) Usá 'writer' solo cuando tengas datos y conclusiones. El writer es el "
    "último paso antes del informe final.\n"
    "4) Cuando el writer haya entregado el informe, delegá a 'finalize' con un "
    "mensaje corto de cierre.\n"
    "5) Tenés un máximo de 6 delegaciones. Si ya no podés avanzar, cerrá con lo "
    "que tengas (delegá a 'finalize'). No repitas trabajo ya hecho.\n"
    "6) ANTILOOP: si un worker devolvió un ERROR o ya lo usaste y no hay avance, "
    "NO lo reintentes con la misma pregunta. Adaptá la estrategia: trabajá con los "
    "resultados que ya tengas (delegá al analyst o al writer con lo disponible) "
    "o cerrá directamente a 'finalize'.\n"
    "7) REGLA WRITER: el informe del writer se ACEPTA tal cual. Si ya tenés un "
    "resultado de 'writer', NO le pidas que 'complete', 'mejore' ni 'repita' el "
    "informe. Con el primer 'writer' basta: delegá a 'finalize' directamente."
)

DATA_WORKER_PROMPT = (
    "Sos el WORKER DATA. Tu única responsabilidad es traducir lo que te pide "
    "el supervisor en consultas a la base de ventas y devolver los DATOS CRUDOS "
    "exactos. Reglas:\n"
    "1) Usá SIEMPRE la herramienta query_sales (o sales_schema primero "
    "si necesitás saber qué hay). Nunca inventes cifras.\n"
    "2) Ante una orden con varios segmentos (ej. comparar meses o trimestres), "
    "consulta cada uno por separado.\n"
    "3) Respondé con los resultados CRUDOS: cifras de las consultas, agrupadas y "
    "claras, SIN interpretaciones ni recomendaciones. Si algo quedó ambiguo, "
    "decilo en vez de suponer. Respondé en español."
)

ANALYST_PROMPT = (
    "Sos el WORKER ANALYST. Recibís datos de ventas y producís CONCLUSIONES. "
    "Reglas:\n"
    "1) Trabajá con los datos que te den o consultá la base si necesitás "
    "desagregar o comparar (query_sales/sales_schema). Nunca inventes cifras.\n"
    "2) Producí: totales por mes/trimestre, categoría líder, producto estrella, "
    "tendencia (¿sube o baja?), y cualquier anomalía que veas.\n"
    "3) Respondé como un analista: conclusiones claras, numeradas, citando los "
    "valores reales de los datos. Sin recomendaciones de redactor. Respondé en español."
)

WRITER_PROMPT = (
    "Sos el WORKER WRITER. Con los datos y conclusiones que te pasa el supervisor, "
    "redactás el INFORME FINAL en Markdown bien estructurado:\n"
    "  # Informe de ventas: <alcance>\n  ## Resumen ejecutivo\n  ## Números\n"
    "  ## Conclusiones\n  ## Recomendaciones\n"
    "Usá SOLO la información que te den (no inventes cifras ni saques conclusiones "
    "nuevas que no estén en los datos que recibiste). Escribí en español, claro y "
    "prolijo, con listas y tablas cuando ayuden."
)


# ----------------------------------------------------------------------- workers
BASE_FUNCS = [query_sales_decl, sales_schema_decl]
BASE_TOOLS = {"query_sales": query_sales, "sales_schema": sales_schema}


def run_worker(role: str, system: str, funcs, tools: dict, task: str) -> str:
    """In-house ReAct loop of a worker, encapsulated.

    The worker runs its own mini conversation (user -> agent -> tools ->
    agent...) with its own system prompt and its own tools, until it answers in
    text or reaches MAX_WORKER_STEPS. Returns the final text.
    """
    messages = [provider.user_msg(task)]
    for step in range(1, MAX_WORKER_STEPS + 1):
        try:
            turn = provider.chat(messages, system=system, funcs=funcs)
        except Exception as e:
            print(
                f"      [{role}] Fallaron todos los proveedores: {str(e)[:160]}. "
                f"Devuelvo el error al supervisor."
            )
            return (
                f"[Error del worker {role}] No pudieron llamar al modelo: "
                f"{str(e)[:200]}. Si ya tenés parte de lo necesario con otro worker, "
                f"cerrá con lo que tengas."
            )
        print(
            f"      [{role}·paso {step}] Turno del modelo (proveedor: "
            f"{provider.LAST_PROVIDER}) → "
            + ("herramienta(s): " + ", ".join(c["name"] for c in turn["calls"])
               if turn["calls"] else "respuesta final en texto")
        )
        if not turn["calls"]:
            text = turn.get("text", "").strip()
            if not text:
                print(f"      [{role}] (respuesta vacía; cierro con lo que tenga)")
                return "(el worker no llegó a una respuesta en texto)"
            return text
        results = []
        for call in turn["calls"]:
            fn = tools.get(call["name"])
            try:
                r = fn(**(call["args"] or {}))
            except Exception as e:
                r = f"[Error interno {call['name']}]: {e}"
            print(f"      [{role}] ejecuté {call['name']}({call['args']}) → {r[:180]}")
            results.append({"id": call["id"], "name": call["name"], "result": r})
        messages.append(provider.tool_results(results))
    # internal cap reached: return the last agent text if any
    last = next((m for m in reversed(messages) if m["role"] == "agent"), None)
    text = (last or {}).get("text", "").strip()
    return text or f"[{role}] alcanzó el tope interno sin respuesta." + (
        f"\n(última respuesta parcial: {text[:400]})" if text else ""
    )


def data_worker(task: str) -> str:
    print(f"    [Worker data] Recibe orden del supervisor: {task[:160]}")
    return run_worker("data", DATA_WORKER_PROMPT, BASE_FUNCS, BASE_TOOLS, task)


def analyst_worker(task: str) -> str:
    print(f"    [Worker analyst] Recibe orden del supervisor: {task[:160]}")
    return run_worker("analyst", ANALYST_PROMPT, BASE_FUNCS, BASE_TOOLS, task)


def writer_worker(task: str) -> str:
    print(f"    [Worker writer] Recibe orden del supervisor: {task[:160]}")
    return run_worker("writer", WRITER_PROMPT, [], {}, task)


WORKER_CALLS = {"data": data_worker, "analyst": analyst_worker, "writer": writer_worker}


# ----------------------------------------------------------------------- graph
class State(TypedDict):
    task: str                                  # the user's original request
    results: Annotated[list, operator.add]     # each worker's output: {"worker", "text"}
    decisions: Annotated[list, operator.add]   # supervisor routing: {"worker", "message"}
    delegations: Annotated[int, operator.add]  # delegation counter (hard cap)


def _context(state: State) -> str:
    """Everything the supervisor needs to see to decide the next step."""
    parts = [f"Pedido: {state['task']}"]
    if state["decisions"]:
        parts.append("\nDecisiones ya tomadas:")
        parts += [f"  {i + 1}. → {d['worker']}: {d['message']}" for i, d in enumerate(state["decisions"])]
    if state["results"]:
        parts.append("\nResultados ya obtenidos:")
        for r in state["results"]:
            text = r["text"]
            if len(text) > 4000:
                text = text[:4000] + "\n[… resultado recortado; consultá de nuevo si necesitás el resto]"
            parts.append(f"  [{r['worker']}] {text}")
    parts.append(
        f"\nDelegaciones usadas: {state['delegations']}"
        + (f" (máximo {MAX_DELEGATIONS}). Si llegás al máximo, cerrá con lo que tengas."
           if state["delegations"] >= MAX_DELEGATIONS else "")
    )
    return "\n".join(parts)


def supervisor_node(state: State) -> dict:
    """The supervisor decides: whom to delegate (or to finalize)."""
    print(f"  [Supervisor] Delegaciones usadas: {state['delegations']}/{MAX_DELEGATIONS}")
    print(f"  [Supervisor] Consultando al modelo para decidir el próximo paso...")
    try:
        turn = provider.chat(
            [provider.user_msg(_context(state))],
            system=SUPERVISOR_PROMPT,
            funcs=[delegate_decl],
        )
    except Exception:
        # No LLM available to coordinate: close with whatever we have.
        print(f"  [Supervisor] Fallaron todos los proveedores; cierro con lo que hay.")
        return {
            "decisions": [{"worker": "finalize", "message": "Cierre por fallo de proveedores."}],
            "delegations": 0,
        }
    call = turn["calls"][0] if turn["calls"] else None
    print(f"  [Supervisor] (proveedor: {provider.LAST_PROVIDER})")

    if call:
        worker = call["args"].get("worker", "finalize")
        message = call["args"].get("message", "")
        print(f"  [Supervisor] Decide → delegar a '{worker}': {message[:200]}")
        return {
            "decisions": [{"worker": worker, "message": message}],
            "delegations": 1,
        }
    # The model chose not to delegate: treat it as a close.
    print("  [Supervisor] No devolvió delegación; cierro con lo que hay.")
    return {
        "decisions": [{"worker": "finalize", "message": turn.get("text", "")}],
        "delegations": 0,
    }


def worker_node(worker: str):
    """Node factory: runs the given worker and accumulates its output."""
    def _node(state: State) -> dict:
        orders = [d for d in state["decisions"] if d["worker"] == worker]
        # The last delegation toward this worker is the active order.
        task = orders[-1]["message"] if orders else ""
        text = WORKER_CALLS[worker](task)
        return {"results": [{"worker": worker, "text": text}]}
    return _node


def route(state: State) -> str:
    """Conditional branching according to the supervisor's last decision."""
    last = state["decisions"][-1] if state["decisions"] else {"worker": "finalize"}
    if last["worker"] == "finalize":
        print("  [Supervisor] Informe listo. Fin de la ejecución.\n")
        return END
    node = f"worker_{last['worker']}"
    print(f"  [Ruteador] state['decisions'][-1] = {last['worker']} → {node}")
    return node


def build_graph():
    """Build the graph: supervisor node, worker nodes and conditional edges."""
    graph = StateGraph(State)
    graph.add_node("supervisor", supervisor_node)
    for w in WORKERS:
        graph.add_node(f"worker_{w}", worker_node(w))

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route,
        {
            **{f"worker_{w}": f"worker_{w}" for w in WORKERS},
            END: END,
        },
    )
    for w in WORKERS:
        graph.add_edge(f"worker_{w}", "supervisor")
    return graph.compile()


def final_report(state: dict) -> str:
    """The writer's text (or the last available result) as the report."""
    for r in reversed(state["results"]):
        if r["worker"] == "writer":
            return r["text"]
    return "\n\n---\n\n".join(
        f"[{r['worker']}]\n{r['text']}" for r in state["results"]
    )


def solve_request(graph, task: str) -> dict:
    """Run the graph for a request and return the final state."""
    return graph.invoke(
        {"task": task, "results": [], "decisions": [], "delegations": 0},
        config={"recursion_limit": RECURSION_LIMIT},
    )


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(
        description="Sistema multi-agente supervisor + workers: informes de ventas (LangGraph)."
    )
    parser.add_argument("--info", action="store_true", help="Muestra qué contiene el dataset.")
    parser.add_argument("--test", action="store_true", help="Demo automática de punta a punta.")
    args = parser.parse_args()

    print(f"Proveedores (con respaldo): {', '.join(provider.PROVIDERS)}")

    if args.info:
        print("\n===== Estructura de la base de ventas =====\n")
        print(sales_schema())
        print("\n===== Ejemplos de preguntas que podés hacer =====\n")
        print(
            "- 'Armá un informe de las ventas del primer trimestre, con totales por mes y categoría.'\n"
            "- 'Compará Q1 contra Q2: ¿qué categoría creció más y por qué?'\n"
            "- '¿Cuál fue el producto estrella del semestre y en qué meses vendió más?'\n"
            "- '¿Cómo evolucionaron los periféricos mes a mes? ¿Hay alguna caída preocupante?'\n"
            "- 'Dame el mejor y el peor mes del semestre, con sus totales.'\n"
        )
        return

    graph = build_graph()

    if args.test:
        requests = [
            "Armá un informe de las ventas del primer trimestre (Q1): totales por mes y "
            "por categoría, el producto estrella y cómo evolucionó la tendencia.",
            "Compará los dos trimestres (Q1 vs Q2). ¿Qué categoría creció más? ¿Cuál fue "
            "el mejor mes del semestre y el producto más vendido en unidades?",
        ]
        for task in requests:
            print("\n" + "=" * 74)
            print(f"PEDIDO: {task}")
            print("=" * 74)
            state = solve_request(graph, task)
            print("\n" + "-" * 74)
            print("INFORME FINAL")
            print("-" * 74)
            print(final_report(state))
        print("\nDemo automática terminada.")
        return

    print("\nEquipo multi-agente de informes de ventas (supervisor + 3 workers).")
    print("Escribí 'salir' para terminar. Probá con '--info' para ver el dataset.\n")

    while True:
        task = input("Vos> ").strip()
        if not task:
            continue
        if task.lower() in ("salir", "exit", "q"):
            print("¡Hasta la próxima!")
            break
        try:
            state = solve_request(graph, task)
        except Exception as e:
            print(f"  [Error] No pude procesar el pedido: {str(e)[:200]}\n")
            continue
        print("\n" + "-" * 74)
        print("INFORME FINAL")
        print("-" * 74)
        print(final_report(state) + "\n")


if __name__ == "__main__":
    main()