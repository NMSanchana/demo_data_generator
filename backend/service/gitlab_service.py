"""
GitLab service for the demo data generator.

Reads the product's Angular source repo — fetching the file tree and
individual component files that the architecture agent uses to extract
screen fields. Write operations from the old QC project are removed;
this service is read-only.
"""

import base64
import logging
import os
import time

import gitlab
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

TREE_CACHE_SECONDS = 45
TREE_FETCH_RETRIES = 3
TREE_FETCH_BACKOFF_SECONDS = 2  # doubles each retry: 2s → 4s


class GitLabService:
    """
    Read-only wrapper around the product's Angular source repo.

    Configured via SOURCE_GITLAB_* env vars (URL, TOKEN, PROJECT_ID, BRANCH).
    The tree is cached in memory for TREE_CACHE_SECONDS to avoid hammering
    GitLab on every generate call.
    """

    def __init__(self, env_prefix: str = "SOURCE_GITLAB"):
        self._env_prefix = env_prefix

        url        = os.getenv(f"{env_prefix}_URL", "https://gitlab.com")
        token      = os.getenv(f"{env_prefix}_TOKEN")
        project_id = os.getenv(f"{env_prefix}_PROJECT_ID")
        self._branch = os.getenv(f"{env_prefix}_BRANCH", "main")
        self._token  = token

        if not token or not project_id:
            raise RuntimeError(
                f"{env_prefix}_TOKEN and {env_prefix}_PROJECT_ID must be set "
                "in .env (see .env.example)."
            )

        self._gl      = gitlab.Gitlab(url, private_token=token)
        self._project = self._gl.projects.get(project_id)

        self._tree_cache: list[str] | None = None
        self._tree_cache_at: float = 0.0

    @property
    def branch(self) -> str:
        return self._branch

    def _fetch_tree_with_retry(self) -> list[dict]:
        """
        repository_tree(recursive=True, all=True) walks pages under the hood.
        For large repos (7000+ files) any single page can hiccup with a
        GitlabGetError even when the token/branch are fine. This retry
        absorbs that instead of failing the whole request.
        """
        last_error: Exception | None = None
        for attempt in range(1, TREE_FETCH_RETRIES + 1):
            try:
                return self._project.repository_tree(
                    recursive=True, all=True, ref=self._branch
                )
            except gitlab.exceptions.GitlabGetError as e:
                last_error = e
                if attempt < TREE_FETCH_RETRIES:
                    wait = TREE_FETCH_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    logger.warning(
                        "%s: repository_tree attempt %d/%d failed (%s) — retrying in %ss",
                        self._env_prefix, attempt, TREE_FETCH_RETRIES, e, wait,
                    )
                    time.sleep(wait)
        raise last_error

    def get_repo_tree(self, force_refresh: bool = False) -> list[str]:
        """Return all blob paths in the repo (cached)."""
        now = time.time()
        if (
            not force_refresh
            and self._tree_cache is not None
            and (now - self._tree_cache_at) < TREE_CACHE_SECONDS
        ):
            return self._tree_cache

        items = self._fetch_tree_with_retry()
        paths = [item["path"] for item in items if item["type"] == "blob"]

        self._tree_cache = paths
        self._tree_cache_at = now
        logger.info("%s: tree fetched — %d blob paths cached", self._env_prefix, len(paths))
        return paths

    def fetch_file(self, path: str) -> str | None:
        """Fetch a single file's content by path. Returns None if not found."""
        try:
            f = self._project.files.get(file_path=path, ref=self._branch)
        except gitlab.exceptions.GitlabGetError as e:
            if e.response_code == 404:
                return None
            raise
        return base64.b64decode(f.content).decode("utf-8")


# -------------------------------------------------------------------
# Singleton accessor — one service instance for the lifetime of the app
# -------------------------------------------------------------------

_service: GitLabService | None = None


def get_gitlab_service() -> GitLabService:
    global _service
    if _service is None:
        _service = GitLabService(env_prefix="SOURCE_GITLAB")
    return _service
