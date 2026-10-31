# Third-party software

## Backend (Python)

| Package | License | Used for |
| --- | --- | --- |
| [Google OR-Tools](https://github.com/google/or-tools) (CP-SAT) | Apache-2.0 | Timetable, booking, rescheduling and exam optimisation; infeasibility cores |
| [FastAPI](https://fastapi.tiangolo.com) | MIT | HTTP API |
| [Uvicorn](https://www.uvicorn.org) | BSD-3-Clause | ASGI server |
| [Pydantic v2](https://docs.pydantic.dev) | MIT | Request and LLM-output validation |
| [PyJWT](https://pyjwt.readthedocs.io) | MIT | Login tokens |
| [httpx](https://www.python-httpx.org) | BSD-3-Clause | LLM API calls; test client |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | BSD-3-Clause | Loading `.env` |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 | File uploads (dataset and change files) |
| [pytest](https://pytest.org) | MIT | Tests |
| [SQLAlchemy](https://www.sqlalchemy.org) 2.0 | MIT | ORM / database abstraction layer for PostgreSQL and SQLite state persistence |
| [psycopg2-binary](https://www.psycopg.org) | LGPL-3.0 | PostgreSQL adapter for SQLAlchemy (production deployments) |

## Frontend (JavaScript)

| Package | License | Used for |
| --- | --- | --- |
| [React](https://react.dev) 18 / react-dom | MIT | UI |
| [Vite](https://vitejs.dev) + @vitejs/plugin-react | MIT | Dev server and build |
| [TypeScript](https://www.typescriptlang.org) | Apache-2.0 | Type checking |
| [Tailwind CSS](https://tailwindcss.com) 3, PostCSS, Autoprefixer | MIT | Styling |
| [Lucide React](https://lucide.dev) | ISC | Icons |
| [Inter](https://rsms.me/inter/) via Google Fonts | OFL-1.1 | Typeface |

## Provided by the organisers (unchanged)

`CrunchWeek_StarterPack/` (dataset, CSVs, `validator.py`, example files). `backend/guardrails.py` loads `validator.py` as a module and never modifies it.

## External services (optional)

Anthropic, OpenAI or Google Gemini APIs, only when `LLM_API_KEY` is set. See [AI_USAGE.md](AI_USAGE.md).
