# lore-hound

A codebase research agent that answers technical questions about any public GitHub repository by autonomously exploring its source code, reading relevant files, and synthesizing findings all exposed through a REST API.

The agent uses tool-calling against an LLM to drive a multi-step exploration loop: it lists directories, reads files, searches for symbols, and consults past session records until it has enough context to produce a cited answer. Every tool call, intermediate finding, and final answer is persisted to PostgreSQL via Django ORM.

---

## Quick Start

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd lore-hound

# 2. Configure environment
cp .env.example .env
# Edit .env: set LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL

# 3. Build and start everything (Docker Compose v2 required)
docker compose up -d

# 4. (Optional) Seed sample records demonstrating a completed research session
docker compose exec app python manage.py seed_data

# 5. (Optional) Django admin access
docker compose exec app python manage.py createsuperuser
# Visit http://localhost:8000/admin/
```

The API is available at **http://localhost:8000/api/research/**.

No Python, no PostgreSQL, no dependency installation on the host, everything runs in containers. Docker with Compose v2 is the only prerequisite.

---

## API

### Start a research session

```
POST /api/research/
Content-Type: application/json

{
  "repo_url": "https://github.com/tiangolo/fastapi",
  "question": "How does FastAPI handle dependency injection internally?"
}
```

Response (201 on completion, 500 on failure):

```json
{
  "id": 1,
  "repository": {
    "id": 1,
    "url": "https://github.com/tiangolo/fastapi",
    "name": "fastapi",
    "owner": "tiangolo",
    "file_count": 184,
    "last_analyzed": null,
    "created_at": "2026-05-16T..."
  },
  "question": "How does FastAPI handle dependency injection internally?",
  "reasoning": [
    "I first examined /fastapi/dependencies/models.py to understand the data structures...",
    "Then traced solve_dependencies() in /fastapi/dependencies/utils.py..."
  ],
  "final_answer": [
    "## How FastAPI Handles Dependency Injection Internally",
    "",
    "The dependency injection system involves four key components...",
    "",
    "### 1. The Dependant Data Model",
    "..."
  ],
  "status": "completed",
  "error_message": null,
  "model_used": "deepseek-ai/DeepSeek-V4-Pro",
  "input_tokens": 180685,
  "output_tokens": 2716,
  "started_at": "2026-05-16T...",
  "completed_at": "2026-05-16T..."
}
```

`final_answer` and `reasoning` are arrays of lines rather than a single escaped string readable in `curl`/`jq` without `\\n` noise. Join them with `jq -r '.final_answer[]'` to reconstruct the markdown.

**Synchronous, 30-120s**: The POST blocks until the agent completes. The response returns 201 on success or 500 on agent failure. Use long HTTP timeouts or poll `GET /api/research/{id}/` for async consumption patterns.

### Read the answer as plain markdown

```
GET /api/research/1/answer/
Content-Type: text/markdown
```

Returns the answer (and reasoning, if available) as raw markdown with no JSON wrapper, ideal for `curl`, Postman, or any tool that just wants to read the output without fighting JSON escaping.

### Retrieve session details (full trace)

```
GET /api/research/1/
```

Returns everything including `tool_calls`, every function call the agent made, with input arguments and output summaries. Useful for debugging agent behavior and understanding how it reached its conclusions.

### List sessions for a repository

```
GET /api/research/?repo_url=https://github.com/tiangolo/fastapi
```

Returns the 50 most recent sessions, ordered by `started_at` descending. Omits `final_answer` and `tool_calls` for brevity; use the detail endpoint for full data.

---

## Project Structure

```
lore-hound/
├── .dockerignore                 # Docker build exclusions
├── .env.example                  # Environment variable template
├── .gitignore                    # Git exclusions
├── .python-version               # Python version pin for uv/pyenv
├── pyproject.toml                # Python project metadata + dependencies (uv)
├── uv.lock                       # Lockfile - pinned exact dependency versions
├── Dockerfile                    # Multi-stage production image (builder + runtime)
├── docker-compose.yml            # App + PostgreSQL 16 orchestration
├── docker-entrypoint.sh          # Container entrypoint: migrate → gunicorn
├── Makefile                      # Dev shortcuts (seed, shell, test, logs)
├── manage.py                     # Django CLI entrypoint
├── pyrightconfig.json            # Static type-checker configuration
├── DECISIONS.md                  # Architecture decision record
├── LICENSE                       # MIT license
├── README.md                     # Project documentation
│
├── lorehound/                    # Django project configuration
│   ├── __init__.py
│   ├── settings.py               # All env-var-driven configuration
│   ├── urls.py                   # Root URL configuration
│   └── wsgi.py                   # WSGI application entrypoint
│
└── research/                     # Core application
    ├── __init__.py
    ├── admin.py                  # Django admin configuration
    ├── apps.py                   # App configuration
    ├── models.py                 # Repository, ResearchSession, ToolCall
    ├── serializers.py            # Request/response serialization
    ├── urls.py                   # URL routing
    ├── views.py                  # REST API endpoints
    ├── tests.py                  # 18 tests (model, serializer, API, answer, repo reuse regression, reasoning_content replay)
    │
    ├── management/
    │   └── commands/
    │       └── seed_data.py      # Seed sample research sessions for demo
    │
    ├── migrations/
    │   ├── __init__.py
    │   ├── 0001_initial.py
    │   ├── 0002_alter_repository_id_alter_researchsession_id_and_more.py
    │   └── 0003_add_reasoning_field.py
    │
    └── services/
        ├── __init__.py
        ├── agent.py              # Agent loop engine (60-iteration max, tool dispatch)
        ├── llm_client.py         # Raw HTTP OpenAI-compatible client (provider-agnostic)
        ├── repo_manager.py       # Shallow git clone + sandboxed file access
        └── tools.py              # 7 agent tools with JSON schemas
```

---

## Agent Design

### Tool set (7 tools)

| Tool                                          | Purpose                                    | Signals when the agent should stop exploring and answer |
| --------------------------------------------- | ------------------------------------------ | ------------------------------------------------------- |
| `list_files(path)`                          | List directory contents                    | When the project structure is understood                |
| `read_file(path, max_length, offset)`       | Read file contents with pagination         | After 3 reads of relevant files                         |
| `search_code(query, path)`                  | Substring search across files              | When the target symbol is located                       |
| `get_file_summary(path)`                    | AST-based structural summary (Python only) | When file relevance is confirmed                        |
| `save_finding(session_id, file_path, note)` | Persist intermediate findings to DB        | N/A (side effect during exploration)                    |
| `get_previous_findings(repo_url)`           | Read past session findings from DB         | When prior work exists for this repo                    |
| `list_past_sessions(repo_url)`              | List all past sessions for this repo       | When checking whether a question has been asked before  |

### Loop mechanics

1. Agent receives the user's question and tool definitions
2. Agent decides which tool to call (or produces a final answer)
3. Backend executes the tool locally, persists the ToolCall record, returns the result
4. Loop repeats until the agent emits a final answer or hits 60 iterations
5. On completion: parses `REASONING:` / `ANSWER:` sections, stores both separately

### Stopping conditions

- **Primary**: The LLM emits `finish_reason="stop"` with no tool calls, it has decided to answer
- **Safety**: Hard cap of 60 iterations prevents runaway loops
- **Truncation recovery**: If the LLM output is truncated (`finish_reason="length"`), the agent sends a continuation prompt and retries
- **Error handling**: LLM API errors (auth failure, rate limit, server error) fail the session immediately with a descriptive error message

### Context management

The agent is prompted to read efficiently: read full functions (≥3000 chars), limit to 3 `read_file` calls, and use `get_file_summary` to pre-screen files before reading. This keeps context window usage within practical limits for most repos. The `search_code` tool targets specific symbols rather than forcing breadth-first exploration.

### Agent-DB interaction

The agent reads from the database (`get_previous_findings`, `list_past_sessions`) before starting exploration, and writes findings (`save_finding`) during the loop. This satisfies the requirement that the agent meaningfully uses the database as part of its workflow, not just as a post-hoc log.

---

## Database Schema

```
Repository (1) ──── (N) ResearchSession (1) ──── (N) ToolCall
```

**Repository**: URL, name, owner, clone path, file count. One per repo separates metadata from session data.

**ResearchSession**: Question, `reasoning` (chain-of-thought extracted from LLM output), `final_answer`, status (pending → in_progress → completed/failed), model name, token counts, timestamps. Each session belongs to exactly one repository.

**ToolCall**: Every tool invocation name, input args (JSON), output summary (truncated to 500 chars), file path touched, sequence number. Linked to a session. Provides the complete audit trail the evaluator expects.

The `reasoning` field was added after observing that the agent's answers were dense walls of text. The LLM is prompted to emit `REASONING:` and `ANSWER:` sections; the backend parses on the delimiter and stores them separately.

---

## Deployment

### Production build

```bash
docker compose build --no-cache
docker compose up -d
```

The Docker image uses a multi-stage build: Python dependencies installed by `uv` in a builder stage, then copied into a lean `python:3.11-slim` runtime with only `git` added. The application runs as a non-root user (uid 1001). Gunicorn serves the Django app with 4 workers and a 300-second timeout (accommodating the synchronous agent loop).

### Environment variables

| Variable                 | Required | Default                           | Description                                              |
| ------------------------ | -------- | --------------------------------- | -------------------------------------------------------- |
| `LLM_API_KEY`          | Yes      |                                   | API key. Should start with `sk-` if from SiliconFlow or OpenAI |
| `LLM_BASE_URL`         | No       | `https://api.siliconflow.cn/v1` | OpenAI-compatible base URL                               |
| `LLM_MODEL`            | No       | `Qwen/Qwen2.5-72B-Instruct`     | Model identifier                                         |
| `DATABASE_URL`         | No       | `postgres://...`                | PostgreSQL connection string (auto-configured in Docker) |
| `DJANGO_SECRET_KEY`    | No       | 72-char dev key                   | Django secret key (generate a fresh one for production)  |
| `DJANGO_DEBUG`         | No       | `True`                          | Django debug mode                                        |
| `DJANGO_ALLOWED_HOSTS` | No       | `*`                             | Comma-separated allowed hosts                            |
| `REPOS_CLONE_DIR`      | No       | `./data/repos`                  | Directory for cloned repositories                        |

---

## Evaluation Notes

This project was built for the CodeFusion AI Senior Backend Developer take-home task. The following documentation is also included in this repository:

- **[DECISIONS.md](DECISIONS.md)** Architecture overview, database schema rationale, key design decisions with trade-off analysis, what I'd do differently with more time, AI tool usage transparency, and known limitations.

### Design rationale summary

| Decision                                | Rationale                                                                                                                                                                         |
| --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Custom agent loop (no LangGraph/CrewAI) | Linear 7-tool loop doesn't need a framework. The hand-written loop is easier to debug, has predictable behavior, and demonstrates understanding of the LLM tool-calling protocol. |
| Raw HTTP LLM client (no SDK)            | Provider-agnostic swap SiliconFlow, OpenAI, or Anthropic by changing env vars. No SDK lock-in.                                                                                    |
| `reasoning` / `final_answer` split  | ~15 tokens in the system prompt separates chain-of-thought from the answer. Zero architecture cost.                                                                               |
| Arrays of lines in JSON response        | Kills `\\n` escaping. `jq -r '.final_answer[]'` reconstructs the markdown. A presentation decision, not a storage decision.                                                   |
| Plain-text `/answer/` endpoint        | Best readability investment for the effort. Raw markdown with no JSON wrapper.                                                                                                    |
| Multi-stage Docker, non-root user       | Minimal image, secure runtime, no build tools in production.                                                                                                                      |
| Synchronous POST (30-120s)              | Acceptable for a demo. Production would use Celery or SSE streaming.                                                                                                              |
| Substring code search (`str.find()`)  | Simple and sufficient for this scope. Embedding-based search would add infrastructure complexity without proportional benefit.                                                    |

### Running tests

```bash
docker compose exec app python -m pytest -v
```

18 tests covering models, serializers, API endpoints, the plain-text answer endpoint, repo reuse behavior, and reasoning_content replay. Tests run against SQLite inside the Docker container (independent of the PostgreSQL volume).

---

## License

MIT
