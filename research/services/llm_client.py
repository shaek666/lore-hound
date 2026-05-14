import json
import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class LLMAPIError(Exception):
    pass


class LLMClient:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=120.0)

    def create_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> dict:
        body = {
            "model": self.model,
            "messages": list(messages),
            "tools": tools,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if system_prompt:
            body["messages"].insert(0, {"role": "system", "content": system_prompt})

        for attempt in range(3):
            try:
                resp = self._client.post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning("Rate limited, retrying in %ds", wait)
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500 and attempt < 2:
                    wait = 2 ** (attempt + 1)
                    logger.warning("Server error %d, retrying in %ds", resp.status_code, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]
                msg = choice["message"]

                result = {
                    "content": msg.get("content") or "",
                    "tool_calls": [],
                    "finish_reason": choice.get("finish_reason", "stop"),
                    "usage": data.get("usage", {}),
                }

                if "tool_calls" in msg:
                    for tc in msg["tool_calls"]:
                        try:
                            args = json.loads(tc["function"]["arguments"])
                        except json.JSONDecodeError:
                            args = {}
                        result["tool_calls"].append({
                            "id": tc["id"],
                            "type": tc["type"],
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": args,
                            },
                        })

                return result

            except httpx.HTTPStatusError as e:
                if attempt < 2:
                    continue
                raise LLMAPIError(f"API error: {e.response.status_code} {e.response.text}") from e
            except httpx.TimeoutException as e:
                if attempt < 2:
                    continue
                raise LLMAPIError("Request timed out after 3 retries") from e
            except httpx.RequestError as e:
                raise LLMAPIError(f"Request failed: {e}") from e

        raise LLMAPIError("Max retries exceeded")

    def close(self):
        self._client.close()


class MockLLMClient:
    def __init__(self, responses: Optional[list[dict]] = None):
        self.responses = responses or []
        self.call_index = 0

    def create_with_tools(self, messages, tools, system_prompt=None, max_tokens=4096):
        if self.call_index < len(self.responses):
            resp = self.responses[self.call_index]
            self.call_index += 1
            return resp
        return {
            "content": "Mock final answer.",
            "tool_calls": [],
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
