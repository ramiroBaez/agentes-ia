# AI Agents: An Intro

An **AI agent** is a system that uses a language model (LLM) as its brain and
can, in a loop, reason about a task, decide what action to take, execute a
tool, observe the result and repeat until the user's request is resolved.

The key difference with a plain **chatbot** is that a chatbot only produces
text, while an agent produces *actions*: it can look things up, calculate,
query databases, write files or call external APIs.

The core loop is known as **ReAct (Reason + Act)**: the model reasons about
what to do, acts through a tool, and incorporates the result into its next
decision.

## Key properties of a good agent

- **Grounding**: it only states facts it can verify through tools or context.
- **Determinism where it matters**: money math, DB lookups and side effects
  are executed by our code, never left to the model's guesswork.
- **Failures are expected**: the agent must handle errors with retries,
  timeouts and limits on the number of turns.

## Building blocks

1. **Tools**: functions the model can request, described by a contract.
2. **Memory**: short-term (within the conversation) and long-term (persisted).
3. **Orchestration**: how the loop is controlled — by hand, by a framework
   (LangGraph, Pydantic AI) or by multiple specialized agents.

An agent is only as reliable as the guardrails around it: iteration limits,
human-in-the-loop checkpoints for sensitive actions, and validated outputs.