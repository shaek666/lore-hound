from django.test import TestCase
from rest_framework import status
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
from pathlib import Path

from research.models import Repository, ResearchSession, ToolCall
from research.serializers import (
    ResearchSessionDetailSerializer,
    ResearchSessionResultSerializer,
    StartResearchSerializer,
)
from research.services.agent import ResearchAgent
from research.services.repo_manager import RepoManager


class ModelTests(TestCase):
    def test_create_repository(self):
        repo = Repository.objects.create(
            url="https://github.com/tiangolo/fastapi",
            name="fastapi",
            owner="tiangolo",
        )
        assert repo.name == "fastapi"
        assert str(repo) == "fastapi"

    def test_create_session(self):
        repo = Repository.objects.create(
            url="https://github.com/tiangolo/fastapi",
            name="fastapi",
        )
        session = ResearchSession.objects.create(
            repository=repo,
            question="How does DI work?",
            status=ResearchSession.Status.COMPLETED,
            reasoning="I looked at models.py",
            final_answer="Through solve_dependencies()",
        )
        assert session.status == "completed"
        assert session.reasoning == "I looked at models.py"
        assert str(session) == "fastapi: How does DI work?"

    def test_create_toolcall(self):
        repo = Repository.objects.create(url="https://github.com/tiangolo/fastapi", name="fastapi")
        session = ResearchSession.objects.create(repository=repo, question="Test?")
        tc = ToolCall.objects.create(
            session=session,
            tool_name="read_file",
            tool_input={"path": "/main.py"},
            sequence_number=1,
        )
        assert tc.tool_name == "read_file"
        assert str(tc) == "[1] read_file"

    def test_ensure_repo_reuses_existing_clone(self):
        with TemporaryDirectory() as clone_dir:
            repo = Repository.objects.create(
                url="https://github.com/tiangolo/fastapi",
                name="fastapi",
                owner="tiangolo",
                clone_path=clone_dir,
            )

            manager = RepoManager(clone_base_dir=clone_dir)
            with patch("research.services.repo_manager.subprocess.run") as mock_run:
                reused = manager.ensure_repo(repo.url)

            assert reused.id == repo.id
            assert reused.clone_path == clone_dir
            mock_run.assert_not_called()


class AgentTests(TestCase):
    def test_replays_reasoning_content_between_llm_calls(self):
        with TemporaryDirectory() as repo_dir:
            Path(repo_dir, "routes.py").write_text("print('hello')\n", encoding="utf-8")

            repo = Repository.objects.create(
                url="https://github.com/tiangolo/fastapi",
                name="fastapi",
                owner="tiangolo",
                clone_path=repo_dir,
            )
            session = ResearchSession.objects.create(
                repository=repo,
                question="How is routing organized?",
            )

            class RecordingLLMClient:
                model = "test-model"

                def __init__(self):
                    self.calls = []
                    self.responses = [
                        {
                            "content": "",
                            "reasoning_content": "Thinking about routing",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": {"path": "routes.py", "max_length": 1000},
                                    },
                                }
                            ],
                            "finish_reason": "tool_calls",
                            "usage": {"prompt_tokens": 12, "completion_tokens": 6},
                        },
                        {
                            "content": "Routing is organized around app and router modules.",
                            "reasoning_content": "I found the routing modules.",
                            "tool_calls": [],
                            "finish_reason": "stop",
                            "usage": {"prompt_tokens": 10, "completion_tokens": 8},
                        },
                    ]

                def create_with_tools(self, messages, tools, system_prompt=None, max_tokens=4096):
                    self.calls.append({
                        "messages": json.loads(json.dumps(messages)),
                        "tools": json.loads(json.dumps(tools)),
                        "system_prompt": system_prompt,
                        "max_tokens": max_tokens,
                    })
                    return self.responses[len(self.calls) - 1]

            llm = RecordingLLMClient()
            agent = ResearchAgent(llm_client=llm, repo_manager=RepoManager(clone_base_dir=repo_dir))

            completed = agent.run(session)
            completed.refresh_from_db()

            assert completed.status == ResearchSession.Status.COMPLETED
            assert completed.reasoning == "I found the routing modules."
            assert completed.final_answer == "Routing is organized around app and router modules."
            assert len(llm.calls) == 2
            second_call_messages = llm.calls[1]["messages"]
            assistant_messages = [message for message in second_call_messages if message["role"] == "assistant"]
            assert assistant_messages[0]["reasoning_content"] == "Thinking about routing"


class SerializerTests(TestCase):
    def test_start_research_serializer_validates(self):
        s = StartResearchSerializer(data={"repo_url": "not-a-url", "question": "short"})
        assert not s.is_valid()

        s = StartResearchSerializer(data={
            "repo_url": "https://github.com/tiangolo/fastapi",
            "question": "How does FastAPI handle dependency injection?",
        })
        assert s.is_valid()


class APITests(TestCase):
    def test_list_sessions_empty(self):
        resp = self.client.get("/api/research/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []

    def test_get_session_404(self):
        resp = self.client.get("/api/research/99999/")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_create_session_invalid_data(self):
        resp = self.client.post(
            "/api/research/",
            {"repo_url": "not-a-url", "question": "short"},
            content_type="application/json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


class AnswerEndpointTests(TestCase):
    def _make_session(self, **kw):
        repo = Repository.objects.create(url="https://github.com/tiangolo/fastapi", name="fastapi")
        return ResearchSession.objects.create(repository=repo, question="Test?", **kw)

    def test_answer_endpoint_returns_200(self):
        session = self._make_session(
            status=ResearchSession.Status.COMPLETED,
            final_answer="## Hello\n\nWorld",
        )
        resp = self.client.get(f"/api/research/{session.id}/answer/")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/markdown")

    def test_answer_endpoint_renders_final_answer(self):
        session = self._make_session(
            status=ResearchSession.Status.COMPLETED,
            final_answer="## Result\n\nSome text here.",
        )
        resp = self.client.get(f"/api/research/{session.id}/answer/")
        assert "## Result" in resp.content.decode()
        assert "Some text here." in resp.content.decode()

    def test_answer_endpoint_renders_reasoning(self):
        session = self._make_session(
            status=ResearchSession.Status.COMPLETED,
            reasoning="Looked at models.py\nFound the Dependant class",
            final_answer="## Answer",
        )
        resp = self.client.get(f"/api/research/{session.id}/answer/")
        body = resp.content.decode()
        assert "# Reasoning" in body
        assert "Looked at models.py" in body
        assert "# Answer" in body
        assert "## Answer" in body

    def test_answer_endpoint_404(self):
        resp = self.client.get("/api/research/99999/answer/")
        assert resp.status_code == 404

    def test_answer_no_reasoning(self):
        session = self._make_session(
            status=ResearchSession.Status.COMPLETED,
            final_answer="Just the answer.",
        )
        resp = self.client.get(f"/api/research/{session.id}/answer/")
        body = resp.content.decode()
        assert "# Answer" in body
        assert "# Reasoning" not in body

    def test_answer_empty_when_no_content(self):
        session = self._make_session()
        resp = self.client.get(f"/api/research/{session.id}/answer/")
        assert resp.content.decode() == ""

    def test_final_answer_is_array_of_lines(self):
        session = self._make_session(
            status=ResearchSession.Status.COMPLETED,
            final_answer="Line one\nLine two\n\nLine four",
        )
        resp = self.client.get(f"/api/research/{session.id}/")
        data = resp.json()
        assert data["final_answer"] == ["Line one", "Line two", "", "Line four"]

    def test_reasoning_is_array_of_lines(self):
        session = self._make_session(
            status=ResearchSession.Status.COMPLETED,
            reasoning="Step one\nStep two\n\nStep four",
        )
        resp = self.client.get(f"/api/research/{session.id}/")
        data = resp.json()
        assert data["reasoning"] == ["Step one", "Step two", "", "Step four"]

    def test_result_serializer_includes_reasoning(self):
        session = self._make_session(
            status=ResearchSession.Status.COMPLETED,
            reasoning="My reasoning",
            final_answer="My answer",
        )
        json_data = ResearchSessionResultSerializer(session).data
        assert json_data["reasoning"] == ["My reasoning"]
        assert json_data["final_answer"] == ["My answer"]
