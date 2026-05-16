from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .models import Repository, ResearchSession, ToolCall


class RepositorySerializer(serializers.ModelSerializer[Repository]):
    class Meta:  # type: ignore
        model = Repository
        fields = ["id", "url", "name", "owner", "file_count", "last_analyzed", "created_at"]


class ToolCallSerializer(serializers.ModelSerializer[ToolCall]):
    class Meta:  # type: ignore
        model = ToolCall
        fields = [
            "id", "tool_name", "tool_input", "tool_output_summary",
            "file_path", "sequence_number", "created_at",
        ]


class ResearchSessionListSerializer(serializers.ModelSerializer[ResearchSession]):
    repository_name = serializers.CharField(source="repository.name", read_only=True)
    tool_calls_count = serializers.IntegerField(source="tool_calls.count", read_only=True)

    class Meta:  # type: ignore
        model = ResearchSession
        fields = [
            "id", "repository_name", "question", "status",
            "tool_calls_count", "input_tokens", "output_tokens",
            "started_at", "completed_at",
        ]


def _split_lines(value: str | None) -> list[str]:
    """Split a text field into an array of lines — kills JSON \\n escaping."""
    if not value:
        return []
    return value.split("\n")


class ResearchSessionResultSerializer(serializers.ModelSerializer[ResearchSession]):
    repository = RepositorySerializer(read_only=True)
    final_answer = serializers.SerializerMethodField()
    reasoning = serializers.SerializerMethodField()

    class Meta:  # type: ignore
        model = ResearchSession
        fields = [
            "id", "repository", "question", "reasoning", "final_answer",
            "status", "error_message", "model_used", "input_tokens", "output_tokens",
            "started_at", "completed_at",
        ]

    def get_final_answer(self, obj: ResearchSession) -> list[str]:
        return _split_lines(obj.final_answer)

    def get_reasoning(self, obj: ResearchSession) -> list[str]:
        return _split_lines(obj.reasoning)


class ResearchSessionDetailSerializer(serializers.ModelSerializer[ResearchSession]):
    repository = RepositorySerializer(read_only=True)
    tool_calls = ToolCallSerializer(many=True, read_only=True)
    final_answer = serializers.SerializerMethodField()
    reasoning = serializers.SerializerMethodField()

    class Meta:  # type: ignore
        model = ResearchSession
        fields = [
            "id", "repository", "question", "reasoning", "final_answer",
            "status", "error_message", "model_used", "input_tokens", "output_tokens",
            "started_at", "completed_at", "tool_calls",
        ]

    def get_final_answer(self, obj: ResearchSession) -> list[str]:
        return _split_lines(obj.final_answer)

    def get_reasoning(self, obj: ResearchSession) -> list[str]:
        return _split_lines(obj.reasoning)


class StartResearchSerializer(serializers.Serializer[dict[str, Any]]):
    repo_url = serializers.CharField(required=True)
    question = serializers.CharField(required=True, min_length=10)
