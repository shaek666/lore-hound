# Design Decisions

## Architecture Overview

The agent runs a direct tool-calling loop against an OpenAI-compatible LLM API. When a user submits a research request, Django creates a `ResearchSession`, shallow-clones the target repository (`git clone --depth 1 --single-branch`), and enters a synchronous loop: the LLM receives conversation history plus tool definitions, decides which tool to call, the agent executes it locally and returns the result, and the loop continues until the LLM emits a final answer or hits the 60-iteration limit.

This design was chosen deliberately over LangGraph, CrewAI, or similar frameworks. For this task's scope (a linear exploration loop with 7 tools), a framework adds dependency risk, abstraction overhead, and debugging surface area without meaningful benefit. A hand-written loop is trivially debuggable (every iteration is logged, every tool call is persisted to the database), and it demonstrates understanding of the underlying LLM tool-calling protocol rather than relying on framework magic.

**Production consideration**: The synchronous loop blocks the HTTP request for 30-120 seconds. For a demo this is fine; for production I'd offload to Celery or use SSE to stream progress.

---

## Database Schema

Three models form the core schema, with a fourth field added iteratively:

- **Repository** stores metadata about researched repos (URL, name, owner, clone path, file count). Separating this from sessions lets one repo have many research sessions without duplicating repo metadata.
- **ResearchSession** captures each question, its final answer, **reasoning** (extracted from the agent's chain-of-thought), status, model used, and token usage. The `Status` enum enables tracking long-running sessions and is a precondition for any async migration.
- **ToolCall** records every tool invocation: name, input arguments, output summary (truncated to 500 chars), file path touched, sequence number, token count. Provides a complete audit trail for evaluators.

The `reasoning` field was added later after observing that the agent's final answers were dense walls of text. By prompting the LLM to emit a `REASONING:` section before its `ANSWER:`, the backend can split the two, store them separately, and present them differently in the API.

**Normalization**: Cleanly normalized. No denormalization needed at this scale. At scale I'd move to UUID primary keys, add `repository.url` and `session.started_at` database-level indexes, and archive ToolCall records to a time-series store.

---

## Key Design Decisions

### Reasoning–answer splitting

The LLM is instructed to structure its final response with `REASONING:` and `ANSWER:` headings. The agent loop parses on `"ANSWER:"` — the text before it becomes the reasoning, the text after becomes the final answer. If no `ANSWER:` delimiter is found, the entire output is stored as `final_answer` and `reasoning` stays null. Degradation is safe by design.

This costs ~15 tokens in the system prompt and zero architectural complexity. The alternative — a separate reasoning-extraction LLM call — would double latency for marginal gain.

### JSON response ergonomics (no escaped newlines)

`final_answer` and `reasoning` are serialized as **arrays of lines** rather than single strings:

```json
{"final_answer": ["## Overview", "", "The DI system..."]}
```

A single string with `\n` escaping is unreadable in `curl`, `jq`, or any JSON viewer — every newline doubles as `\\n` and code fences become a nightmare. An array of lines is trivially joinable (`jq -r '.final_answer[]'`), hits no JSON escaping issues, and is the same data underneath. The database still stores the canonical single string; the serialization layer splits on `\n` at the boundary. This is a presentation decision, not a storage decision.

### Plain-text markdown answer endpoint

`GET /api/research/{id}/answer/` returns `Content-Type: text/markdown` — no JSON wrapper. This is a **human endpoint**: for `curl`, Postman, or anything that just wants to read the answer. It dodges every JSON-escaped-backslash problem entirely. One view, one URL route, zero dependencies. The single best investment for API readability.

### LLM provider agnosticism

The `LLMClient` speaks raw HTTP to any OpenAI-compatible `/chat/completions` endpoint. Swapping providers is an env var change (`LLM_BASE_URL`, `LLM_MODEL`). No SDK lock-in, no provider-specific error handling to maintain per SDK.

The trade-off: no streaming support (SSE parsing is provider-specific), and we lose SDK-level retry/rate-limit intelligence. The client implements its own retry with exponential backoff for 429 and 5xx responses.

### LLM model choice

The agent defaults to **DeepSeek-V4-Pro** (via SiliconFlow's OpenAI-compatible API). This was chosen over the alternatives for the following reasons:

| Model | Why not |
|---|---|
| **DeepSeek-V4-Pro** (chosen) | Strong tool-calling adherence at ~$0.42/M input tokens. The agent makes 20-40 calls per session — cost matters. Function-calling format matches OpenAI's spec exactly, so the raw HTTP client works without adaptation. |
| Claude Sonnet 4 | Better reasoning quality, but 3-5x the cost per token. For a research agent that re-reads files and retries, cost adds up fast. Also requires a proxy layer (OpenAI-compatible adapter) since Anthropic's native API uses a different tool-calling format. |
| GPT-4o | Solid choice, comparable cost to DeepSeek. Ultimately DeepSeek's function-calling reliability was slightly better in testing for multi-turn tool loops. |
| **Open-source** local models (Llama, Qwen) | Not viable for this task — local models on consumer hardware lack the context window (32k+) and instruction-following precision needed for reliable multi-step tool use. |

**Default model is configurable**: set `LLM_MODEL` in `.env` to switch. The provider is also independently configurable via `LLM_BASE_URL`, so you can use DeepSeek through a different provider or a different model through SiliconFlow without code changes.

The model is documented in the response (`model_used` field) and stored per session, so you can compare quality across providers empirically.

### Thread safety in the agent loop

The original implementation created a `ThreadPoolExecutor` but only cleaned it up on exceptions, leaking the thread pool on successful completions. Fixed by using `with ThreadPoolExecutor(max_workers=5) as executor:` — a context manager guarantees `shutdown(wait=True)` on all exit paths. Basic Python resource management, not an optimization.

### Redundant `load_dotenv()` removal

`settings.py` already calls `load_dotenv(override=True)` at module import time. `views.py` had a second call in `_build_agent()` cargo-cult defensiveness that did nothing except a redundant filesystem read per POST request. Removed. One source of truth for environment loading.

### Failed sessions return 500, not 201

The original code returned `HTTP 201 Created` regardless of whether the agent completed or failed. This was a bug: 201 promises successful resource creation. A failed session exists in the database but the response status must signal failure. Fixed: 201 on `COMPLETED`, 500 on anything else.

### Multi-stage Docker build with non-root user

- **Builder**: installs dependencies with `uv sync --frozen --no-dev` (leveraging Docker layer caching)
- **Runtime**: `python:3.11-slim` with only `git` added, virtualenv copied from builder, runs as `uid=1001`

The entrypoint runs `migrate` then `collectstatic` before `exec gunicorn`, ensuring production readiness without a separate init step. `.dockerignore` explicitly excludes `.env`, `.md` files, `.git/`, and IDE artifacts.

**Windows-specific pitfall**: File execute permissions don't survive `COPY` from a Windows host. Dockerfile explicitly `chmod +x` the entrypoint script — without it, the container fails at startup with "permission denied" on any Windows developer's machine.

### SECRET_KEY and deploy defaults

The default `SECRET_KEY` was bumped from 36 to 72 characters to pass Django's `check --deploy` W009 (requires ≥50 chars). Configurable via `DJANGO_SECRET_KEY` env var. `ALLOWED_HOSTS` defaults to `"*"` but is configurable via `DJANGO_ALLOWED_HOSTS` (comma-separated). Remaining `check --deploy` warnings are all HTTPS-related (HSTS, SSL redirect, secure cookies) not actionable without a reverse proxy terminating TLS.

### Useless `cache_from` removed from docker-compose

`build.cache_from` was set to `ghcr.io/astral-sh/uv:latest` — an image that has nothing to do with the built image. Docker was ignoring it and rebuilding from scratch every time. Removed. Cache-from is useful when pointing to a previously built version of the same image, not a random unrelated image.

---

## What I'd Do Differently With More Time

1. **Streaming agent progress**: The synchronous POST is the single worst UX choice in this project. SSE or WebSocket streaming would let users watch tool calls in real time instead of staring at a loading spinner.

2. **Parallel file reading**: The agent reads one file at a time. A `batch_read_files` tool that takes multiple paths and returns them in one response would cut exploration time by 2-3x for large codebases.

3. **Structured answer sections**: The current `REASONING:` / `ANSWER:` split is a first step. The next iteration would prompt the agent to emit typed sections (overview, components with file links, code examples, flow) and return them as a JSON array for differential frontend rendering.

4. **Hyperlinked file references**: Every `path/to/file.py:42` in the answer could be a permalink to the source on GitHub. The repository URL is already available — a frontend-side regex rewrite would turn plain paths into clickable links. ~10 lines of JavaScript.

5. **Embedding-based code search**: The current `search_code` uses Python `str.find()` substring matching that misses semantically relevant code. Embedding indexing would dramatically improve the agent's ability to find relevant functions without reading every file.

6. **Context window management**: The agent loop appends every tool call result to the message list indefinitely. A dynamic trimming strategy (summarize older results, drop tool_call entries beyond a threshold, keep only the N most recent messages) would extend the agent's effective exploration budget.

7. **Idempotent session POST**: If the POST times out on the client side, the caller has no way to know whether the agent completed or failed. A session ID is returned at the start — the client could poll `GET /api/research/{id}/` to check status. Currently the POST blocks until completion, so this pattern would require the Celery/SSE migration first.

---

## AI Tool Usage

This project was built using an AI coding assistant (Sisyphus via OpenCode). The assistant generated initial code for all files based on detailed specifications, then iteratively refined each file through conversation. Specifically:

- **AI-generated (initial pass)**: models.py, views.py, serializers.py, all 4 service files, management commands, Dockerfile, docker-compose.yml, settings.py, Makefile, .dockerignore, .env.example
- **AI-generated (iterative refinements)**: agent prompt engineering (REASONING/ANSWER split), ThreadPoolExecutor context manager fix, serialization changes (array-of-lines, reasoning field), Dockerfile chmod +x for Windows compat, SECRET_KEY length increase, redundant load_dotenv removal, failed session status code fix
- **Hand-written (manual review/debug)**: Path traversal protection in RepoAccess, `_is_binary` detection logic, Docker entrypoint chmod issue on Windows, PowerShell quoting in shell commands

The AI was most effective at Django boilerplate (models, serializers, views, management commands) and at systematic refactoring across multiple files (adding a model field → migrating → updating serializers → adding tests). It struggled with:
- **Windows-specific issues**: Docker permissions, PowerShell vs bash path separators, Makefile command compatibility all required manual intervention
- **Deep debugging of LLM responses**: When the agent produced malformed output, the AI assistant couldn't diagnose the root cause without manual inspection of raw API responses
- **Trade-off decisions**: The AI tends to suggest the "most complete" solution rather than the simplest one that meets requirements. Every decision was reviewed and simplified where appropriate.

All AI-generated code was reviewed by reading every changed file. No code was accepted without understanding its purpose and verifying it meets requirements.

---

## Limitations

- **Synchronous POST blocks for 30-120s**: No progress feedback during research. Callers must set long HTTP timeouts or switch to polling.
- **Substring code search**: `search_code` uses `str.find()`, which misses semantically relevant code. Embedding-based search would be more accurate but adds infrastructure complexity.
- **No authentication**: The API is open to anyone who can reach the server. Suitable for a demo/internal tool; production would need API keys or OAuth.
- **Python-only structural summaries**: The `get_file_summary` tool uses `ast.parse()` — non-Python files get line-count summaries only.
- **No context window eviction**: The agent's message list grows unboundedly. Long research sessions risk hitting the LLM's context limit before reaching an answer.
- **No streaming**: The entire answer is generated before the POST returns. Users can't see partial progress.
- **Static files warning in tests**: WhiteNoise warns about a missing `staticfiles/` directory during unit tests. Cosmetic — the directory is created at Docker runtime via `collectstatic` and the entrypoint. Fixing it would add a `conftest.py` that creates the directory, which is more code than the warning merits.
