# lore-hound

A tenacious AI agent that hunts through codebases, sniffs out architectural patterns, and leaves a trail of cited findings in its Django-PostgreSQL kennel.

Built as a Senior Backend Developer take-home task for [CodeFusion AI](https://www.codefusionai.com).

## Prerequisites

- **Python 3.11+** via [uv](https://docs.astral.sh/uv/)
- **Docker Desktop** (for PostgreSQL)
- An **LLM API key** with an OpenAI-compatible endpoint (e.g., SiliconFlow, OpenAI, Anthropic)

## Quick Start

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd lore-hound

# 2. Configure environment
cp .env.example .env
# Edit .env: set your LLM_API_KEY, LLM_BASE_URL, and LLM_MODEL

# 3. Start PostgreSQL
docker compose up -d

# 4. Install Python dependencies
uv sync

# 5. Run migrations
uv run python manage.py migrate

# 6. (Optional) Seed sample data
uv run python manage.py seed_data

# 7. Start the development server
uv run python manage.py runserver
```

## API Usage

### Start a research session (POST — may take 30-120s)

```bash
curl -X POST http://localhost:8000/api/research/ \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/tiangolo/fastapi",
    "question": "How does FastAPI handle dependency injection?"
  }'
```

### Get session details

```bash
curl http://localhost:8000/api/research/1/
```

### List past sessions for a repo

```bash
curl "http://localhost:8000/api/research/?repo_url=https://github.com/tiangolo/fastapi"
```

### Browse via admin

```bash
# Create a superuser first
uv run python manage.py createsuperuser
# Visit http://localhost:8000/admin/
```

## Project Structure

```
lore-hound/
├── pyproject.toml          # Python dependencies (uv)
├── docker-compose.yml      # PostgreSQL 16
├── manage.py               # Django CLI entrypoint
├── lorehound/              # Django project settings
└── research/               # Core app
    ├── models.py           # Repository, ResearchSession, ToolCall
    ├── views.py            # REST API endpoints (DRF @api_view)
    ├── serializers.py      # Request/response serializers
    ├── urls.py             # API routing
    └── services/
        ├── llm_client.py   # OpenAI-compatible API wrapper
        ├── repo_manager.py # Git clone + filesystem access
        ├── tools.py        # 7 agent tools + JSON schemas
        └── agent.py        # Agent loop engine
```

## Technology Choices

| Component | Choice | Rationale |
|-----------|--------|-----------|
| Framework | Django 5.1 | Required by spec |
| API Layer | Django REST Framework | Industry standard for Django APIs |
| Database | PostgreSQL 16 (Docker) | Required by spec, production-grade |
| LLM Client | Raw HTTP (no SDK) | Provider-agnostic, works with any OpenAI-compatible API |
| Agent Loop | Custom (no framework) | Demonstrates engineering understanding |
| Package Mgr | uv | Fast Python package management, respects D: drive storage |

