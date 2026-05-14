import logging

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from research.models import ResearchSession
from research.serializers import (
    ResearchSessionDetailSerializer,
    ResearchSessionListSerializer,
    StartResearchSerializer,
)
from research.services.agent import ResearchAgent
from research.services.llm_client import LLMClient
from research.services.repo_manager import RepoManager

logger = logging.getLogger(__name__)


def _build_agent():
    llm = LLMClient(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        model=settings.LLM_MODEL,
    )
    rm = RepoManager(clone_base_dir=settings.REPOS_CLONE_DIR)
    return ResearchAgent(llm_client=llm, repo_manager=rm)


@api_view(["POST", "GET"])
def research_list_create(request):
    if request.method == "POST":
        return start_research(request)
    return list_sessions(request)


def start_research(request):
    serializer = StartResearchSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    repo_url = serializer.validated_data["repo_url"]
    question = serializer.validated_data["question"]

    agent = _build_agent()

    try:
        repository = agent.repo_manager.ensure_repo(repo_url)
    except (ValueError, RuntimeError) as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    session = ResearchSession.objects.create(
        repository=repository,
        question=question,
    )

    try:
        session = agent.run(session)
    except Exception as e:
        logger.exception("Agent run failed")
        session.status = ResearchSession.Status.FAILED
        session.error_message = f"Agent run failed: {e}"
        session.save()

    result_serializer = ResearchSessionDetailSerializer(session)
    return Response(result_serializer.data, status=status.HTTP_201_CREATED)


def list_sessions(request):
    repo_url = request.query_params.get("repo_url")
    sessions = ResearchSession.objects.select_related("repository").all()
    if repo_url:
        sessions = sessions.filter(repository__url=repo_url)
    sessions = sessions.order_by("-started_at")[:50]
    serializer = ResearchSessionListSerializer(sessions, many=True)
    return Response(serializer.data)


@api_view(["GET"])
def get_session(request, session_id):
    session = get_object_or_404(
        ResearchSession.objects.select_related("repository").prefetch_related("tool_calls"),
        id=session_id,
    )
    serializer = ResearchSessionDetailSerializer(session)
    return Response(serializer.data)
