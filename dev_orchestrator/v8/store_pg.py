"""V8 PostgreSQL Store — implements the full AbstractStore protocol using psycopg async.

Replaces InMemoryStore for production use. State survives process restarts,
supports multi-worker concurrency via SELECT ... FOR UPDATE SKIP LOCKED,
and uses idempotent DDL so bootstrap() is safe to call on every startup.
"""
from __future__ import annotations

import asyncio
import json
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dev_orchestrator.v8.observability import get_logger
from dev_orchestrator.v8.models import (
    AISlot,
    AgentRun,
    Artifact,
    ErrorClassification,
    Event,
    Job,
    JobStatus,
    PackageRole,
    Project,
    ProjectConfig,
    ProviderHealthStatus,
    ProviderState,
    Run,
    RunMetadata,
    RunStatus,
    Wave,
    WorkPackage,
    new_id,
    normalize_database_url,
    utc_now,
)

try:
    import psycopg
    import psycopg.rows
    from psycopg_pool import AsyncConnectionPool
    _PSYCOPG_AVAILABLE = True
except ImportError:
    _PSYCOPG_AVAILABLE = False

log = get_logger("store_pg")

_MIGRATION_SQL = (
    Path(__file__).parent / "migrations" / "001_v8_initial.sql"
).read_text(encoding="utf-8")

_TERMINAL_JOB_STATUSES = (
    JobStatus.completed.value,
    JobStatus.dead_letter.value,
    JobStatus.cancelled.value,
)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _row_to_project(row: dict[str, Any]) -> Project:
    cfg = row.get("config") or {}
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    return Project(
        id=row["id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        title=row.get("title", ""),
        description=row.get("description", ""),
        project_path=row.get("project_path", ""),
        config=ProjectConfig.model_validate(cfg) if cfg else ProjectConfig(name=row["name"]),
        status=row.get("status", "active"),
        created_at=_utc(row["created_at"]) or utc_now(),
        updated_at=_utc(row["updated_at"]) or utc_now(),
    )


def _row_to_run(row: dict[str, Any]) -> Run:
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    cont = row.get("continuation") or {}
    if isinstance(cont, str):
        cont = json.loads(cont)
    return Run(
        id=row["id"],
        tenant_id=row["tenant_id"],
        project_id=row["project_id"],
        status=RunStatus(row["status"]),
        checkpoint=row.get("checkpoint", "run_created"),
        continuation=Run.model_fields["continuation"].default_factory() if not cont  # type: ignore[misc]
            else Run.__pydantic_fields__["continuation"].annotation.model_validate(cont),  # type: ignore[union-attr]
        metadata=RunMetadata.model_validate(meta),
        created_at=_utc(row["created_at"]) or utc_now(),
        updated_at=_utc(row["updated_at"]) or utc_now(),
    )


def _row_to_job(row: dict[str, Any]) -> Job:
    payload = row.get("payload") or {}
    result = row.get("result") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(result, str):
        result = json.loads(result)
    from dev_orchestrator.v8.models import JobType
    return Job(
        id=row["id"],
        tenant_id=row["tenant_id"],
        run_id=row["run_id"],
        job_type=JobType(row["job_type"]),
        role=PackageRole(row["role"]),
        status=JobStatus(row["status"]),
        resume_key=row.get("resume_key", ""),
        work_package_id=row.get("work_package_id"),
        wave_id=row.get("wave_id"),
        payload=payload,
        result=result,
        attempts=row.get("attempts", 0),
        max_attempts=row.get("max_attempts", 3),
        worker_id=row.get("worker_id", ""),
        lease_until=_utc(row.get("lease_until")),
        heartbeat_at=_utc(row.get("heartbeat_at")),
        last_error=row.get("last_error", ""),
        created_at=_utc(row["created_at"]) or utc_now(),
        updated_at=_utc(row["updated_at"]) or utc_now(),
    )


def _row_to_wave(row: dict[str, Any]) -> Wave:
    return Wave(
        id=row["id"],
        run_id=row["run_id"],
        wave_key=row["wave_key"],
        sequence=row["sequence"],
        status=row.get("status", "queued"),
    )


def _row_to_work_package(row: dict[str, Any]) -> WorkPackage:
    def _jlist(v: Any) -> list:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            return json.loads(v)
        return []
    return WorkPackage(
        id=row["id"],
        run_id=row["run_id"],
        wave_id=row["wave_id"],
        wave_key=row["wave_key"],
        package_key=row["package_key"],
        role=PackageRole(row["role"]),
        domain=row.get("domain", ""),
        status=JobStatus(row.get("status", "queued")),
        allowed_paths=_jlist(row.get("allowed_paths")),
        forbidden_paths=_jlist(row.get("forbidden_paths")),
        depends_on=_jlist(row.get("depends_on")),
        objective=row.get("objective", ""),
        expected_outputs=_jlist(row.get("expected_outputs")),
        acceptance_gates=_jlist(row.get("acceptance_gates")),
    )


def _row_to_ai_slot(row: dict[str, Any]) -> AISlot:
    from typing import Literal
    return AISlot(
        id=row["id"],
        tenant_id=row["tenant_id"],
        run_id=row["run_id"],
        provider=row["provider"],
        agent_run_id=row["agent_run_id"],
        task_kind=row["task_kind"],
        status=row.get("status", "active"),  # type: ignore[arg-type]
        acquired_at=_utc(row["acquired_at"]) or utc_now(),
        lease_until=_utc(row["lease_until"]) or utc_now(),
        released_at=_utc(row.get("released_at")),
    )


def _row_to_artifact(row: dict[str, Any]) -> Artifact:
    return Artifact(
        id=row["id"],
        tenant_id=row["tenant_id"],
        project_id=row["project_id"],
        run_id=row["run_id"],
        job_id=row.get("job_id", ""),
        kind=row["kind"],
        key=row["key"],
        content_type=row.get("content_type", "application/json"),
        content=row.get("content", ""),
        created_at=_utc(row["created_at"]) or utc_now(),
    )


def _row_to_event(row: dict[str, Any]) -> Event:
    payload = row.get("payload") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    return Event(
        id=row["id"],
        tenant_id=row["tenant_id"],
        run_id=row["run_id"],
        event_type=row["event_type"],
        payload=payload,
        sequence=row.get("sequence", 0),
        created_at=_utc(row["created_at"]) or utc_now(),
    )


def _row_to_agent_run(row: dict[str, Any]) -> AgentRun:
    errs = row.get("contract_errors") or []
    if isinstance(errs, str):
        errs = json.loads(errs)
    return AgentRun(
        id=row["id"],
        tenant_id=row["tenant_id"],
        run_id=row["run_id"],
        job_id=row["job_id"],
        role=row["role"],
        task_kind=row["task_kind"],
        model=row.get("model", ""),
        status=row.get("status", "ok"),
        elapsed_ms=row.get("elapsed_ms", 0),
        input_chars=row.get("input_chars", 0),
        output_chars=row.get("output_chars", 0),
        error=row.get("error", ""),
        contract_ok=row.get("contract_ok"),
        contract_errors=errs,
        created_at=_utc(row["created_at"]) or utc_now(),
    )


def _row_to_provider_state(row: dict[str, Any]) -> ProviderState:
    return ProviderState(
        provider=row["provider"],
        status=ProviderHealthStatus(row.get("status", "healthy")),
        success_count=row.get("success_count", 0),
        failure_count=row.get("failure_count", 0),
        failure_streak=row.get("failure_streak", 0),
        last_error_kind=row.get("last_error_kind", ""),
        last_error=row.get("last_error", ""),
        last_event_at=row.get("last_event_at", 0.0),
        retry_after_seconds=row.get("retry_after_seconds", 0),
        circuit_open_until=row.get("circuit_open_until", 0.0),
    )


class StoreError(RuntimeError):
    """Raised when a store operation fails."""


class PostgresStore:
    """PostgreSQL-backed store implementing the full AbstractStore protocol.

    Uses psycopg async driver with connection pooling. State survives restarts.
    bootstrap() runs idempotent DDL on every startup — safe to call repeatedly.
    """

    def __init__(self, database_url: str, *, min_size: int = 2, max_size: int = 20) -> None:
        if not _PSYCOPG_AVAILABLE:
            raise RuntimeError(
                "psycopg is not installed. Run: pip install 'psycopg[binary,pool]>=3.2'"
            )
        # psycopg uses postgresql:// not postgresql+psycopg://
        self._url = normalize_database_url(database_url)
        self._min_size = min_size
        self._max_size = max_size
        self._pool: AsyncConnectionPool | None = None

    async def bootstrap(self) -> None:
        """Open connection pool and run idempotent DDL migration."""
        self._pool = AsyncConnectionPool(
            self._url,
            min_size=self._min_size,
            max_size=self._max_size,
            open=False,
        )
        await self._pool.open()
        async with self._pool.connection() as conn:
            await conn.execute(_MIGRATION_SQL)
        log.info("postgres_store_bootstrapped", url=self._url.split("@")[-1])

    async def _conn(self, timeout: float = 10.0):  # type: ignore[return]
        if self._pool is None:
            raise StoreError("PostgresStore not bootstrapped — call bootstrap() first")
        return self._pool.connection(timeout=timeout)

    # -- Projects -----------------------------------------------------------

    async def create_project(self, tenant_id: str, project: Project) -> Project:
        project.tenant_id = tenant_id
        now = utc_now()
        project.updated_at = now
        async with await self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO projects (id, tenant_id, name, title, description, project_path, config, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (project.id, tenant_id, project.name, project.title, project.description,
                 project.project_path, json.dumps(project.config.model_dump()), project.status,
                 project.created_at, project.updated_at),
            )
        return project

    async def get_project(self, project_id: str) -> Project | None:
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
                row = await cur.fetchone()
        return _row_to_project(row) if row else None

    async def list_projects(self, tenant_id: str) -> list[Project]:
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM projects WHERE tenant_id = %s ORDER BY created_at DESC",
                    (tenant_id,),
                )
                rows = await cur.fetchall()
        return [_row_to_project(r) for r in rows]

    async def delete_project(self, project_id: str) -> None:
        async with await self._conn() as conn:
            # ON DELETE CASCADE handles child rows
            await conn.execute("DELETE FROM projects WHERE id = %s", (project_id,))

    # -- Runs ---------------------------------------------------------------

    async def create_run(self, tenant_id: str, project_id: str, metadata: RunMetadata) -> Run:
        run = Run(tenant_id=tenant_id, project_id=project_id, metadata=metadata)
        async with await self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO runs (id, tenant_id, project_id, status, checkpoint, continuation, metadata, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (run.id, tenant_id, project_id, run.status.value, run.checkpoint,
                 json.dumps(run.continuation.model_dump()),
                 json.dumps(run.metadata.model_dump()),
                 run.created_at, run.updated_at),
            )
        return run

    async def get_run(self, run_id: str) -> Run | None:
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute("SELECT * FROM runs WHERE id = %s", (run_id,))
                row = await cur.fetchone()
        return _row_to_run(row) if row else None

    async def update_run(self, run_id: str, **fields: object) -> Run:
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute("SELECT * FROM runs WHERE id = %s FOR UPDATE", (run_id,))
                row = await cur.fetchone()
                if row is None:
                    raise StoreError(f"run not found: {run_id}")

                run = _row_to_run(row)
                now = utc_now()

                # Apply field updates with type coercion
                for key, value in fields.items():
                    if key == "status" and isinstance(value, str):
                        value = RunStatus(value)
                    if key == "metadata":
                        if isinstance(value, dict):
                            value = RunMetadata.model_validate(value)
                        if isinstance(value, RunMetadata):
                            setattr(run, key, value)
                            continue
                    if hasattr(run, key):
                        setattr(run, key, value)
                run.updated_at = now

                meta_json = json.dumps(run.metadata.model_dump())
                cont_json = json.dumps(run.continuation.model_dump())
                await cur.execute(
                    """
                    UPDATE runs SET status=%s, checkpoint=%s, continuation=%s, metadata=%s, updated_at=%s
                    WHERE id=%s
                    """,
                    (run.status.value, run.checkpoint, cont_json, meta_json, now, run_id),
                )
        return run

    async def list_runs(self, project_id: str) -> list[Run]:
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM runs WHERE project_id = %s ORDER BY created_at DESC",
                    (project_id,),
                )
                rows = await cur.fetchall()
        return [_row_to_run(r) for r in rows]

    # -- Jobs ---------------------------------------------------------------

    async def enqueue_job(self, tenant_id: str, job: Job) -> Job:
        job.tenant_id = tenant_id
        job.status = JobStatus.queued
        job.updated_at = utc_now()
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                # Idempotency: return existing non-terminal job with same resume_key
                if job.resume_key:
                    await cur.execute(
                        """
                        SELECT * FROM jobs WHERE resume_key = %s AND status != ALL(%s::text[])
                        LIMIT 1
                        """,
                        (job.resume_key, list(_TERMINAL_JOB_STATUSES)),
                    )
                    existing = await cur.fetchone()
                    if existing:
                        return _row_to_job(existing)

                await cur.execute(
                    """
                    INSERT INTO jobs (id, tenant_id, run_id, job_type, role, status, resume_key,
                        work_package_id, wave_id, payload, result, attempts, max_attempts,
                        worker_id, last_error, created_at, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (job.id, tenant_id, job.run_id, job.job_type.value, job.role.value,
                     job.status.value, job.resume_key, job.work_package_id, job.wave_id,
                     json.dumps(job.payload), json.dumps(job.result),
                     job.attempts, job.max_attempts, job.worker_id, job.last_error,
                     job.created_at, job.updated_at),
                )
        return job

    async def claim_job(self, tenant_id: str, role: PackageRole, worker_id: str, lease_seconds: int) -> Job | None:
        now = utc_now()
        lease_until = now + timedelta(seconds=lease_seconds)
        try:
            conn_ctx = await self._conn(timeout=2.0)
        except Exception:
            return None
        async with conn_ctx as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                    await cur.execute(
                        """
                        SELECT * FROM jobs
                        WHERE tenant_id = %s AND role = %s AND status = ANY(%s::text[])
                        ORDER BY created_at ASC
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                        """,
                        (tenant_id, role.value, ["queued", "retry"]),
                    )
                    row = await cur.fetchone()
                    if row is None:
                        return None

                    job_id = row["id"]
                    attempts = row.get("attempts", 0) + 1
                    await cur.execute(
                        """
                        UPDATE jobs SET status='leased', attempts=%s, worker_id=%s,
                            lease_until=%s, heartbeat_at=%s, updated_at=%s
                        WHERE id=%s
                        """,
                        (attempts, worker_id, lease_until, now, now, job_id),
                    )
                    # Re-fetch to get updated row
                    await cur.execute("SELECT * FROM jobs WHERE id=%s", (job_id,))
                    updated = await cur.fetchone()
        return _row_to_job(updated) if updated else None

    async def start_job(self, job_id: str, worker_id: str, lease_seconds: int) -> Job | None:
        now = utc_now()
        lease_until = now + timedelta(seconds=lease_seconds)
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    """
                    UPDATE jobs SET status='running', heartbeat_at=%s, lease_until=%s, updated_at=%s
                    WHERE id=%s AND worker_id=%s AND status='leased'
                    RETURNING *
                    """,
                    (now, lease_until, now, job_id, worker_id),
                )
                row = await cur.fetchone()
        return _row_to_job(row) if row else None

    async def finish_job(self, job_id: str, worker_id: str, result: dict) -> Job:
        now = utc_now()
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    """
                    UPDATE jobs SET status='completed', result=%s, lease_until=NULL,
                        heartbeat_at=%s, updated_at=%s
                    WHERE id=%s AND worker_id=%s
                    RETURNING *
                    """,
                    (json.dumps(result), now, now, job_id, worker_id),
                )
                row = await cur.fetchone()
        if row is None:
            raise StoreError(f"job not found or not owned: {job_id}")
        return _row_to_job(row)

    async def fail_job(self, job_id: str, worker_id: str, error: str, retryable: bool) -> Job:
        now = utc_now()
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute("SELECT attempts, max_attempts FROM jobs WHERE id=%s", (job_id,))
                meta = await cur.fetchone()
                if meta is None:
                    raise StoreError(f"job not found: {job_id}")
                attempts = meta["attempts"]
                max_attempts = meta["max_attempts"]
                new_status = (
                    JobStatus.retry.value
                    if retryable and attempts < max_attempts
                    else JobStatus.dead_letter.value
                )
                await cur.execute(
                    """
                    UPDATE jobs SET status=%s, worker_id='', lease_until=NULL,
                        last_error=%s, heartbeat_at=%s, updated_at=%s
                    WHERE id=%s AND worker_id=%s
                    RETURNING *
                    """,
                    (new_status, error[:2000], now, now, job_id, worker_id),
                )
                row = await cur.fetchone()
        if row is None:
            raise StoreError(f"job not found or not owned: {job_id}")
        return _row_to_job(row)

    async def heartbeat_job(self, job_id: str, worker_id: str, lease_seconds: int) -> None:
        now = utc_now()
        lease_until = now + timedelta(seconds=lease_seconds)
        async with await self._conn() as conn:
            await conn.execute(
                """
                UPDATE jobs SET heartbeat_at=%s, lease_until=%s, updated_at=%s
                WHERE id=%s AND worker_id=%s AND status = ANY(%s::text[])
                """,
                (now, lease_until, now, job_id, worker_id, ["leased", "running"]),
            )

    async def list_jobs(self, run_id: str | None = None, status: JobStatus | None = None) -> list[Job]:
        conditions = []
        params: list[Any] = []
        if run_id is not None:
            conditions.append("run_id = %s")
            params.append(run_id)
        if status is not None:
            conditions.append("status = %s")
            params.append(status.value)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(f"SELECT * FROM jobs {where} ORDER BY created_at", params)
                rows = await cur.fetchall()
        return [_row_to_job(r) for r in rows]

    async def count_jobs(self, run_id: str, status: JobStatus) -> int:
        async with await self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM jobs WHERE run_id=%s AND status=%s",
                    (run_id, status.value),
                )
                row = await cur.fetchone()
        return row[0] if row else 0

    async def requeue_expired_jobs(self, tenant_id: str) -> int:
        now = utc_now()
        async with await self._conn() as conn:
            async with conn.cursor() as cur:
                # Retry if under max_attempts, else dead_letter — single atomic UPDATE
                await cur.execute(
                    """
                    UPDATE jobs SET
                        status = CASE WHEN attempts < max_attempts THEN 'retry' ELSE 'dead_letter' END,
                        worker_id = '',
                        lease_until = NULL,
                        updated_at = %s
                    WHERE tenant_id = %s
                      AND status = ANY(%s::text[])
                      AND lease_until < %s
                    """,
                    (now, tenant_id, ["leased", "running"], now),
                )
                return cur.rowcount

    # -- AI Slots -----------------------------------------------------------

    async def acquire_ai_slot(
        self,
        tenant_id: str,
        run_id: str,
        provider: str,
        agent_run_id: str,
        task_kind: str,
        provider_limit: int,
        run_limit: int,
        wait_seconds: int,
        lease_seconds: int,
    ) -> AISlot:
        provider_limit = max(1, provider_limit)
        run_limit = max(1, run_limit)
        lease_sec = max(30, lease_seconds)
        deadline = _time.monotonic() + max(0, wait_seconds)

        while True:
            now = utc_now()
            lease_until = now + timedelta(seconds=lease_sec)

            async with await self._conn() as conn:
                async with conn.transaction():
                    async with conn.cursor() as cur:
                        # Expire stale active slots
                        await cur.execute(
                            "UPDATE ai_slots SET status='expired', released_at=%s WHERE status='active' AND lease_until < %s",
                            (now, now),
                        )
                        # Count active slots per provider and per run
                        await cur.execute(
                            "SELECT COUNT(*) FROM ai_slots WHERE provider=%s AND status='active'",
                            (provider,),
                        )
                        provider_active = (await cur.fetchone())[0]  # type: ignore[index]

                        await cur.execute(
                            "SELECT COUNT(*) FROM ai_slots WHERE run_id=%s AND status='active'",
                            (run_id,),
                        )
                        run_active = (await cur.fetchone())[0]  # type: ignore[index]

                        if provider_active < provider_limit and run_active < run_limit:
                            slot_id = new_id()
                            await cur.execute(
                                """
                                INSERT INTO ai_slots (id, tenant_id, run_id, provider, agent_run_id, task_kind, status, acquired_at, lease_until)
                                VALUES (%s,%s,%s,%s,%s,%s,'active',%s,%s)
                                """,
                                (slot_id, tenant_id, run_id, provider, agent_run_id, task_kind, now, lease_until),
                            )
                            return AISlot(
                                id=slot_id, tenant_id=tenant_id, run_id=run_id,
                                provider=provider, agent_run_id=agent_run_id,
                                task_kind=task_kind, status="active",
                                acquired_at=now, lease_until=lease_until,
                            )

            if _time.monotonic() >= deadline:
                raise StoreError("AI concurrency slot wait timed out")
            await asyncio.sleep(0.5)

    async def release_ai_slot(self, slot_id: str) -> None:
        async with await self._conn() as conn:
            await conn.execute(
                "UPDATE ai_slots SET status='released', released_at=%s WHERE id=%s",
                (utc_now(), slot_id),
            )

    async def list_active_ai_slots(self, run_id: str | None = None) -> list[AISlot]:
        where = "WHERE status='active'" + (" AND run_id=%s" if run_id else "")
        params = [run_id] if run_id else []
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(f"SELECT * FROM ai_slots {where}", params)
                rows = await cur.fetchall()
        return [_row_to_ai_slot(r) for r in rows]

    # -- Artifacts ----------------------------------------------------------

    async def add_artifact(self, tenant_id: str, project_id: str, artifact: Artifact) -> Artifact:
        artifact.tenant_id = tenant_id
        artifact.project_id = project_id
        artifact.created_at = utc_now()
        async with await self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO artifacts (id, tenant_id, project_id, run_id, job_id, kind, key, content_type, content, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id, kind, key) DO UPDATE SET content=EXCLUDED.content, job_id=EXCLUDED.job_id
                """,
                (artifact.id, tenant_id, project_id, artifact.run_id, artifact.job_id,
                 artifact.kind, artifact.key, artifact.content_type, artifact.content, artifact.created_at),
            )
        return artifact

    async def list_artifacts(self, run_id: str, kind: str | None = None) -> list[Artifact]:
        where = "WHERE run_id=%s" + (" AND kind=%s" if kind else "")
        params = [run_id] + ([kind] if kind else [])
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(f"SELECT * FROM artifacts {where} ORDER BY created_at", params)
                rows = await cur.fetchall()
        return [_row_to_artifact(r) for r in rows]

    async def get_artifact(self, run_id: str, kind: str, key: str) -> Artifact | None:
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM artifacts WHERE run_id=%s AND kind=%s AND key=%s",
                    (run_id, kind, key),
                )
                row = await cur.fetchone()
        return _row_to_artifact(row) if row else None

    # -- Events -------------------------------------------------------------

    async def add_event(self, event: Event) -> Event:
        event.created_at = utc_now()
        async with await self._conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE run_id=%s",
                    (event.run_id,),
                )
                seq = (await cur.fetchone())[0]  # type: ignore[index]
                event.sequence = seq
                await cur.execute(
                    """
                    INSERT INTO events (id, tenant_id, run_id, event_type, payload, sequence, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (event.id, event.tenant_id, event.run_id, event.event_type,
                     json.dumps(event.payload), seq, event.created_at),
                )
        return event

    async def list_events(self, run_id: str, event_type: str | None = None) -> list[Event]:
        where = "WHERE run_id=%s" + (" AND event_type=%s" if event_type else "")
        params = [run_id] + ([event_type] if event_type else [])
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(f"SELECT * FROM events {where} ORDER BY sequence, created_at", params)
                rows = await cur.fetchall()
        return [_row_to_event(r) for r in rows]

    # -- Waves & Work Packages ----------------------------------------------

    async def upsert_wave(self, run_id: str, wave: Wave) -> Wave:
        wave.run_id = run_id
        async with await self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO waves (id, run_id, wave_key, sequence, status)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (run_id, wave_key) DO UPDATE SET sequence=EXCLUDED.sequence, status=EXCLUDED.status
                """,
                (wave.id, run_id, wave.wave_key, wave.sequence, wave.status),
            )
            # Return the actual stored id (may differ if upserted)
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute("SELECT * FROM waves WHERE run_id=%s AND wave_key=%s", (run_id, wave.wave_key))
                row = await cur.fetchone()
        return _row_to_wave(row) if row else wave

    async def list_waves(self, run_id: str) -> list[Wave]:
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM waves WHERE run_id=%s ORDER BY sequence", (run_id,)
                )
                rows = await cur.fetchall()
        return [_row_to_wave(r) for r in rows]

    async def upsert_work_package(self, run_id: str, wave_id: str, package: WorkPackage) -> WorkPackage:
        package.run_id = run_id
        package.wave_id = wave_id
        async with await self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO work_packages (id, run_id, wave_id, wave_key, package_key, role, domain, status,
                    allowed_paths, forbidden_paths, depends_on, objective, expected_outputs, acceptance_gates)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO UPDATE SET
                    status=EXCLUDED.status, allowed_paths=EXCLUDED.allowed_paths,
                    forbidden_paths=EXCLUDED.forbidden_paths, depends_on=EXCLUDED.depends_on,
                    objective=EXCLUDED.objective, expected_outputs=EXCLUDED.expected_outputs,
                    acceptance_gates=EXCLUDED.acceptance_gates
                """,
                (package.id, run_id, wave_id, package.wave_key, package.package_key,
                 package.role.value, package.domain, package.status.value,
                 json.dumps(package.allowed_paths), json.dumps(package.forbidden_paths),
                 json.dumps(package.depends_on), package.objective,
                 json.dumps(package.expected_outputs), json.dumps(package.acceptance_gates)),
            )
        return package

    async def get_work_package(self, package_id: str) -> WorkPackage | None:
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute("SELECT * FROM work_packages WHERE id=%s", (package_id,))
                row = await cur.fetchone()
        return _row_to_work_package(row) if row else None

    async def update_work_package(self, package_id: str, **fields: object) -> WorkPackage:
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute("SELECT * FROM work_packages WHERE id=%s", (package_id,))
                row = await cur.fetchone()
                if row is None:
                    raise StoreError(f"work package not found: {package_id}")
                pkg = _row_to_work_package(row)
                for key, value in fields.items():
                    if key == "status" and isinstance(value, str):
                        value = JobStatus(value)
                    if hasattr(pkg, key):
                        setattr(pkg, key, value)
                await cur.execute(
                    "UPDATE work_packages SET status=%s WHERE id=%s",
                    (pkg.status.value, package_id),
                )
        return pkg

    async def list_work_packages(self, run_id: str) -> list[WorkPackage]:
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute("SELECT * FROM work_packages WHERE run_id=%s", (run_id,))
                rows = await cur.fetchall()
        return [_row_to_work_package(r) for r in rows]

    # -- Agent Runs ---------------------------------------------------------

    async def record_agent_run(self, agent_run: AgentRun) -> AgentRun:
        agent_run.created_at = utc_now()
        async with await self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO agent_runs (id, tenant_id, run_id, job_id, role, task_kind, model, status,
                    elapsed_ms, input_chars, output_chars, error, contract_ok, contract_errors, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
                """,
                (agent_run.id, agent_run.tenant_id, agent_run.run_id, agent_run.job_id,
                 agent_run.role, agent_run.task_kind, agent_run.model, agent_run.status,
                 agent_run.elapsed_ms, agent_run.input_chars, agent_run.output_chars,
                 agent_run.error, agent_run.contract_ok,
                 json.dumps(agent_run.contract_errors), agent_run.created_at),
            )
        return agent_run

    async def list_agent_runs(self, run_id: str) -> list[AgentRun]:
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM agent_runs WHERE run_id=%s ORDER BY created_at",
                    (run_id,),
                )
                rows = await cur.fetchall()
        return [_row_to_agent_run(r) for r in rows]

    async def list_contract_violations(self, run_id: str) -> list[AgentRun]:
        """Extra V8 method: returns agent runs with contract failures."""
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM agent_runs WHERE run_id=%s AND contract_ok=false ORDER BY created_at",
                    (run_id,),
                )
                rows = await cur.fetchall()
        return [_row_to_agent_run(r) for r in rows]

    # -- Provider Health ----------------------------------------------------

    async def record_provider_success(self, provider: str) -> ProviderState:
        provider = provider or "unknown-provider"
        now = _time.time()
        state = ProviderState(
            provider=provider, status=ProviderHealthStatus.healthy,
            last_event_at=now, failure_streak=0,
        )
        async with await self._conn() as conn:
            await conn.execute(
                """
                INSERT INTO provider_states (provider, status, success_count, failure_count, failure_streak,
                    last_error_kind, last_error, last_event_at, retry_after_seconds, circuit_open_until)
                VALUES (%s,'healthy',1,0,0,'','',%(t)s,0,0)
                ON CONFLICT (provider) DO UPDATE SET
                    status='healthy',
                    success_count=provider_states.success_count+1,
                    failure_streak=0,
                    last_event_at=%(t)s,
                    retry_after_seconds=0,
                    circuit_open_until=0
                """,
                {"provider": provider, "t": now},
            )
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute("SELECT * FROM provider_states WHERE provider=%s", (provider,))
                row = await cur.fetchone()
        return _row_to_provider_state(row) if row else state

    async def record_provider_failure(self, provider: str, error: str, classification: ErrorClassification) -> ProviderState:
        provider = provider or "unknown-provider"
        now = _time.time()
        retryable = classification.retryable
        backoff = classification.backoff_seconds

        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute("SELECT failure_streak FROM provider_states WHERE provider=%s", (provider,))
                existing = await cur.fetchone()
                streak = (existing["failure_streak"] if existing else 0) + 1

            new_status: str
            if not retryable:
                new_status = "blocked"
            elif streak >= 5:
                new_status = "circuit_open"
            else:
                new_status = "degraded"

            circuit_open_until = (now + backoff) if retryable and backoff > 0 else 0.0

            await conn.execute(
                """
                INSERT INTO provider_states (provider, status, success_count, failure_count, failure_streak,
                    last_error_kind, last_error, last_event_at, retry_after_seconds, circuit_open_until)
                VALUES (%s,%s,0,1,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (provider) DO UPDATE SET
                    status=%s,
                    failure_count=provider_states.failure_count+1,
                    failure_streak=%s,
                    last_error_kind=%s,
                    last_error=%s,
                    last_event_at=%s,
                    retry_after_seconds=%s,
                    circuit_open_until=%s
                """,
                (provider, new_status, streak, classification.error_kind, error[:1000], now,
                 int(backoff), circuit_open_until,
                 # ON CONFLICT DO UPDATE values:
                 new_status, streak, classification.error_kind, error[:1000], now,
                 int(backoff), circuit_open_until),
            )
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute("SELECT * FROM provider_states WHERE provider=%s", (provider,))
                row = await cur.fetchone()
        return _row_to_provider_state(row) if row else ProviderState(provider=provider)

    async def provider_health_snapshot(self) -> dict[str, ProviderState]:
        async with await self._conn() as conn:
            async with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                await cur.execute("SELECT * FROM provider_states")
                rows = await cur.fetchall()
        return {r["provider"]: _row_to_provider_state(r) for r in rows}

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
