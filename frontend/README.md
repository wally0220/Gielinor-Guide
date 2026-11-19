# Frontend (phase 3)

React + TypeScript (Vite). Planned:

- **Dashboards** (no LLM): player stats, quest requirement checker, GE margin tables.
- **Chat**: streams responses from the FastAPI `/chat` endpoint over SSE, renders tool calls
  inline. Bring-your-own Anthropic API key (kept in the browser) plus a rate-limited demo mode.
