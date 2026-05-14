import logging
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from research.models import Repository

logger = logging.getLogger(__name__)

GIT_CLONE_TIMEOUT = 120
GIT_URL_PATTERN = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)([^/]+)/([^/.]+)"
)


class RepoAccess:
    def __init__(self, repo_path: Path):
        self._repo_path = repo_path.resolve()

    def _resolve(self, relative_path: str) -> Path:
        resolved = (self._repo_path / relative_path).resolve()
        if not str(resolved).startswith(str(self._repo_path)):
            raise PermissionError(f"Path traversal blocked: {relative_path}")
        return resolved

    def list_files(self, relative_path: str = "/", pattern: str = None) -> list[dict]:
        target = self._resolve(relative_path)
        if not target.exists():
            return [{"error": f"Path not found: {relative_path}"}]
        entries = []
        try:
            for entry in sorted(os.scandir(target), key=lambda e: e.name):
                if entry.name.startswith("."):
                    continue
                info = {
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                }
                if entry.is_file():
                    try:
                        stat = entry.stat()
                        info["size"] = stat.st_size
                        info["extension"] = Path(entry.name).suffix
                    except OSError:
                        info["size"] = 0
                        info["extension"] = ""
                entries.append(info)
                if len(entries) >= 200:
                    entries.append({"warning": "Truncated at 200 entries"})
                    break
            if pattern:
                entries = [e for e in entries if isinstance(e, dict) and
                           (pattern in e.get("name", "") or
                            e.get("extension", "") == pattern.replace("*", ""))]
        except PermissionError:
            return [{"error": f"Permission denied: {relative_path}"}]
        return entries

    def read_file(self, relative_path: str, max_length: int = 10000) -> str:
        target = self._resolve(relative_path)
        if not target.is_file():
            return f"[File not found: {relative_path}]"
        if target.stat().st_size > 1_000_000:
            return f"[File too large: {relative_path} ({target.stat().st_size} bytes)]"
        if self._is_binary(target):
            return f"[Binary file: {relative_path}, {target.stat().st_size} bytes]"
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
            if len(text) > max_length:
                text = text[:max_length] + f"\n... [truncated, {len(text) - max_length} more bytes]"
            return text
        except (OSError, UnicodeDecodeError) as e:
            return f"[Error reading {relative_path}: {e}]"

    def search_code(self, query: str, file_pattern: str = None) -> list[dict]:
        results = []
        skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "data"}
        for root, dirs, files in os.walk(self._repo_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                if file_pattern and not Path(fname).match(file_pattern):
                    continue
                fpath = Path(root) / fname
                rel = str(fpath.relative_to(self._repo_path))
                if self._is_binary(fpath):
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                        for line_no, line in enumerate(fh, 1):
                            if query in line:
                                results.append({
                                    "file": rel,
                                    "line": line_no,
                                    "content": line.rstrip("\n"),
                                })
                                if len(results) >= 50:
                                    return results
                except (OSError, UnicodeDecodeError):
                    continue
        return results

    def get_file_summary(self, relative_path: str) -> dict:
        target = self._resolve(relative_path)
        if not target.is_file():
            return {"error": f"File not found: {relative_path}"}
        if self._is_binary(target):
            return {
                "path": relative_path,
                "size": target.stat().st_size,
                "lines": 0,
                "extension": target.suffix,
                "binary": True,
            }
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            summary = {
                "path": relative_path,
                "size": target.stat().st_size,
                "lines": len(lines),
                "extension": target.suffix,
                "imports": [],
                "functions": [],
                "classes": [],
            }
            if target.suffix == ".py":
                self._summarize_python(text, summary)
            return summary
        except (OSError, UnicodeDecodeError) as e:
            return {"error": f"Error reading {relative_path}: {e}"}

    def _is_binary(self, path: Path) -> bool:
        try:
            with open(path, "rb") as f:
                chunk = f.read(512)
            return b"\0" in chunk
        except OSError:
            return True

    def _summarize_python(self, text: str, summary: dict):
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                summary["imports"].append(stripped)
            if stripped.startswith("def ") and stripped.endswith(":"):
                summary["functions"].append(stripped[4:-1].split("(")[0].strip())
            if stripped.startswith("class ") and stripped.endswith(":"):
                summary["classes"].append(stripped[6:-1].split("(")[0].strip())


class RepoManager:
    def __init__(self, clone_base_dir: str):
        self.clone_base_dir = Path(clone_base_dir)
        self.clone_base_dir.mkdir(parents=True, exist_ok=True)

    def ensure_repo(self, repo_url: str) -> Repository:
        existing = Repository.objects.filter(url=repo_url).first()
        if existing and existing.clone_path and Path(existing.clone_path).exists():
            logger.info("Repo already cloned: %s", repo_url)
            return existing

        match = GIT_URL_PATTERN.match(repo_url)
        if not match:
            raise ValueError(f"Invalid GitHub URL: {repo_url}")
        owner, repo_name = match.group(1), match.group(2)

        clone_dir = self.clone_base_dir / f"{owner}-{repo_name}"
        if clone_dir.exists():
            import shutil
            shutil.rmtree(clone_dir)

        logger.info("Cloning %s into %s", repo_url, clone_dir)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--single-branch", repo_url, str(clone_dir)],
                check=True,
                capture_output=True,
                text=True,
                timeout=GIT_CLONE_TIMEOUT,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Git clone failed: {e.stderr}") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"Git clone timed out after {GIT_CLONE_TIMEOUT}s") from e

        file_count = 0
        total_size = 0
        for f in clone_dir.rglob("*"):
            if f.is_file() and not f.name.startswith("."):
                file_count += 1
                try:
                    total_size += f.stat().st_size
                except OSError:
                    pass

        result_branch = subprocess.run(
            ["git", "-C", str(clone_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        default_branch = result_branch.stdout.strip() or "main"

        if existing:
            existing.clone_path = str(clone_dir)
            existing.file_count = file_count
            existing.total_size_bytes = total_size
            existing.owner = owner
            existing.default_branch = default_branch
            existing.last_analyzed = time.time()
            existing.save()
            return existing

        return Repository.objects.create(
            url=repo_url,
            name=repo_name,
            owner=owner,
            default_branch=default_branch,
            clone_path=str(clone_dir),
            file_count=file_count,
            total_size_bytes=total_size,
        )

    def get_repo_path(self, repository: Repository) -> Path:
        if not repository.clone_path:
            raise ValueError("Repository has no clone path")
        return Path(repository.clone_path)

    @contextmanager
    def access(self, repository: Repository):
        path = self.get_repo_path(repository)
        yield RepoAccess(path)

    def cleanup_repo(self, repository: Repository):
        if repository.clone_path and Path(repository.clone_path).exists():
            import shutil
            shutil.rmtree(repository.clone_path)
            repository.clone_path = None
            repository.save()
