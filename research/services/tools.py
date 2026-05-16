import logging
import threading
from typing import Any, Optional

from research.models import ResearchSession, ToolCall

logger = logging.getLogger(__name__)

TOOL_REGISTRY = {}
_sequence_lock = threading.Lock()
_sequence_counter = 0
_tool_definitions_cache: list[dict[str, Any]] | None = None


def _get_seq():
    global _sequence_counter
    with _sequence_lock:
        _sequence_counter += 1
        return _sequence_counter


def register_tool(fn):
    TOOL_REGISTRY[fn.__name__] = fn
    return fn


def execute_tool(tool_name: str, arguments: dict[str, Any], session: Optional[ResearchSession] = None, repo_access=None):
    if tool_name not in TOOL_REGISTRY:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        result = TOOL_REGISTRY[tool_name](arguments, repo_access=repo_access, session=session)

        if session:
            output_summary = str(result)
            if len(output_summary) > 500:
                output_summary = output_summary[:500] + "..."
            ToolCall.objects.create(
                session=session,
                tool_name=tool_name,
                tool_input=arguments,
                tool_output_summary=output_summary,
                file_path=arguments.get("path") or arguments.get("file_path"),
                sequence_number=_get_seq(),
            )

        return result
    except Exception as e:
        logger.exception("Tool %s failed", tool_name)
        return {"error": f"Tool {tool_name} failed: {e}"}


def get_tool_definitions() -> list[dict[str, Any]]:
    global _tool_definitions_cache
    if _tool_definitions_cache is not None:
        return _tool_definitions_cache
    _tool_definitions_cache = [
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files and directories at a given path in the repository. Use this to explore the project structure before reading files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative directory path (e.g., '/' for root, '/src' for src folder)",
                        },
                        "pattern": {
                            "type": "string",
                            "description": "Optional glob filter (e.g., '*.py')",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the contents of a file. Use get_file_summary first to understand what a file contains before reading it entirely.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative file path (e.g., '/src/main.py')",
                        },
                        "max_length": {
                            "type": "integer",
                            "description": "Maximum characters to read (minimum 1000, default 10000). Read at least 1000 chars each call to avoid excessive round trips.",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Line number to start reading from (1-indexed). Use this to read specific sections of a file.",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_code",
                "description": "Search for a text pattern across all files in the repository. Useful for finding where functions are defined or used.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Text to search for (case-insensitive substring match)",
                        },
                        "file_pattern": {
                            "type": "string",
                            "description": "Optional file glob pattern (e.g., '*.py' to only search Python files)",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_file_summary",
                "description": "Get a summary of a file including size, line count, and for Python files: imports, function names, and class names. Use this BEFORE read_file to decide if a file is worth reading.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative file path (e.g., '/src/main.py')",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "save_finding",
                "description": "Save an observation or finding about a specific file during your research. This is stored in the database and can be reviewed in the final answer.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Path to the file this finding relates to",
                        },
                        "note": {
                            "type": "string",
                            "description": "Your observation or finding about this file",
                        },
                    },
                    "required": ["file_path", "note"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_previous_findings",
                "description": "Retrieve findings and answers from previous research sessions on the same repository. Use this before exploring to avoid duplicating work.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_past_sessions",
                "description": "List past research sessions for the current repository, including their questions and status.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of past sessions to return",
                        },
                    },
                },
            },
        },
    ]
    return _tool_definitions_cache


@register_tool
def list_files(arguments: dict[str, Any], repo_access=None, session: Optional[ResearchSession] = None):
    if repo_access is None:
        return {"error": "Repository access not available"}
    path = arguments.get("path", "/")
    pattern = arguments.get("pattern")
    return repo_access.list_files(path, pattern)


@register_tool
def read_file(arguments: dict[str, Any], repo_access=None, session: Optional[ResearchSession] = None):
    if repo_access is None:
        return {"error": "Repository access not available"}
    path = arguments.get("path")
    max_length = max(arguments.get("max_length", 10000), 1000)
    offset = arguments.get("offset", 0)
    if not path:
        return {"error": "path is required"}
    content = repo_access.read_file(path, max_length, offset)
    return {"path": path, "content": content}


@register_tool
def search_code(arguments: dict[str, Any], repo_access=None, session: Optional[ResearchSession] = None):
    if repo_access is None:
        return {"error": "Repository access not available"}
    query = arguments.get("query")
    file_pattern = arguments.get("file_pattern")
    if not query:
        return {"error": "query is required"}
    results = repo_access.search_code(query, file_pattern)
    return {"query": query, "results": results, "count": len(results)}


@register_tool
def get_file_summary(arguments: dict[str, Any], repo_access=None, session: Optional[ResearchSession] = None):
    if repo_access is None:
        return {"error": "Repository access not available"}
    path = arguments.get("path")
    if not path:
        return {"error": "path is required"}
    summary = repo_access.get_file_summary(path)
    return summary


@register_tool
def save_finding(arguments: dict[str, Any], repo_access=None, session: Optional[ResearchSession] = None):
    file_path = arguments.get("file_path")
    note = arguments.get("note")
    if session:
        ToolCall.objects.create(
            session=session,
            tool_name="save_finding",
            tool_input={"file_path": file_path, "note": note},
            file_path=file_path,
            tool_output_summary="Finding saved",
        )
    return {"status": "saved", "note": note, "file_path": file_path}


@register_tool
def get_previous_findings(arguments: dict[str, Any], repo_access=None, session: Optional[ResearchSession] = None):
    if not session:
        return {"findings": []}
    repo = session.repository
    prev_sessions = repo.sessions.filter(status="completed").exclude(id=session.id)[:5]
    findings = []
    for s in prev_sessions:
        calls = s.tool_calls.filter(tool_name__in=["save_finding", "read_file"]).values(
            "tool_name", "file_path", "tool_output_summary"
        )[:20]
        findings.append({
            "session_id": s.id,
            "question": s.question,
            "answer_preview": (s.final_answer or "")[:200],
            "calls": list(calls),
        })
    return {"findings": findings}


@register_tool
def list_past_sessions(arguments: dict[str, Any], repo_access=None, session: Optional[ResearchSession] = None):
    if not session:
        return {"sessions": []}
    limit = arguments.get("limit", 10)
    repo = session.repository
    past = repo.sessions.exclude(id=session.id).order_by("-started_at")[:limit]
    return {
        "sessions": [
            {
                "session_id": s.id,
                "question": s.question,
                "status": s.status,
                "tool_calls_count": s.tool_calls.count(),
                "started_at": s.started_at.isoformat() if s.started_at else None,
            }
            for s in past
        ]
    }
