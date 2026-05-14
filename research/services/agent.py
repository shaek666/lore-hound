import logging
import time
from datetime import datetime, timezone

from django.utils import timezone as django_timezone

from research.models import ResearchSession
from research.services import tools
from research.services.llm_client import LLMAPIError

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 25
TRUNCATION_RETRY_MESSAGE = (
    "Your response was truncated. Please continue and provide a complete answer."
)

SYSTEM_PROMPT = """You are lore-hound, a codebase research agent. You explore GitHub repositories and answer technical questions about their code. You have access to tools for exploring files and saving findings to a database.

KEY RULES:
1. Always start by exploring the repository structure using list_files("/") to understand the layout before diving deep.
2. Use get_file_summary() to understand what a file contains before reading it.
3. Use read_file() to examine specific implementations.
4. Use search_code() to find relevant code patterns across the repo.
5. Save important findings with save_finding() to build your case.
6. Check get_previous_findings() and list_past_sessions() before starting fresh.
7. Work iteratively: explore -> form hypothesis -> verify -> conclude.
8. When you have enough evidence, provide a clear answer with specific file paths, function names, and line numbers as references.
9. Maximum 25 tool calls per session."""


class ResearchAgent:
    def __init__(self, llm_client, repo_manager):
        self.llm_client = llm_client
        self.repo_manager = repo_manager

    def run(self, session: ResearchSession) -> ResearchSession:
        session.status = ResearchSession.Status.IN_PROGRESS
        session.save(update_fields=["status"])

        repository = session.repository
        tool_defs = tools.get_tool_definitions()

        try:
            with self.repo_manager.access(repository) as repo_access:
                messages = [{"role": "user", "content": session.question}]
                iteration = 0

                while iteration < MAX_ITERATIONS:
                    iteration += 1
                    logger.info(
                        "Agent loop iteration %d/%d for session %d",
                        iteration, MAX_ITERATIONS, session.id,
                    )

                    try:
                        response = self.llm_client.create_with_tools(
                            messages=messages,
                            tools=tool_defs,
                            system_prompt=SYSTEM_PROMPT,
                        )
                    except LLMAPIError as e:
                        session.status = ResearchSession.Status.FAILED
                        session.error_message = f"LLM API error: {e}"
                        session.save()
                        return session

                    usage = response.get("usage", {})
                    session.input_tokens = (session.input_tokens or 0) + usage.get("prompt_tokens", 0)
                    session.output_tokens = (session.output_tokens or 0) + usage.get("completion_tokens", 0)

                    content = response.get("content", "")
                    tool_calls = response.get("tool_calls", [])
                    finish_reason = response.get("finish_reason", "stop")

                    if content:
                        messages.append({"role": "assistant", "content": content})

                    if finish_reason == "length":
                        messages.append({"role": "user", "content": TRUNCATION_RETRY_MESSAGE})
                        continue

                    if finish_reason == "stop" and not tool_calls:
                        session.final_answer = content or "No answer generated."
                        session.status = ResearchSession.Status.COMPLETED
                        session.completed_at = django_timezone.now()
                        session.model_used = self.llm_client.model
                        session.save()
                        logger.info("Session %d completed successfully", session.id)
                        return session

                    if tool_calls:
                        for tc in tool_calls:
                            tc_id = tc.get("id", "")
                            fn = tc.get("function", {})
                            fn_name = fn.get("name", "")
                            fn_args = fn.get("arguments", {})

                            logger.info("Tool call: %s(%s)", fn_name, fn_args)

                            result = tools.execute_tool(
                                fn_name, fn_args,
                                session=session,
                                repo_access=repo_access,
                            )

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "content": str(result),
                            })

                    if iteration >= MAX_ITERATIONS and tool_calls:
                        session.status = ResearchSession.Status.FAILED
                        session.error_message = "Max iterations reached without completing answer"
                        session.completed_at = django_timezone.now()
                        session.save()
                        logger.warning("Session %d hit max iterations", session.id)
                        return session

                session.status = ResearchSession.Status.FAILED
                session.error_message = "Max iterations reached"
                session.completed_at = django_timezone.now()
                session.save()
                return session

        except Exception as e:
            logger.exception("Unexpected error in agent loop")
            session.status = ResearchSession.Status.FAILED
            session.error_message = f"Unexpected error: {e}"
            session.completed_at = django_timezone.now()
            session.save()
            return session
