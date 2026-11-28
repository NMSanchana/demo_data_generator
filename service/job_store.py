"""
In-memory jobs for /generate.

A Generate call no longer holds the request open until every screen is done. It creates a Job here, runs it in the
background and returns the job id at once. The page then asks GET /generate/{job_id} every second or two.

A job holds:
  - screens : one entry per screen of the run (parents first), each with a status:
                waiting | generating | done | reused | failed
  - logs    : the readable run log (service/run_log.py), growing while the run works
  - results : the full result (fields and rows) of every screen that has finished, in the order they finished

Jobs live in backend memory only. If the backend restarts mid-run, that run is gone; whatever was already stored in the
profile database stays. Finished jobs are dropped after JOB_TTL_SECONDS and at most MAX_JOBS are kept.
"""

import asyncio
import time
import uuid
from typing import Any

from service.run_log import RunLog

MAX_JOBS = 50
JOB_TTL_SECONDS = 6 * 3600

SCREEN_STATUSES = ("waiting", "generating", "done", "reused", "failed")
FINISHED_STATUSES = ("done", "reused", "failed")


class Job:
    def __init__(self, profile_id: str, profile_name: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.profile_id = profile_id
        self.profile_name = profile_name
        self.created = time.monotonic()
        self.finished_at: float | None = None
        self.status = "preparing"  # preparing | running | completed | failed
        self.error: str | None = None
        self.log = RunLog()
        self.screens: list[dict] = []
        self.results: list[dict] = []
        self.storage: dict | None = None
        self.task: asyncio.Task | None = None  # keeps a reference so the background task is not garbage collected

    # ---- screens ----------------------------------------------------------

    def add_screen(self, *, screen: str, status: str = "waiting", **extra: Any) -> int:
        """Add a screen to the run and return its key (its position in the list)."""
        key = len(self.screens)
        self.screens.append({
            "key": key, "screen": screen, "status": status,
            "screen_id": None, "feature_name": None, "row_count": 0, "message": None,
            "auto_added": False, "auto_added_for": [], "reused_from_version": None,
            "result_index": None, "seconds": None,
            **extra,
        })
        return key

    def update_screen(self, key: int, **fields: Any) -> None:
        self.screens[key].update(fields)

    def add_result(self, key: int, result: dict, **screen_fields: Any) -> None:
        """Store the full result of a finished screen and update that screen's status line."""
        self.results.append(result)
        self.screens[key].update(result_index=len(self.results) - 1, **screen_fields)

    # ---- run ----------------------------------------------------------------

    def set_phase(self, status: str) -> None:
        self.status = status

    def complete(self, storage: dict | None) -> None:
        self.storage = storage
        self.status = "completed"
        self.finished_at = time.monotonic()

    def fail(self, message: str) -> None:
        self.error = message
        self.status = "failed"
        self.finished_at = time.monotonic()

    @property
    def finished(self) -> bool:
        return self.status in ("completed", "failed")

    def snapshot(self, log_from: int = 0, result_from: int = 0) -> dict:
        """What the progress endpoint returns. Logs and results are sent only from the given positions on,
        so each poll carries just what is new."""
        log_from = max(0, log_from)
        result_from = max(0, result_from)
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        entries = self.log.entries
        return {
            "job_id": self.id,
            "profile_id": self.profile_id,
            "status": self.status,
            "error": self.error,
            "elapsed": round(end - self.created, 1),
            "screens": [dict(s) for s in self.screens],
            "done": sum(1 for s in self.screens if s["status"] in FINISHED_STATUSES),
            "total": len(self.screens),
            "logs": entries[log_from:],
            "log_next": len(entries),
            "results": self.results[result_from:],
            "result_next": len(self.results),
            "storage": self.storage,
        }


_jobs: dict[str, Job] = {}


def _prune() -> None:
    now = time.monotonic()
    for jid in [j.id for j in _jobs.values() if j.finished and j.finished_at is not None and now - j.finished_at > JOB_TTL_SECONDS]:
        _jobs.pop(jid, None)
    if len(_jobs) > MAX_JOBS:
        # drop the oldest finished jobs first; never drop one that is still running
        for j in sorted((j for j in _jobs.values() if j.finished), key=lambda j: j.created):
            if len(_jobs) <= MAX_JOBS:
                break
            _jobs.pop(j.id, None)


def create_job(profile_id: str, profile_name: str) -> Job:
    _prune()
    job = Job(profile_id, profile_name)
    _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)