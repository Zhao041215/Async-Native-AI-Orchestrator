-- V8 Initial Schema Migration
-- Safe to run multiple times: all statements use IF NOT EXISTS / ON CONFLICT DO NOTHING

CREATE TABLE IF NOT EXISTS projects (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    name         TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    project_path TEXT NOT NULL DEFAULT '',
    config       JSONB NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS projects_tenant_idx ON projects(tenant_id);

CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'queued',
    checkpoint   TEXT NOT NULL DEFAULT 'run_created',
    continuation JSONB NOT NULL DEFAULT '{}',
    metadata     JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS runs_project_idx ON runs(project_id);
CREATE INDEX IF NOT EXISTS runs_tenant_idx ON runs(tenant_id);

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    run_id          TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    job_type        TEXT NOT NULL,
    role            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',
    resume_key      TEXT NOT NULL DEFAULT '',
    work_package_id TEXT,
    wave_id         TEXT,
    payload         JSONB NOT NULL DEFAULT '{}',
    result          JSONB NOT NULL DEFAULT '{}',
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    worker_id       TEXT NOT NULL DEFAULT '',
    lease_until     TIMESTAMPTZ,
    heartbeat_at    TIMESTAMPTZ,
    last_error      TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_run_idx ON jobs(run_id);
CREATE INDEX IF NOT EXISTS jobs_tenant_role_status_idx ON jobs(tenant_id, role, status);
CREATE INDEX IF NOT EXISTS jobs_resume_key_idx ON jobs(resume_key) WHERE resume_key != '';
CREATE INDEX IF NOT EXISTS jobs_lease_until_idx ON jobs(lease_until) WHERE status IN ('leased', 'running');

CREATE TABLE IF NOT EXISTS waves (
    id        TEXT PRIMARY KEY,
    run_id    TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    wave_key  TEXT NOT NULL,
    sequence  INTEGER NOT NULL DEFAULT 0,
    status    TEXT NOT NULL DEFAULT 'queued',
    UNIQUE(run_id, wave_key)
);
CREATE INDEX IF NOT EXISTS waves_run_idx ON waves(run_id);

CREATE TABLE IF NOT EXISTS work_packages (
    id               TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    wave_id          TEXT NOT NULL REFERENCES waves(id) ON DELETE CASCADE,
    wave_key         TEXT NOT NULL,
    package_key      TEXT NOT NULL,
    role             TEXT NOT NULL,
    domain           TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'queued',
    allowed_paths    JSONB NOT NULL DEFAULT '[]',
    forbidden_paths  JSONB NOT NULL DEFAULT '[]',
    depends_on       JSONB NOT NULL DEFAULT '[]',
    objective        TEXT NOT NULL DEFAULT '',
    expected_outputs JSONB NOT NULL DEFAULT '[]',
    acceptance_gates JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS work_packages_run_idx ON work_packages(run_id);
CREATE INDEX IF NOT EXISTS work_packages_wave_idx ON work_packages(wave_id);

CREATE TABLE IF NOT EXISTS ai_slots (
    id            TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    provider      TEXT NOT NULL,
    agent_run_id  TEXT NOT NULL,
    task_kind     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    acquired_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until   TIMESTAMPTZ NOT NULL,
    released_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ai_slots_provider_status_idx ON ai_slots(provider, status);
CREATE INDEX IF NOT EXISTS ai_slots_run_status_idx ON ai_slots(run_id, status);
CREATE INDEX IF NOT EXISTS ai_slots_lease_idx ON ai_slots(lease_until) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS artifacts (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    project_id   TEXT NOT NULL,
    run_id       TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    job_id       TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL,
    key          TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/json',
    content      TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(run_id, kind, key)
);
CREATE INDEX IF NOT EXISTS artifacts_run_idx ON artifacts(run_id);
CREATE INDEX IF NOT EXISTS artifacts_run_kind_idx ON artifacts(run_id, kind);

CREATE TABLE IF NOT EXISTS events (
    id         TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    run_id     TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload    JSONB NOT NULL DEFAULT '{}',
    sequence   INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_run_idx ON events(run_id);
CREATE INDEX IF NOT EXISTS events_run_type_idx ON events(run_id, event_type);
CREATE INDEX IF NOT EXISTS events_run_seq_idx ON events(run_id, sequence);

CREATE TABLE IF NOT EXISTS agent_runs (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    run_id       TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    job_id       TEXT NOT NULL,
    role         TEXT NOT NULL,
    task_kind    TEXT NOT NULL,
    model        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'ok',
    elapsed_ms   INTEGER NOT NULL DEFAULT 0,
    input_chars  INTEGER NOT NULL DEFAULT 0,
    output_chars INTEGER NOT NULL DEFAULT 0,
    error        TEXT NOT NULL DEFAULT '',
    contract_ok  BOOLEAN,
    contract_errors JSONB NOT NULL DEFAULT '[]',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_runs_run_idx ON agent_runs(run_id);
CREATE INDEX IF NOT EXISTS agent_runs_contract_idx ON agent_runs(run_id, contract_ok) WHERE contract_ok = false;

CREATE TABLE IF NOT EXISTS provider_states (
    provider            TEXT PRIMARY KEY,
    status              TEXT NOT NULL DEFAULT 'healthy',
    success_count       INTEGER NOT NULL DEFAULT 0,
    failure_count       INTEGER NOT NULL DEFAULT 0,
    failure_streak      INTEGER NOT NULL DEFAULT 0,
    last_error_kind     TEXT NOT NULL DEFAULT '',
    last_error          TEXT NOT NULL DEFAULT '',
    last_event_at       DOUBLE PRECISION NOT NULL DEFAULT 0,
    retry_after_seconds INTEGER NOT NULL DEFAULT 0,
    circuit_open_until  DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO schema_migrations(version) VALUES ('001_v8_initial') ON CONFLICT DO NOTHING;
