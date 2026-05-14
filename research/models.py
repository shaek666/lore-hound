from django.db import models


class Repository(models.Model):
    url = models.URLField(unique=True, max_length=2000)
    name = models.CharField(max_length=255)
    owner = models.CharField(max_length=255, blank=True, default="")
    default_branch = models.CharField(max_length=255, default="main")
    clone_path = models.CharField(max_length=1000, null=True, blank=True)
    file_count = models.IntegerField(null=True, blank=True)
    total_size_bytes = models.BigIntegerField(null=True, blank=True)
    last_analyzed = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "repositories"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class ResearchSession(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    repository = models.ForeignKey(
        Repository, on_delete=models.CASCADE, related_name="sessions"
    )
    question = models.TextField()
    final_answer = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    model_used = models.CharField(max_length=100, null=True, blank=True)
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.repository.name}: {self.question[:60]}"


class ToolCall(models.Model):
    session = models.ForeignKey(
        ResearchSession, on_delete=models.CASCADE, related_name="tool_calls"
    )
    tool_name = models.CharField(max_length=100)
    tool_input = models.JSONField(default=dict)
    tool_output_summary = models.TextField(null=True, blank=True)
    file_path = models.CharField(max_length=2000, null=True, blank=True)
    token_count = models.IntegerField(null=True, blank=True)
    sequence_number = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["session", "sequence_number"]

    def __str__(self):
        return f"[{self.sequence_number}] {self.tool_name}"
