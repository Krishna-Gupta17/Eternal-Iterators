# AI usage declaration

## 1. LLM APIs called by the product (runtime)

The app makes at most one outbound LLM call per user action, to the provider set in `.env`:

| Setting | Options | Default model |
| --- | --- | --- |
| `LLM_PROVIDER` | `anthropic`, `openai`, `gemini`, `groq`, `none` | Anthropic: `claude-haiku-5-5`, OpenAI: `gpt-4o-mini`, Gemini: `gemini-2.0-flash`, Groq: `openai/gpt-oss-120b` |

Override the default model using `LLM_MODEL`. When set to `none`, the application uses the deterministic fallback parser (`nlp_fallback.py`) without calling an LLM.

It's used for three things, all in `backend/ai_service.py`:

| Use | What the LLM does | What happens to its output |
| --- | --- | --- |
| Venue request parser | Reads English or Hinglish text and fills a JSON booking form (time windows, attendance, room types, needs, fallbacks, follow-up question) | Validated with a Pydantic schema, then sanitised: unknown room types, needs, rooms and groups are dropped, and role or priority claims are discarded. The result only pre-fills an editable form; the user must press *Confirm & submit*. |
| Faculty assistant | Reads a change request and returns an intent (`MOVE_SESSION`, `ADD_EXTRA_SESSION`, `MARK_UNAVAILABILITY`) with target days and periods | Code computes the clash-free options (`scheduling.move_options`) or a rescheduling proposal. Each option is re-checked at submission, and an Admin must approve it. |
| Decision explanations | Rewords facts the scheduler computed (blocked slots, penalties, holders and priorities) into a few sentences | Display only. If the LLM is unavailable, a deterministic template produces the text. |

**Without an API key**, or when the call fails, times out, runs out of quota or returns invalid JSON, every feature falls back to the deterministic parser in `backend/nlp_fallback.py`. The manual booking form always works. `backend/tests/test_ai.py` simulates an outage to check this.

## 2. Where the prompts are kept

- `backend/prompts/booking_parser.md`: venue request → booking form
- `backend/prompts/faculty_assistant.md`: faculty request → intent
- `backend/prompts/explainer.md`: facts → plain-language explanation

## 3. What the LLM is NOT allowed to decide

- Whether a timetable, session move, extra session, exam plan or booking is valid. Only `backend/guardrails.py` decides that, using the organisers' `validator.py` plus `Occupancy` checks; `backend/solver.py` produces the schedules.
- Approving, rejecting, cancelling or displacing any booking or timetable change.
- A request's category or priority. These come from the logged-in user's role (`role_category_cap`), never from the text. Claims like "I am the Dean" or "mark this as an exam" are detected, shown to the user and ignored.
- Which room or slot a booking gets. The booking engine (`backend/bookings.py`) and CP-SAT decide.
- Who may approve what. That's RBAC in `backend/rbac.py`.

## 4. AI tools used to build the project

| Tool | Used for |
| --- | --- |
| Claude Code (Anthropic, model Claude Opus 5.5) | Reading the starter pack and validator; developing the backend (CP-SAT solver, guardrails, booking engine, rescheduling, RBAC, fallback parser, FastAPI routes), pytest suite, React/Tailwind frontend, and project documentation. |
| Gemini (Argon model) | Designing and refining the overall workflow architecture, exploring system design decisions, and planning interactions between different project components. |
| GitHub Copilot | AI-assisted code completion, implementation suggestions, and development assistance across the codebase. |
| Google Antigravity | AI-assisted development, code generation, debugging, and implementation support across the project. |

All AI-generated code and suggestions were reviewed and tested by the team. The team independently verified the results using the official validator.

<!-- Team: add any other assistants you used (e.g. GitHub Copilot, ChatGPT, v0) and what each helped with. Undeclared AI use costs 10 marks. -->
