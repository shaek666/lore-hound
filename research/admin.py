from django.contrib import admin

from .models import Repository, ResearchSession, ToolCall


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = ["name", "url", "file_count", "last_analyzed", "created_at"]
    search_fields = ["name", "url"]
    readonly_fields = ["created_at"]


@admin.register(ResearchSession)
class ResearchSessionAdmin(admin.ModelAdmin):
    list_display = ["repository", "short_question", "status", "started_at", "completed_at"]
    list_filter = ["status", "repository"]
    search_fields = ["question", "repository__name"]

    def short_question(self, obj):
        return obj.question[:80]

    short_question.short_description = "Question"


@admin.register(ToolCall)
class ToolCallAdmin(admin.ModelAdmin):
    list_display = ["session", "tool_name", "file_path", "sequence_number", "created_at"]
    list_filter = ["tool_name", "session__repository"]
