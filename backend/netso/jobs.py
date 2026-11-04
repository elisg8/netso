"""Background jobs (downloads, image conversion, ISO import, checksums) with progress for the UI."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable


@dataclass
class Job:
    id: str
    kind: str
    label: str
    status: str = "running"   # running | done | error
    progress: float | None = None  # 0..1, None = indeterminate
    message: str = ""
    created: float = field(default_factory=time.time)
    finished: float | None = None
    result: dict | None = None  # structured outcome for the UI (e.g. per-node results)

    def update(self, progress: float | None = None, message: str | None = None) -> None:
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if message is not None:
            self.message = message


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def start(kind: str, label: str, fn: Callable[[Job], str | None], background: bool = True) -> Job:
    job = Job(id=uuid.uuid4().hex[:10], kind=kind, label=label)
    with _lock:
        _jobs[job.id] = job

    def run():
        try:
            msg = fn(job)
            job.status, job.progress = "done", 1.0
            if msg:
                job.message = msg
        except Exception as exc:  # shown to the user as is
            job.status, job.message = "error", str(exc)
        job.finished = time.time()

    if background:
        threading.Thread(target=run, daemon=True).start()
    else:
        run()
    return job


def list_jobs() -> list[dict]:
    now = time.time()
    with _lock:  # forget finished jobs after an hour
        for k in [k for k, j in _jobs.items() if j.finished and now - j.finished > 3600]:
            _jobs.pop(k)
        return [asdict(j) for j in sorted(_jobs.values(), key=lambda j: j.created, reverse=True)]


def dismiss(job_id: str) -> None:
    with _lock:
        j = _jobs.get(job_id)
        if j and j.status != "running":
            _jobs.pop(job_id)
