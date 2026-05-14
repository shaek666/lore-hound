# Design Decisions

## Architecture Overview

The agent uses a direct tool-calling loop against an OpenAI-compatible LLM API. When a user submits a research request, Django creates a `ResearchSession` record, clones the target repository (shallow clone, `--depth 1`), and enters a loop: the LLM receives the conversation history plus tool definitions, decides which tool to call, the agent executes it locally and returns the result, and the loop continues until the LLM produces a final answer or hits the 25-iteration limit.

This design was chosen deliberately over agent frameworks like LangGraph or CrewAI. For this task's scope — a linear exploration loop with 7 tools — a framework adds dependency risk and abstraction overhead without meaningful benefit. A hand-written loop is easier to debug, has predictable behavior, and demonstrates understanding of the underlying LLM tool-calling protocol.

## Database Schema Rationale

Three models form the core schema:

- **Repository** stores metadata about researched repos (URL, name, clone path, file count). Separating this from sessions allows one repo to have many research sessions without duplicating repo metadata.
- **ResearchSession** captures each question asked, its answer, status, and token usage. The status field (`pending` → `in_progress` → `completed`/`failed`) enables tracking long-running sessions and building async support later.
- **ToolCall** records every tool invocation during a session: what was called, with what arguments, what the tool returned (truncated to 500 chars), and in what sequence. This provides a complete audit trail — evaluators can see exactly how the agent reasoned.

The schema is normalized (no denormalization needed at this scale) and uses Django's standard AutoField primary keys for simplicity. At scale, I'd move to UUID primary keys, add database-level indexing on `repository.url` and `session.started_at`, and potentially archive old ToolCall records to reduce table size.

## Key Design Decisions

**LLM provider agnosticism**: The `LLMClient` makes raw HTTP requests to any OpenAI-compatible chat completions endpoint. This means the agent works with SiliconFlow, OpenAI, Anthropic (via proxy), or local models with zero code changes — just swap the env vars.

**Synchronous execution**: The agent runs synchronously within the HTTP request. This means POST requests can take 30-120 seconds. For a demo/take-home this is acceptable; for production I'd use Celery or Django Channels to make it async.

**Shallow git clone**: Cloning with `--depth 1` and `--single-branch` minimizes disk usage and clone time, which matters when researching unfamiliar repos. The trade-off is no git history access, but the spec doesn't require it.

**Tool organization**: Tools are plain functions decorated with `@register_tool` and defined alongside their JSON schemas. This keeps the schema and implementation together, making it easy to add or modify tools. The `execute_tool` dispatch function handles logging, error wrapping, and result truncation centrally.

**File-level summarization**: The `get_file_summary` tool uses Python's `ast` module to extract imports, function names, and class names from Python files without reading the full content. This lets the agent decide whether a file is worth reading in detail — critical for fitting large codebases in the context window.

## What I'd Do Differently With More Time

1. **Parallel file reads**: Allow the agent to read multiple small files in parallel to speed up exploration.
2. **Retrieval-augmented file search**: Index file contents with embeddings for semantic search instead of substring matching.
3. **Streaming responses**: Stream the agent's progress (tool calls, partial findings) via Server-Sent Events so the user sees progress during long sessions.
4. **More robust context management**: Dynamically trim older tool results from the conversation when approaching context limits.

## AI Tool Usage

This project was built using an AI coding assistant (Claude Code via OpenCode). The assistant generated the initial code for all files based on detailed specifications, and I reviewed and edited the output. Specifically:

- **AI-generated**: Initial versions of models.py, views.py, serializers.py, all 4 service files, seed_data command, README.md, docker-compose.yml, settings.py
- **Hand-written after AI review**: Bug fixes to imports, the `_is_binary` detection logic, error handling paths in the agent loop, the path traversal protection in RepoAccess
- **AI-assisted then edited**: DECISIONS.md (restructured for clarity), pyproject.toml dependencies, Docker healthcheck configuration

The AI was most helpful for boilerplate (Django patterns, DRF serializers, management commands) and for getting the 7-tool infrastructure running quickly. It struggled with Windows-specific issues (Docker volume paths, git bash vs PowerShell path separators) which required manual debugging. All AI output was reviewed by reading every generated file — no code was accepted without understanding it.

## Limitations

- No streaming feedback during research sessions (the POST blocks until complete)
- The `search_code` tool uses substring matching, which misses semantically relevant code
- No authentication — the API is open to anyone who can reach the server
- The agent can't handle repos with exclusively non-Python languages for structural summaries
