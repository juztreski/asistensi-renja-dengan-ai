"""Penyimpanan sementara pekerjaan asistensi.

Setiap unggahan dibuatkan ``job`` dengan ID acak. Data disimpan di memori
proses dan dibersihkan setelah TTL. Untuk deployment multi-worker, gunakan
penyimpanan bersama (mis. Redis) atau jalankan satu worker dengan beberapa
thread; lihat README.
"""

from __future__ import annotations

import copy
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable


class JobStore:
    """Thread-safe in-memory job store dengan masa berlaku."""

    def __init__(
        self,
        ttl_seconds: int = 6 * 60 * 60,
        max_jobs: int = 200,
        workdir_validator: Callable[[Path], bool] | None = None,
    ):
        self.ttl_seconds = ttl_seconds
        self.max_jobs = max_jobs
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._workdir_validator = workdir_validator

    def create(self, payload: dict[str, Any] | None = None, workdir: str | Path | None = None) -> str:
        job_id = secrets.token_urlsafe(18)
        now = time.time()
        job = dict(payload or {})
        job.setdefault("workdir", str(workdir) if workdir else "")
        job["_created_at"] = now
        job["_updated_at"] = now
        with self._lock:
            self._cleanup_locked(now)
            self._jobs[job_id] = job
            self._evict_locked()
        return job_id

    def get(self, job_id: str, default: Any = None) -> Any:
        with self._lock:
            self._cleanup_locked(time.time())
            job = self._jobs.get(job_id)
            if job is None:
                return default
            self._jobs[job_id]["_updated_at"] = time.time()
            return copy.deepcopy(job)

    def set(self, job_id: str, payload: dict[str, Any]) -> bool:
        with self._lock:
            if job_id not in self._jobs:
                return False
            created = self._jobs[job_id].get("_created_at", time.time())
            job = dict(payload)
            job["_created_at"] = created
            job["_updated_at"] = time.time()
            self._jobs[job_id] = job
            return True

    def update(self, job_id: str, **fields: Any) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            job.update(fields)
            job["_updated_at"] = time.time()
            return True

    def mutate(self, job_id: str, func: Callable[[dict[str, Any]], Any]) -> Any:
        """Ubah job secara atomik dan kembalikan nilai dari ``func``."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            result = func(job)
            job["_updated_at"] = time.time()
            return result

    def delete(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.pop(job_id, None)
        if job:
            self._remove_workdir(job)
        return job

    def exists(self, job_id: str) -> bool:
        with self._lock:
            self._cleanup_locked(time.time())
            return job_id in self._jobs

    def age_seconds(self, job_id: str) -> float | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return time.time() - job.get("_created_at", time.time())

    def stats(self) -> dict[str, int]:
        with self._lock:
            self._cleanup_locked(time.time())
            return {"jobs": len(self._jobs), "ttl_seconds": self.ttl_seconds, "max_jobs": self.max_jobs}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _cleanup_locked(self, now: float) -> None:
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if now - job.get("_created_at", now) > self.ttl_seconds
        ]
        for job_id in expired:
            job = self._jobs.pop(job_id, None)
            if job:
                self._remove_workdir(job)

    def _evict_locked(self) -> None:
        while len(self._jobs) > self.max_jobs:
            oldest = min(self._jobs, key=lambda key: self._jobs[key].get("_created_at", 0))
            job = self._jobs.pop(oldest, None)
            if job:
                self._remove_workdir(job)

    def _remove_workdir(self, job: dict[str, Any]) -> None:
        workdir = job.get("workdir")
        if not workdir:
            return
        path = Path(workdir)
        try:
            if self._workdir_validator and not self._workdir_validator(path):
                return
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass

