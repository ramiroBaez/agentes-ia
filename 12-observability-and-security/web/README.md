# Aurora Shop Ops — SPA

The console: talk to the agent, watch what it does while it does it, and
resolve whatever is left waiting for a person.

> Code, identifiers, comments and test names are in English. Everything the end
> user sees (labels, buttons, messages, agent replies) is in Spanish.

## How to run it

```bash
npm install
npm run dev      # http://localhost:5173, with /api proxied to 127.0.0.1:8000
```

The API runs separately (`uvicorn api:app`). If it lives elsewhere:

```bash
AURORA_API=http://127.0.0.1:9000 npm run dev
```

## What is inside

| File | What it does |
| --- | --- |
| `src/api/client.ts` | Talks to the API: API-key login, session cookie, CSRF, translated errors. |
| `src/api/sse.ts` | Reads the turn's `text/event-stream` and hands over each parsed event. |
| `src/api/types.ts` | The API contract, as types. If it changes there, it changes here. |
| `src/components/Login.tsx` | The first screen: the API key. |
| `src/components/Conversation.tsx` | The thread with the agent and the waterfall of what it is doing. |
| `src/components/Waterfall.tsx` | The steps, in order and with their duration. |
| `src/components/Panel.tsx` | Pending approvals and recent activity. |
| `src/App.tsx` | The shell: session, tabs and API status. |

## Commands

```bash
npm run dev      # development with HMR
npm run build    # type-checks and builds into dist/
npm run preview  # serves dist/ the way nginx will
npm run lint     # oxlint
npm test         # vitest
```

## Decisions that are not visible in the code

- **No router or store.** It is one screen, not an application: the session is
  re-queried on open and each view holds itself up.
- **The panel only appears with the traces scope.** The SPA does not
  reimplement the permission hierarchy: it reads `scopes` from `GET /session`
  and decides what to show.
- **Spans are not requested separately.** They arrive in the same stream as the
  answer, so the waterfall fills while the model answers, not after.
- **No fake data.** Empty states say what will happen when there is nothing,
  instead of hiding it behind an endless spinner.
