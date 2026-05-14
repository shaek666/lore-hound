from rest_framework import serializers

from .models import Repository, ResearchSession, ToolCall


class RepositorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Repository
        fields = ["id", "url", "name", "owner", "file_count", "last_analyzed", "created_at"]


class ToolCallSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToolCall
        fields = [
            "id", "tool_name", "tool_input", "tool_output_summary",
            "file_path", "sequence_number", "created_at",
        ]


class ResearchSessionListSerializer(serializers.ModelSerializer):
    repository_name = serializers.CharField(source="repository.name", read_only=True)
    tool_calls_count = serializers.IntegerField(source="tool_calls.count", read_only=True)

    class Meta:
        model = ResearchSession
        fields = [
            "id", "repository_name", "question", "status",
            "tool_calls_count", "input_tokens", "output_tokens",
            "started_at", "completed_at",
        ]


class ResearchSessionDetailSerializer(serializers.ModelSerializer):
    repository = RepositorySerializer(read_only=True)
    tool_calls = ToolCallSerializer(many=True, read_only=True)

    class Meta:
        model = ResearchSession
        fields = "__all__"


class StartResearchSerializer(serializers.Serializer):
    repo_url = serializers.URLField(required=True)
    question = serializers.CharField(required=True, min_length=10)
