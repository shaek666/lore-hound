import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from django.utils import timezone as django_timezone

from research.models import ResearchSession
from research.services import tools
from research.services.llm_client import LLMAPIError

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 60
TRUNCATION_RETRY_MESSAGE = (
    "Your response was truncated. Please continue and provide a complete answer."
)

SYSTEM_PROMPT = """You are lore-hound, a codebase research agent. Answer the user's question by exploring the GitHub repository.

EXPLORATION FLOW (follow this order):
1. Call list_files("/") ONCE to see the project structure
2. Call list_files() on relevant subdirectories (max 3)
3. Use get_file_summary() on relevant files to understand what they contain
4. Once you know the relevant files, use read_file() with max_length >= 3000 to read substantial code sections at once. Do NOT read tiny snippets.
5. Use search_code() to find specific function/class definitions
6. Read at most 2-3 files in detail, then produce your answer

CRITICAL SPEED RULES:
- After 3 read_file() calls, you MUST stop reading and produce your answer.
- Each tool call costs ~5 seconds of real time. Be efficient.
- Use read_file() with max_length >= 3000 to read entire functions at once.
- Do NOT read the same file more than twice.
- NEVER use max_length < 1000 — it's extremely wasteful.
- If you've read 2-3 relevant files, you have enough info to answer.
- Provide your answer with specific file paths, function names, and line numbers.
- Read_file() offset parameter is 1-indexed (line number).

FINAL ANSWER FORMAT:
Before your final answer, include a REASONING section that briefly explains your exploration process and how you arrived at your conclusions. Then provide the complete ANSWER.
Use EXACTLY these headings:
REASONING:
(2-5 sentences explaining what files you examined and what you found)

ANSWER:
(your complete answer with markdown formatting, code blocks, file paths, etc.)"""


class ResearchAgent:
    def __init__(self, llm_client, repo_manager):
        self.llm_client = llm_client
        self.repo_manager = repo_manager

    def run(self, session: ResearchSession) -> ResearchSession:
        session.status = ResearchSession.Status.IN_PROGRESS
        session.model_used = self.llm_client.model
        session.save(update_fields=["status", "model_used"])

        repository = session.repository
        tool_defs = tools.get_tool_definitions()

        try:
            with (
                self.repo_manager.access(repository) as repo_access,
                ThreadPoolExecutor(max_workers=5) as executor,
            ):
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

                    if content or tool_calls:
                        assistant_msg: dict[str, Any] = {"role": "assistant"}
                        if content:
                            assistant_msg["content"] = content
                        if tool_calls:
                            assistant_msg["tool_calls"] = [
                                {
                                    "id": tc.get("id", ""),
                                    "type": tc.get("type", "function"),
                                    "function": {
                                        "name": tc.get("function", {}).get("name", ""),
                                        "arguments": json.dumps(tc.get("function", {}).get("arguments", {})),
                                    },
                                }
                                for tc in tool_calls
                            ]
                        messages.append(assistant_msg)

                    if finish_reason == "length":
                        messages.append({"role": "user", "content": TRUNCATION_RETRY_MESSAGE})
                        continue

                    if finish_reason == "stop" and not tool_calls:
                        content = content or ""
                        reasoning = ""
                        answer = content

                        if "ANSWER:" in content:
                            raw_answer = content.split("ANSWER:", 1)[1].strip()
                            reasoning_raw = content.split("ANSWER:", 1)[0].strip()
                            reasoning = reasoning_raw.replace("REASONING:", "", 1).strip()
                            answer = raw_answer

                        session.reasoning = reasoning
                        session.final_answer = answer
                        session.status = ResearchSession.Status.COMPLETED
                        session.completed_at = django_timezone.now()
                        session.model_used = self.llm_client.model
                        session.save()
                        logger.info("Session %d completed successfully", session.id)
                        return session

                    if tool_calls:
                        futures: dict[Any, dict[str, Any]] = {}
                        for tc in tool_calls:
                            fn = tc.get("function", {})
                            fn_name = fn.get("name", "")
                            fn_args = fn.get("arguments", {})
                            logger.info("Tool call: %s(%s)", fn_name, fn_args)
                            future = executor.submit(
                                tools.execute_tool, fn_name, fn_args,
                                session=session, repo_access=repo_access,
                            )
                            futures[future] = tc

                        for future in as_completed(futures):
                            tc = futures[future]
                            tc_id = tc.get("id", "")
                            result = future.result()
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
