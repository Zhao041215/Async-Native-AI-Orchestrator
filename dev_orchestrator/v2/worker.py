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

from dev_orchestrator.v2.service import V2Orchestrator


DEFAULT_WORKER_ROLES = ("chief", "planner", "architect", "frontend", "backend", "qa", "security", "integration", "release")


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
        return {"role": item.role, "index": item.index, "pid": item.process.pid, "status": "running"}
