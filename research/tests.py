from django.test import TestCase
from rest_framework import status

from research.models import Repository, ResearchSession, ToolCall


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
            final_answer="Through solve_dependencies()",
        )
        assert session.status == "completed"
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


class SerializerTests(TestCase):
    def test_start_research_serializer_validates(self):
        from research.serializers import StartResearchSerializer

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
