"""Tests for V7 kernel, models, and core infrastructure."""
from __future__ import annotations

import asyncio
import pytest
from pathlib import Path

from dev_orchestrator.v7.models import (
    Job, JobStatus, JobType, PackageRole, Project, Run, RunStatus,
    ScaleProfileData, Wave, WorkPackage, new_id, stable_json, sha256_bytes,
    iso_now, slugify, source_line_count,
)
from dev_orchestrator.v7.profiles import resolve_scale_profile, list_scale_profiles, scale_job_attempts, SCALE_PROFILES
from dev_orchestrator.v7.role_aliases import canonical_worker_role, normalize_role_name
from dev_orchestrator.v7.scale_inference import infer_initial_scale, choose_larger_scale, normalize_scale_name
from dev_orchestrator.v7.kernel import build_checkpoint_resume_plan, build_state_transition_event, summarize_events
from dev_orchestrator.v7.resilience import classify_error, ProviderCircuitBreaker
from dev_orchestrator.v7.contracts import validate_agent_contract


# --- Models ---

class TestModels:
    def test_new_id_unique(self):
        ids = {new_id() for _ in range(100)}
        assert len(ids) == 100

    def test_stable_json_deterministic(self):
        a = {"b": 1, "a": 2}
        b = {"a": 2, "b": 1}
        assert stable_json(a) == stable_json(b)

    def test_sha256_bytes(self):
        h = sha256_bytes(b"hello")
        assert len(h) == 64
        assert h == sha256_bytes(b"hello")

    def test_slugify(self):
        assert slugify("Hello World!") == "hello-world"
        assert slugify("") == "project"
        assert slugify("  spaces  ") == "spaces"

    def test_scale_profile_data(self):
        p = ScaleProfileData(name="small", target_loc_hint=5000)
        assert p.name == "small"
        assert p.target_loc_hint == 5000

    def test_project_creation(self):
        p = Project(name="test", title="Test Project")
        assert p.name == "test"
        assert p.tenant_id == "local-workspace"
        assert p.status == "active"

    def test_run_creation(self):
        r = Run(project_id="proj-1")
        assert r.status == RunStatus.queued
        assert r.checkpoint == "run_created"

    def test_job_creation(self):
        j = Job(run_id="run-1", job_type=JobType.requirements_analysis, role=PackageRole.requirements)
        assert j.status == JobStatus.queued
        assert j.attempts == 0


# --- Profiles ---

class TestProfiles:
    def test_resolve_default(self):
        p = resolve_scale_profile(None)
        assert p["name"] == "medium"

    def test_resolve_explicit(self):
        p = resolve_scale_profile({"target_scale": "small"})
        assert p["name"] == "small"

    def test_resolve_alias(self):
        p = resolve_scale_profile({"target_scale": "xl"})
        assert p["name"] == "xlarge_100k"

    def test_list_profiles(self):
        profiles = list_scale_profiles()
        assert len(profiles) == 4
        names = {p["name"] for p in profiles}
        assert "small" in names
        assert "xlarge_100k" in names

    def test_scale_job_attempts(self):
        attempts = scale_job_attempts("requirements_analysis", {"name": "small"})
        assert attempts == 3

    def test_all_profiles_valid(self):
        for name, profile in SCALE_PROFILES.items():
            assert profile.name == name
            assert profile.target_loc_hint > 0
            assert profile.max_waves > 0


# --- Role Aliases ---

class TestRoleAliases:
    def test_normalize(self):
        assert normalize_role_name("backend-engineer") == "backend_engineer"
        assert normalize_role_name("QA") == "qa"

    def test_canonical(self):
        assert canonical_worker_role("database") == "db"
        assert canonical_worker_role("frontend_engineer") == "frontend"
        assert canonical_worker_role("unknown") == "backend"  # fallback

    def test_hint_based(self):
        assert canonical_worker_role("engineer", domain="database") == "db"
        assert canonical_worker_role("engineer", subsystem="ui") == "frontend"


# --- Scale Inference ---

class TestScaleInference:
    def test_initial_explicit(self):
        result = infer_initial_scale({"target_scale": "large"}, "")
        assert result["selected_scale"] == "large"
        assert result["auto"] is False

    def test_initial_auto(self):
        result = infer_initial_scale({"target_scale": "auto"}, "")
        assert result["auto"] is True

    def test_choose_larger(self):
        assert choose_larger_scale("small", "large") == "large"
        assert choose_larger_scale("large", "small") == "large"
        assert choose_larger_scale("medium", "medium") == "medium"


# --- Kernel ---

class TestKernel:
    def test_checkpoint_resume_plan(self):
        run = {"id": "r1", "status": "running", "checkpoint": "requirements_completed", "continuation": {}}
        plan = build_checkpoint_resume_plan(run=run, events=[], jobs=[], waves=[], packages=[])
        assert plan["checkpoint"] == "requirements_completed"
        assert plan["next_action"] == "architecture_design"

    def test_state_transition_event(self):
        prev = {"status": "queued", "checkpoint": "run_created"}
        curr = {"status": "running", "checkpoint": "requirements_completed", "tenant_id": "t1", "id": "r1"}
        event = build_state_transition_event(prev, curr)
        assert event is not None
        assert event["event_type"] == "mission_state_changed"

    def test_summarize_events(self):
        events = [{"event_type": "test", "sequence": 1, "created_at": "2026-01-01"}]
        summary = summarize_events(events)
        assert summary["count"] == 1


# --- Resilience ---

class TestResilience:
    def test_circuit_breaker_starts_closed(self):
        cb = ProviderCircuitBreaker()
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(cb.allow_request("test"))
        assert result is True
        loop.close()


# --- Contracts ---

class TestContracts:
    def test_validate_requirements(self):
        payload = {"status": "GO", "summary": "test", "goals": ["g1"]}
        result, errors = validate_agent_contract("requirements", payload)
        assert result is not None
        assert errors == []

    def test_validate_unknown_kind(self):
        result, errors = validate_agent_contract("unknown_kind", {})
        assert result is None
        assert len(errors) > 0

    def test_validate_missing_required(self):
        result, errors = validate_agent_contract("requirements", {})
        assert result is None
        assert any("status" in e for e in errors)
