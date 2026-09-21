# Production, Observability and Security

Moving an agent from a demo to production means treating it like any software:
- **Packaging**: expose the agent as an API (FastAPI) and containerize it
  with Docker.
- **Deployment**: run it on a cloud platform (Railway, Render) behind a
  public URL.
- **Observability**: trace every execution with LangSmith or Langfuse — which
  steps it took, how long each took, and what each call cost.
- **Evaluation**: measure whether the agent "works well" with test datasets,
  metrics and automated evals — not just gut feeling.

## Security guardrails

An agent that can execute tools can cause real-world side effects. Guardrails
are non-negotiable:

- **Prompt injection**: untrusted text in prompts must never override the
  agent's instructions.
- **Output validation**: validate the model's output before acting on it,
  especially when it can write to databases, send emails or spend money.
- **Rate limiting and cost control**: per-user limits to prevent abuse and
  runaway spending.
- **Sandboxing**: isolate dangerous tools (code execution, file access).
- **Human-in-the-loop**: some actions must request human confirmation before
  running (e.g. "send email" stays in simulation until approved).

## Mental model

Try the three-layer lens: *packaging* (how it ships), *observability* (how we
know what it did), and *guardrails* (how we keep it from harming). A finished
agent is the one that can be shipped, traced, and safely limited.