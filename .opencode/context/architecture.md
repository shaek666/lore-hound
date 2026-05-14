# Architecture: lore-hound

## Tech Stack
- **Language**: Python 3.11+
- **Framework**: Django 5.1
- **API Layer**: Django REST Framework (@api_view, no ViewSets)
- **Database**: PostgreSQL 16 (Docker), data volume mapped to D: drive (./data/postgres)
- **LLM**: OpenAI-compatible API (SiliconFlow), configurable via env vars (LLM_API_KEY, LLM_BASE_URL, LLM_MODEL)
- **Package Manager**: uv (all caches on D: drive, UV_CACHE_DIR=.uv-cache)
- **Containerization**: Docker Compose (PostgreSQL only)

## Project Structure
```
lore-hound/
├── pyproject.toml           # uv deps: django, djangorestframework, psycopg[binary], httpx, python-dotenv
├── docker-compose.yml       # PostgreSQL 16, port 5432, volume ./data/postgres
├── .env.example             # All configurable env vars with placeholder values
├── lorehound/               # Django project package
│   ├── settings.py          # DB from DATABASE_URL, LLM from LLM_* vars
│   └── urls.py              # Includes research/ URLs
└── research/                # Django app
    ├── models.py            # Repository, ResearchSession, ToolCall
    ├── views.py             # 3 API endpoints (@api_view)
    ├── serializers.py       # DRF serializers
    ├── urls.py              # /api/research/ routing
    └── services/
        ├── llm_client.py    # OpenAI-compatible API wrapper
        ├── repo_manager.py  # Git clone + filesystem access
        ├── tools.py         # 7 tool implementations + JSON schemas
        └── agent.py         # Agent loop engine (max 25 iterations)
```

## Key Architecture Decisions
1. **Raw tool-calling loop** (no LangChain/LangGraph) — evaluates engineering judgment
2. **Synchronous agent execution** — POST blocks for 30-120s during research
3. **Shallow git clone** (--depth 1) — minimizes disk usage and clone time
4. **Normalized DB schema** — Repository (1) → ResearchSession (many) → ToolCall (many)
5. **File-level summarization** — Agent reads summaries before deep-diving files
6. **No auth, no multi-user** — explicitly not required by spec

## Database Schema
- **Repository**: url (unique), name, clone_path, file_count, owner, default_branch
- **ResearchSession**: FK→Repository, question, final_answer, status (pending/in_progress/completed/failed), token tracking, timestamps
- **ToolCall**: FK→ResearchSession, tool_name, tool_input (JSON), tool_output_summary, file_path, sequence_number

## Critical Constraints
- ALL Python artifacts on D: drive (C: drive is full)
- uv for everything (no system Python, no pip install --user)
- Docker PostgreSQL data volume on D: drive
- No frontend, no CLI, no auth
