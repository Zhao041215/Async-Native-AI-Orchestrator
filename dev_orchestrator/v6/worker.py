from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dev_orchestrator.config import load_config
from dev_orchestrator.llm_client import OpenAICompatibleClient
from dev_orchestrator.v6.models import DEFAULT_TENANT, ROLES, iso_now, new_id
from dev_orchestrator.v6.profiles import resolve_scale_profile
from dev_orchestrator.v6.service import V6Orchestrator


DEFAULT_WORKER_ROLES = tuple(ROLES)


class WorkerStatusRegistry:
    def __init__(self, workspace_root: Path):
        self.root = workspace_root / "runtime" / "workers"
        self.root.mkdir(parents=True, exist_ok=True)

    def update(self, worker_id: str, payload: dict[str, Any]) -> None:
        path = self.root / f"{worker_id}.json"
        payload = dict(payload)
        payload.setdefault("worker_id", worker_id)
        payload["last_heartbeat"] = iso_now()
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    def list(self, limit: int = 12) -> list[dict[str, Any]]:
        workers = []
        for path in sorted(self.root.glob("*.json")):
            try:
                workers.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        workers.sort(key=self._heartbeat_sort_key, reverse=True)
        return workers[: max(1, limit)]

    def _heartbeat_sort_key(self, payload: dict[str, Any]) -> datetime:
        raw = str(payload.get("last_heartbeat") or "")
        try:
            value = datetime.fromisoformat(raw)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)


@dataclass
class WorkerRunResult:
    worker_id: str
    role: str
    claimed: bool
    job_id: str = ""
    job_type: str = ""
    status: str = "idle"
    error: str = ""
    processed_job_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "role": self.role,
            "claimed": self.claimed,
            "job_id": self.job_id,
            "job_type": self.job_type,
            "status": self.status,
            "error": self.error,
            "processed_job_count": self.processed_job_count,
        }


class DurableWorker:
    def __init__(
        self,
        service: V6Orchestrator,
        role: str,
        tenant_id: str = DEFAULT_TENANT,
        worker_id: str = "",
        poll_seconds: float = 2.0,
        lease_seconds: int = 300,
        registry: WorkerStatusRegistry | None = None,
    ):
        if role not in ROLES:
            raise ValueError(f"unknown worker role: {role}")
        self.service = service
        self.role = role
        self.tenant_id = tenant_id or DEFAULT_TENANT
        self.worker_id = worker_id or f"{role}-{os.getpid()}-{new_id()[:8]}"
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.registry = registry or WorkerStatusRegistry(service.workspace_root)
        self.processed_job_count = 0
        self.root_dir = Path(__file__).resolve().parents[2]
        self._config_mtime = 0.0
        self._heartbeat_stop: threading.Event | None = None
        self._heartbeat_thread: threading.Thread | None = None

    def run_once(self) -> WorkerRunResult:
        self._reload_llm_config_if_changed()
        self.service.store.requeue_expired_jobs(self.tenant_id)
        self._status("idle", current_job=None, last_error="")
        job = self.service.store.claim_job(self.tenant_id, self.role, self.worker_id, self.lease_seconds)
        if not job:
            return WorkerRunResult(worker_id=self.worker_id, role=self.role, claimed=False, status="idle", processed_job_count=self.processed_job_count)
        self._status("leased", current_job={"id": job["id"], "job_type": job["job_type"], "run_id": job.get("run_id")}, last_error="")
        try:
            running_job = self.service.store.start_job(job["id"], self.worker_id, self.lease_seconds)
            if not running_job:
                raise RuntimeError("leased job could not transition to running")
            job = running_job
            self._status("running", current_job={"id": job["id"], "job_type": job["job_type"], "run_id": job.get("run_id")}, last_error="")
            self.service.store.heartbeat_job(job["id"], self.worker_id, self.lease_seconds)
            self._start_heartbeat(job["id"])
            try:
                result = self.service.execute_job(job)
            finally:
                self._stop_heartbeat()
            self.service.store.finish_job(job["id"], self.worker_id, result)
            self.processed_job_count += 1
            self._status("idle", current_job=None, last_error="")
            return WorkerRunResult(
                worker_id=self.worker_id,
                role=self.role,
                claimed=True,
                job_id=job["id"],
                job_type=job["job_type"],
                status="completed",
                processed_job_count=self.processed_job_count,
            )
        except Exception as exc:
            self._stop_heartbeat()
            error = f"{type(exc).__name__}: {exc}"
            retryable = bool(getattr(exc, "retryable", True))
            self.service.store.fail_job(job["id"], self.worker_id, error, retryable=retryable)
            self._status("error", current_job=None, last_error=error)
            return WorkerRunResult(
                worker_id=self.worker_id,
                role=self.role,
                claimed=True,
                job_id=job["id"],
                job_type=job["job_type"],
                status="failed",
                error=error,
                processed_job_count=self.processed_job_count,
            )

    def run_forever(self, max_jobs: int | None = None) -> dict[str, Any]:
        started = iso_now()
        while True:
            result = self.run_once()
            if result.claimed and result.status == "completed" and max_jobs and self.processed_job_count >= max_jobs:
                return {"status": "stopped", "reason": "max_jobs", "started_at": started, "result": result.to_dict()}
            if not result.claimed:
                time.sleep(self.poll_seconds)

    def _status(self, status: str, current_job: dict[str, Any] | None, last_error: str) -> None:
        self.registry.update(
            self.worker_id,
            {
                "role": self.role,
                "pid": os.getpid(),
                "status": status,
                "current_job": current_job,
                "processed_job_count": self.processed_job_count,
                "last_error": last_error,
                "log_path": "",
            },
        )

    def _reload_llm_config_if_changed(self) -> None:
        if self.service.llm_client is None or not isinstance(self.service.llm_client, OpenAICompatibleClient):
            return
        config_path = self.root_dir / "orchestrator_config.json"
        try:
            mtime = config_path.stat().st_mtime
        except OSError:
            return
        if mtime <= self._config_mtime:
            return
        config = load_config(self.root_dir)
        self.service.update_llm_client(OpenAICompatibleClient(config.llm))
        self._config_mtime = mtime

    def _start_heartbeat(self, job_id: str) -> None:
        self._stop_heartbeat()
        stop = threading.Event()
        interval = max(1.0, min(20.0, self.lease_seconds / 4))
        self._heartbeat_stop = stop

        def beat() -> None:
            while not stop.wait(interval):
                try:
                    self.service.store.heartbeat_job(job_id, self.worker_id, self.lease_seconds)
                    self._status("running", current_job={"id": job_id}, last_error="")
                except Exception:
                    continue

        thread = threading.Thread(target=beat, name=f"v6-heartbeat-{self.worker_id}", daemon=True)
        thread.start()
        self._heartbeat_thread = thread

    def _stop_heartbeat(self) -> None:
        stop = self._heartbeat_stop
        thread = self._heartbeat_thread
        self._heartbeat_stop = None
        self._heartbeat_thread = None
        if stop:
            stop.set()
        if thread and thread.is_alive():
            thread.join(timeout=1.0)


class WorkerSupervisor:
    def __init__(
        self,
        root_dir: Path,
        workspace_root: Path,
        roles: list[str] | None = None,
        tenant_id: str = DEFAULT_TENANT,
        poll_seconds: float = 2.0,
        lease_seconds: int = 300,
        concurrency: dict[str, int] | None = None,
    ):
        self.root_dir = root_dir
        self.workspace_root = workspace_root
        self.roles = roles or list(DEFAULT_WORKER_ROLES)
        self.tenant_id = tenant_id or DEFAULT_TENANT
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self.concurrency = {role: max(1, int((concurrency or {}).get(role, 1))) for role in self.roles}
        self.registry = WorkerStatusRegistry(workspace_root)
        self.processes: dict[str, subprocess.Popen[Any]] = {}
        self.log_handles: dict[str, Any] = {}

    def start(self) -> list[dict[str, Any]]:
        started = []
        for role in self.roles:
            for index in range(self.concurrency.get(role, 1)):
                process = self._start_role(role, index)
                started.append({"role": role, "slot": index + 1, "pid": process.pid})
        self.registry.update(
            "supervisor",
            {
                "role": "supervisor",
                "pid": os.getpid(),
                "status": "running",
                "current_job": None,
                "processed_job_count": 0,
                "last_error": "",
                "log_path": "",
                "children": started,
            },
        )
        return started

    def run_forever(self) -> None:
        if not self.processes:
            self.start()
        while True:
            for key, process in list(self.processes.items()):
                if process.poll() is not None:
                    self._close_log(key)
                    role, index_text = key.rsplit("#", 1)
                    self._start_role(role, int(index_text))
            time.sleep(2.0)

    def _start_role(self, role: str, index: int = 0) -> subprocess.Popen[Any]:
        if role not in ROLES:
            raise ValueError(f"unknown supervisor role: {role}")
        key = f"{role}#{index}"
        logs_root = self.root_dir / "logs" / "v6-workers"
        logs_root.mkdir(parents=True, exist_ok=True)
        log_path = logs_root / f"{role}-{index + 1}.log" if self.concurrency.get(role, 1) > 1 else logs_root / f"{role}.log"
        handle = log_path.open("ab")
        args = [
            sys.executable,
            "run_server.py",
            "--worker",
            "--role",
            role,
            "--tenant",
            self.tenant_id,
            "--worker-id",
            f"{role}-{index + 1}",
            "--poll-seconds",
            str(self.poll_seconds),
            "--lease-seconds",
            str(self.lease_seconds),
        ]
        process = subprocess.Popen(args, cwd=str(self.root_dir), stdout=handle, stderr=handle)
        self.processes[key] = process
        self.log_handles[key] = handle
        self.registry.update(
            f"supervisor-child-{role}-{index + 1}",
            {
                "role": role,
                "slot": index + 1,
                "pid": process.pid,
                "status": "started",
                "current_job": None,
                "processed_job_count": 0,
                "last_error": "",
                "log_path": str(log_path),
            },
        )
        return process

    def _close_log(self, key: str) -> None:
        handle = self.log_handles.pop(key, None)
        if handle:
            try:
                handle.close()
            except OSError:
                pass


def parse_role_concurrency(value: str) -> dict[str, int]:
    if not value.strip():
        return {}
    result: dict[str, int] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            role, count = item.split("=", 1)
        elif ":" in item:
            role, count = item.split(":", 1)
        else:
            role, count = item, "1"
        role = role.strip().lower()
        if role not in ROLES:
            raise ValueError(f"unknown worker role: {role}")
        result[role] = max(1, int(count.strip() or "1"))
    return result


def default_role_concurrency(target_scale: str = "xlarge_100k") -> dict[str, int]:
    return dict(resolve_scale_profile({"target_scale": target_scale}).get("worker_role_concurrency") or {})

