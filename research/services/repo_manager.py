import logging
import os
import re
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
import shutil
from typing import Any, Optional

from django.utils import timezone

from research.models import Repository

logger = logging.getLogger(__name__)

GIT_CLONE_TIMEOUT = 120
GIT_URL_PATTERN = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)([^/]+)/([^/.]+?)(?:\.git)?$"
)
LOCAL_PATH_PREFIXES = ("/", "./", "../", ".\\", "..\\") + tuple(f"{d}:\\" for d in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _normalize_git_url(url: str) -> str:
    """Normalize GitHub URLs: strip trailing .git, prefer https:// format."""
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    return url


def _rmtree_onerror(func, path, _excinfo):
    """Handle read-only files on Windows by setting write permission and retrying."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


class RepoAccess:
    def __init__(self, repo_path: Path):
        self._repo_path = repo_path.resolve()

    def _resolve(self, relative_path: str) -> Path:
        """Resolve a relative path within the repo, preventing path traversal."""
        base = relative_path.strip("/\\")
        if not base:
            return self._repo_path
        resolved = (self._repo_path / base).resolve()
        try:
            resolved.relative_to(self._repo_path)
        except ValueError:
            raise PermissionError(f"Path traversal blocked: {relative_path}")
        return resolved

    def list_files(self, relative_path: str = "/", pattern: Optional[str] = None) -> list[dict[str, Any]]:
        target = self._resolve(relative_path)
        if not target.exists():
            return [{"error": f"Path not found: {relative_path}"}]
        entries: list[dict[str, Any]] = []
        try:
            for entry in sorted(os.scandir(target), key=lambda e: e.name):
                if entry.name.startswith("."):
                    continue
                info: dict[str, Any] = {
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

    def read_file(self, relative_path: str, max_length: int = 10000, offset: int = 0) -> str:
        target = self._resolve(relative_path)
        try:
            st = target.stat()
        except OSError:
            return f"[Error accessing {relative_path}]"
        if not stat.S_ISREG(st.st_mode):
            return f"[File not found: {relative_path}]"
        if st.st_size > 1_000_000:
            return f"[File too large: {relative_path} ({st.st_size} bytes)]"
        if self._is_binary(target):
            return f"[Binary file: {relative_path}, {st.st_size} bytes]"
        try:
            if offset > 0:
                # Line-based seeking: skip to offset line, then read up to max_length chars
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    for _ in range(offset - 1):
                        f.readline()
                    text = f.read(max_length + 500)
                if len(text) > max_length:
                    text = text[:max_length] + f"\n... [truncated, {len(text) - max_length} more bytes]"
                return text
            # Full file read only for offset=0
            text = target.read_text(encoding="utf-8", errors="replace")
            if len(text) > max_length:
                text = text[:max_length] + f"\n... [truncated, {len(text) - max_length} more bytes]"
            return text
        except (OSError, UnicodeDecodeError) as e:
            return f"[Error reading {relative_path}: {e}]"

    def search_code(self, query: str, file_pattern: Optional[str] = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        # Try ripgrep first for speed, fall back to Python os.walk
        try:
            cmd = ["rg", "-n", "--no-heading", "--max-count", "50"]
            if file_pattern:
                cmd.extend(["-g", file_pattern])
            cmd.extend([query, str(self._repo_path)])
            rg_result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )
            if rg_result.returncode in (0, 1):  # 0=match, 1=no match
                for line in rg_result.stdout.splitlines():
                    parts = line.split(":", 2)
                    if len(parts) == 3:
                        rel_file = parts[0]
                        line_no = int(parts[1])
                        content = parts[2]
                        results.append({"file": rel_file, "line": line_no, "content": content})
                        if len(results) >= 50:
                            return results
                return results
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass  # Fall through to Python-based search

        # Fallback: Python os.walk
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

    def get_file_summary(self, relative_path: str) -> dict[str, Any]:
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

    def _summarize_python(self, text: str, summary: dict[str, Any]):
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

    @staticmethod
    def _is_local_path(path: str) -> bool:
        return path.startswith(LOCAL_PATH_PREFIXES) or Path(path).exists()

    def ensure_repo(self, repo_url: str) -> Repository:
        repo_url = _normalize_git_url(repo_url)
        if self._is_local_path(repo_url):
            local_path = Path(repo_url).resolve()
            if not local_path.is_dir():
                raise ValueError(f"Local path does not exist or is not a directory: {repo_url}")
            repo_identifier = str(local_path)
            owner = "local"
            repo_name = local_path.name

            existing = Repository.objects.filter(url=repo_identifier).first()
            if existing and existing.clone_path and Path(existing.clone_path).exists():
                logger.info("Repo already registered: %s", repo_identifier)
                return existing

            file_count = 0
            total_size = 0
            for f in local_path.rglob("*"):
                if f.is_file() and not f.name.startswith("."):
                    file_count += 1
                    try:
                        total_size += f.stat().st_size
                    except OSError:
                        pass

            if existing:
                existing.clone_path = repo_identifier
                existing.name = repo_name
                existing.file_count = file_count
                existing.total_size_bytes = total_size
                existing.last_analyzed = timezone.now()
                existing.save()
                return existing

            return Repository.objects.create(
                url=repo_identifier,
                name=repo_name,
                owner=owner,
                default_branch="main",
                clone_path=repo_identifier,
                file_count=file_count,
                total_size_bytes=total_size,
            )

        existing = Repository.objects.filter(url=repo_url).first()
        if existing and existing.clone_path and Path(existing.clone_path).exists():
            logger.info("Repo already cloned: %s", repo_url)
            return existing

        match = GIT_URL_PATTERN.match(repo_url)
        if not match:
            raise ValueError(
                f"Invalid input: {repo_url}. Provide a GitHub URL or a local filesystem path."
            )
        owner, repo_name = match.group(1), match.group(2)

        clone_dir = self.clone_base_dir / f"{owner}-{repo_name}"

        # Remove existing clone directory if present
        if clone_dir.exists():
            shutil.rmtree(clone_dir, onerror=_rmtree_onerror)

        logger.info("Cloning %s into %s", repo_url, clone_dir)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--single-branch", repo_url, str(clone_dir)],
                check=True, capture_output=True, text=True, timeout=GIT_CLONE_TIMEOUT,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Git clone failed: {e.stderr}") from e

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
            existing.last_analyzed = timezone.now()
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
            shutil.rmtree(repository.clone_path)
            repository.clone_path = None
            repository.save()
