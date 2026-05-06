from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO

from dev_orchestrator.config import load_config
from dev_orchestrator.v2.service import V2Orchestrator


DEFAULT_WORKER_ROLES = ("chief", "planner", "architect", "frontend", "backend", "qa", "security", "integration", "release")
WORKER_STATE_SCHEMA_VERSION = "1.0.0"


@dataclass
class WorkerLoopResult:
    worker_id: str
    role: str
    processed: int
    stopped: bool

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "role": self.role,
            "processed": self.processed,
            "stopped": self.stopped,
        }


class DurableWorker:
    def __init__(
        self,
        *,
        service: V2Orchestrator,
        role: str,
        tenant_id: str,
        worker_id: str = "",
        poll_seconds: float = 2.0,
        lease_seconds: int = 300,
        heartbeat_seconds: int = 30,
    ) -> None:
        self.service = service
        self.role = (role or "planner").strip().lower()
        self.tenant_id = tenant_id
        self.worker_id = worker_id or f"{self.role}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.poll_seconds = max(0.1, float(poll_seconds))
        self.lease_seconds = max(1, int(lease_seconds))
        self.heartbeat_seconds = max(1, int(heartbeat_seconds))
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run_once(self) -> dict:
        self._reload_service_config()
        claimed = self.service.claim_durable_job(
            tenant_id=self.tenant_id,
            role=self.role,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        job = claimed.get("job")
        if not job:
            return {"ok": True, "claimed": False, "worker_id": self.worker_id, "expired": claimed.get("expired", [])}

        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat_loop, args=(job["id"], heartbeat_stop), daemon=True)
        heartbeat.start()
        try:
            result = self.service.execute_durable_job(job, worker_id=self.worker_id, lease_seconds=self.lease_seconds)
            return {**result, "claimed": True, "worker_id": self.worker_id, "expired": claimed.get("expired", [])}
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=2)

    def _reload_service_config(self) -> None:
        root_dir = self.service.config.root_dir
        self.service = V2Orchestrator(config=load_config(root_dir))

    def run_forever(self, max_jobs: int | None = None) -> WorkerLoopResult:
        processed = 0
        while not self._stop.is_set():
            result = self.run_once()
            if result.get("claimed"):
                processed += 1
                if max_jobs is not None and processed >= max_jobs:
                    break
                continue
            if max_jobs is not None and processed >= max_jobs:
                break
            self._stop.wait(self.poll_seconds)
        return WorkerLoopResult(worker_id=self.worker_id, role=self.role, processed=processed, stopped=self._stop.is_set())

    def _heartbeat_loop(self, job_id: str, stop: threading.Event) -> None:
        while not stop.wait(self.heartbeat_seconds):
            try:
                lease_until = (datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)).isoformat()
                job = self.service.storage.heartbeat_durable_job(job_id, self.worker_id, lease_until=lease_until)
                if job.get("run_id"):
                    self.service._refresh_run_durable_queue_state(job["run_id"])
            except Exception:
                return


@dataclass
class WorkerProcess:
    role: str
    index: int
    process: subprocess.Popen
    stdout: IO[bytes]
    stderr: IO[bytes]

    def close_logs(self) -> None:
        self.stdout.close()
        self.stderr.close()


class LocalWorkerSupervisor:
    def __init__(
        self,
        *,
        root_dir: Path,
        roles: list[str],
        tenant_id: str,
        poll_seconds: float = 2.0,
        lease_seconds: int = 300,
        heartbeat_seconds: int = 30,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.roles = roles or list(DEFAULT_WORKER_ROLES)
        self.tenant_id = tenant_id
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.processes: list[WorkerProcess] = []
        self._stop = False

    def start(self) -> list[dict]:
        if self.processes:
            return [self._process_summary(item) for item in self.processes]
        for index, role in enumerate(self.roles, start=1):
            self.processes.append(self._spawn(role, index))
        return [self._process_summary(item) for item in self.processes]

    def run_forever(self) -> None:
        self.start()
        try:
            while not self._stop:
                for index, item in enumerate(list(self.processes)):
                    code = item.process.poll()
                    if code is None:
                        continue
                    item.close_logs()
                    replacement = self._spawn(item.role, item.index)
                    self.processes[index] = replacement
                time.sleep(2)
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop = True
        for item in self.processes:
            if item.process.poll() is None:
                item.process.terminate()
        for item in self.processes:
            try:
                item.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                item.process.kill()
            item.close_logs()
        self.processes = []

    def _spawn(self, role: str, index: int) -> WorkerProcess:
        logs_dir = self.root_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = logs_dir / f"worker-{role}-{index}.stdout.log"
        stderr_path = logs_dir / f"worker-{role}-{index}.stderr.log"
        stdout = stdout_path.open("ab")
        stderr = stderr_path.open("ab")
        args = [
            sys.executable,
            str(self.root_dir / "run_server.py"),
            "--worker",
            "--role",
            role,
            "--tenant",
            self.tenant_id,
            "--poll-seconds",
            str(self.poll_seconds),
            "--lease-seconds",
            str(self.lease_seconds),
            "--heartbeat-seconds",
            str(self.heartbeat_seconds),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(args, cwd=str(self.root_dir), stdout=stdout, stderr=stderr, creationflags=creationflags)
        return WorkerProcess(role=role, index=index, process=process, stdout=stdout, stderr=stderr)

    def _process_summary(self, item: WorkerProcess) -> dict:
        status = "running" if item.process.poll() is None else f"exited:{item.process.returncode}"
        return {
            "role": item.role,
            "index": item.index,
            "pid": item.process.pid,
            "status": status,
            "worker_id_hint": f"{item.role}-{item.process.pid}",
        }


def _parse_worker_pid(worker_id: str) -> int | None:
    parts = (worker_id or "").split("-")
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _tail_text(path: Path, limit: int = 4000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit), os.SEEK_SET)
            return handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return ""


def describe_worker_runtime(root_dir: Path, durable_jobs: list[dict], worker_jobs: list[dict], roles: list[str] | None = None) -> dict:
    """Build a DB-backed worker/supervisor status view for the hosted API."""
    root = Path(root_dir)
    logs_dir = root / "logs"
    configured_roles = roles or list(DEFAULT_WORKER_ROLES)
    queue_counts: dict[str, int] = {}
    role_counts: dict[str, dict[str, int]] = {}
    observed: dict[str, dict] = {}

    for job in durable_jobs:
        status = job.get("status", "")
        role = job.get("role", "")
        queue_counts[status] = queue_counts.get(status, 0) + 1
        role_bucket = role_counts.setdefault(role, {})
        role_bucket[status] = role_bucket.get(status, 0) + 1
        worker_id = job.get("worker_id", "")
        if not worker_id:
            continue
        item = observed.setdefault(
            worker_id,
            {
                "worker_id": worker_id,
                "pid": _parse_worker_pid(worker_id),
                "role": role,
                "processed_job_count": 0,
                "recent_job_count": 0,
                "last_heartbeat": "",
                "last_error": "",
                "states": {},
            },
        )
        item["role"] = item.get("role") or role
        item["recent_job_count"] += 1
        item["states"][status] = item["states"].get(status, 0) + 1
        if status in {"completed", "failed", "dead_letter"}:
            item["processed_job_count"] += 1
        heartbeat = job.get("heartbeat_at") or job.get("updated_at") or ""
        if heartbeat > item.get("last_heartbeat", ""):
            item["last_heartbeat"] = heartbeat
        if job.get("error"):
            item["last_error"] = job["error"]

    for job in worker_jobs:
        worker_id = job.get("worker_id", "")
        if not worker_id:
            continue
        item = observed.setdefault(
            worker_id,
            {
                "worker_id": worker_id,
                "pid": _parse_worker_pid(worker_id),
                "role": "",
                "processed_job_count": 0,
                "recent_job_count": 0,
                "last_heartbeat": "",
                "last_error": "",
                "states": {},
            },
        )
        if job.get("status") == "finished":
            item["processed_job_count"] += 1
        heartbeat = job.get("locked_at") or job.get("updated_at") or ""
        if heartbeat > item.get("last_heartbeat", ""):
            item["last_heartbeat"] = heartbeat

    log_processes: list[dict] = []
    if logs_dir.exists():
        for stdout_path in sorted(logs_dir.glob("worker-*.stdout.log")):
            stem = stdout_path.name[: -len(".stdout.log")]
            stderr_path = logs_dir / f"{stem}.stderr.log"
            parts = stem.split("-")
            role = "-".join(parts[1:-1]) if len(parts) >= 3 else stem.replace("worker-", "")
            try:
                stat = stdout_path.stat()
            except OSError:
                continue
            log_processes.append(
                {
                    "role": role,
                    "stdout_path": str(stdout_path),
                    "stderr_path": str(stderr_path),
                    "stdout_size_bytes": stat.st_size,
                    "stderr_size_bytes": stderr_path.stat().st_size if stderr_path.exists() else 0,
                    "last_log_at": stat.st_mtime,
                    "last_error": _tail_text(stderr_path, 2000).strip()[-1000:] if stderr_path.exists() else "",
                }
            )

    return {
        "schema_version": WORKER_STATE_SCHEMA_VERSION,
        "mode": "local-process-supervisor",
        "configured_roles": configured_roles,
        "status_model": "queued -> leased -> completed|retry|dead_letter|cancelled|paused",
        "queue_counts": queue_counts,
        "role_counts": role_counts,
        "workers": sorted(observed.values(), key=lambda item: (item.get("role", ""), item.get("worker_id", ""))),
        "log_processes": log_processes,
    }
