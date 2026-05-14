from django.core.management.base import BaseCommand
from django.utils import timezone

from research.models import Repository, ResearchSession, ToolCall


class Command(BaseCommand):
    help = "Seed the database with sample research data for demo purposes"

    def handle(self, *args, **options):
        self.stdout.write("Seeding database...")

        repo, _ = Repository.objects.get_or_create(
            url="https://github.com/tiangolo/fastapi",
            defaults={
                "name": "fastapi",
                "owner": "tiangolo",
                "file_count": 150,
                "total_size_bytes": 2_500_000,
            },
        )

        session1 = ResearchSession.objects.create(
            repository=repo,
            question="How does FastAPI handle dependency injection internally?",
            status=ResearchSession.Status.COMPLETED,
            model_used="Qwen/Qwen2.5-72B-Instruct",
            input_tokens=4500,
            output_tokens=1200,
            final_answer=(
                "FastAPI handles dependency injection through its `solve_dependencies` function "
                "in `fastapi/dependencies/utils.py`. When a path operation function declares "
                "parameters with type hints that are not path/query parameters, FastAPI treats "
                "them as dependencies. The `get_dependant()` function in the same file extracts "
                "dependency information from the function signature, building a dependency graph. "
                "FastAPI then calls `solve_dependencies()` which recursively resolves each "
                "dependency, handling sub-dependencies, caching (`use_cache`), and error states. "
                "The actual dependency values are resolved at request time in "
                "`routing.py:get_request_handler()`."
            ),
            completed_at=timezone.now(),
        )
        session1.started_at = timezone.now()
        session1.save(update_fields=["started_at"])

        ToolCall.objects.bulk_create([
            ToolCall(
                session=session1,
                tool_name="list_files",
                tool_input={"path": "/"},
                tool_output_summary="Root directory listing of fastapi repo",
                sequence_number=1,
            ),
            ToolCall(
                session=session1,
                tool_name="get_file_summary",
                tool_input={"path": "/fastapi/dependencies/utils.py"},
                tool_output_summary="File with solve_dependencies, get_dependant functions",
                file_path="/fastapi/dependencies/utils.py",
                sequence_number=2,
            ),
            ToolCall(
                session=session1,
                tool_name="read_file",
                tool_input={"path": "/fastapi/dependencies/utils.py", "max_length": 10000},
                tool_output_summary="Read 10000 chars of the DI implementation",
                file_path="/fastapi/dependencies/utils.py",
                sequence_number=3,
            ),
            ToolCall(
                session=session1,
                tool_name="search_code",
                tool_input={"query": "solve_dependencies", "file_pattern": "*.py"},
                tool_output_summary="Found solve_dependencies in utils.py and routing.py",
                sequence_number=4,
            ),
            ToolCall(
                session=session1,
                tool_name="save_finding",
                tool_input={
                    "file_path": "/fastapi/dependencies/utils.py",
                    "note": "Core DI logic in solve_dependencies() - handles sub-dependencies, caching, and error states",
                },
                tool_output_summary="Finding saved",
                file_path="/fastapi/dependencies/utils.py",
                sequence_number=5,
            ),
        ])

        session2 = ResearchSession.objects.create(
            repository=repo,
            question="How does FastAPI generate OpenAPI schemas?",
            status=ResearchSession.Status.COMPLETED,
            model_used="Qwen/Qwen2.5-72B-Instruct",
            input_tokens=3200,
            output_tokens=900,
            final_answer=(
                "FastAPI generates OpenAPI schemas through the `openapi.py` module. "
                "The `get_openapi()` function in `fastapi/openapi/utils.py` constructs "
                "the OpenAPI schema by iterating over all registered routes and extracting "
                "their path, methods, parameters, and response models. It uses `get_openapi_path()` "
                "to process individual routes and Pydantic's `schema_json()` to generate JSON Schema "
                "for request/response models."
            ),
            completed_at=timezone.now(),
        )
        session2.started_at = timezone.now()
        session2.save(update_fields=["started_at"])

        ToolCall.objects.create(
            session=session2,
            tool_name="list_files",
            tool_input={"path": "/fastapi/openapi"},
            tool_output_summary="OpenAPI module directory listing",
            sequence_number=1,
        )

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {Repository.objects.count()} repo(s), "
            f"{ResearchSession.objects.count()} session(s), "
            f"{ToolCall.objects.count()} tool call(s)"
        ))
