from __future__ import annotations

import concurrent.futures
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from dev_orchestrator.config import AppConfig
from dev_orchestrator.v2.agent_runtime import AgentRunner, new_agent_run_payload
from dev_orchestrator.v2.chief import build_dag
from dev_orchestrator.v2.git_runtime import GitRuntime, GitRuntimeError
from dev_orchestrator.v2.models import RepairTask, utc_now
from dev_orchestrator.v2.quality import evaluate_project_quality, write_quality_report
from dev_orchestrator.v2.storage import V2Storage
from dev_orchestrator.v2.test_runner import TestRunner


TERMINAL_STATUSES = {"completed", "blocked", "failed", "release_candidate_ready", "cancelled"}


@dataclass
class AgentAttemptResult:
    agent_run: dict
    patch_set: dict | None
    worktree: dict | None


class V2ChiefExecutor:
    def __init__(
        self,
        *,
        config: AppConfig,
        storage: V2Storage,
        agent_runner: AgentRunner | None = None,
        test_runner: TestRunner | None = None,
        max_repair_rounds: int = 3,
    ) -> None:
        self.config = config
        self.storage = storage
        self.agent_runner = agent_runner or AgentRunner(config)
        self.test_runner = test_runner or TestRunner(config.runtime.max_shell_seconds)
        self.max_repair_rounds = max_repair_rounds

    def execute_run(self, project_id: str, run_id: str) -> dict:
        project = self.storage.get_project(project_id)
        requirement_bundle = self.storage.get_requirement_bundle(project_id)
        project_root = Path(project["project_path"]).resolve()
        agent_root = project_root / ".agent" / "v2"
        run_root = agent_root / "runs" / run_id
        patch_root = run_root / "patches"
        run_root.mkdir(parents=True, exist_ok=True)
        self.storage.update_run(run_id, status="running", chief_summary="Chief execution engine started.")
        self.storage.update_project(project_id, status="running")
        self.storage.add_event(run_id, "info", "chief", "V2 real execution run started.", {"project_id": project_id})

        git = GitRuntime(
            project_root=project_root,
            worktree_root=self.config.root_dir / "workspace" / "v2-worktrees" / project_id,
            timeout_seconds=self.config.runtime.max_shell_seconds,
        )
        try:
            sandbox = git.ensure_repository()
        except Exception as exc:
            return self._block_run(project_id, run_id, f"Git sandbox initialization failed: {exc}")

        base_sha = sandbox["base_sha"]
        self.storage.add_event(
            run_id,
            "info",
            "sandbox",
            "Git sandbox baseline ready.",
            {"base_sha": base_sha, "dirty_paths": sandbox.get("dirty_paths", [])[:20]},
        )

        dag = build_dag(requirement_bundle.get("work_packages", []))
        (run_root / "dag.json").write_text(json.dumps(dag, indent=2, ensure_ascii=True), encoding="utf-8")
        package_map = {item["id"]: item for item in requirement_bundle.get("work_packages", [])}
        self.storage.update_run(
            run_id,
            continuation_state={
                **self.storage.get_run(run_id).get("continuation_state", {}),
                "schema_version": "2.2.0",
                "checkpoint": "blueprint_complete",
                "state": "running",
                "next_action": "execute_waves",
                "completed_waves": [],
                "pending_waves": [wave.get("id") for wave in dag.get("waves", [])],
                "completed_packages": [],
                "pending_packages": list(package_map.keys()),
                "applied_patch_ids": [],
                "pending_patch_ids": [],
                "patch_paths": [],
                "release_manifest_path": "",
                "failure_reason": "",
                "summary": "DAG waves are ready for execution.",
            },
        )

        patch_sets: list[dict] = []
        failure_context = ""
        for wave in dag.get("waves", []):
            work_packages = [package_map[item_id] for item_id in wave.get("work_package_ids", []) if item_id in package_map]
            if not work_packages:
                continue
            self.storage.add_event(
                run_id,
                "info",
                "chief",
                "Dispatching DAG wave.",
                {
                    "wave_id": wave.get("id"),
                    "parallel": wave.get("parallel", False),
                    "work_packages": [item["id"] for item in work_packages],
                },
            )
            run_state = self.storage.get_run(run_id)
            continuation = run_state.get("continuation_state", {})
            continuation.update(
                {
                    "schema_version": "2.2.0",
                    "checkpoint": "wave_started",
                    "state": "running",
                    "next_action": "execute_wave",
                    "current_wave": wave.get("id"),
                    "current_packages": [item["id"] for item in work_packages],
                    "summary": f"Executing {wave.get('id')}.",
                }
            )
            self.storage.update_run(run_id, continuation_state=continuation)
            attempt_results = self._run_wave(
                git=git,
                run_id=run_id,
                base_sha=base_sha,
                work_packages=work_packages,
                requirement_bundle=requirement_bundle,
                patch_root=patch_root,
                attempt=1,
                failure_context=failure_context,
            )
            for result in attempt_results:
                if result.patch_set:
                    patch_sets.append(result.patch_set)
                if result.agent_run["status"] != "completed":
                    failure_context = result.agent_run.get("error") or result.agent_run.get("summary", "")
                    repair = self._save_repair(project_id, run_id, "agent_runtime_failed", result.agent_run["role"], failure_context)
                    self.storage.add_event(
                        run_id,
                        "warning",
                        "chief",
                        "Agent attempt failed; repair task created.",
                        {"repair_id": repair["id"], "work_package_id": result.agent_run["work_package_id"]},
                    )
                    repaired = self._run_repair_attempt(
                        git,
                        run_id,
                        base_sha,
                        package_map[result.agent_run["work_package_id"]],
                        requirement_bundle,
                        patch_root,
                        failure_context,
                    )
                    if repaired.patch_set:
                        patch_sets.append(repaired.patch_set)
                    if repaired.agent_run["status"] != "completed":
                        return self._block_run(project_id, run_id, "Agent repair attempts exhausted.")
            run_state = self.storage.get_run(run_id)
            continuation = run_state.get("continuation_state", {})
            completed = list(continuation.get("completed_waves", []))
            if wave.get("id") not in completed:
                completed.append(wave.get("id"))
            pending = [item for item in continuation.get("pending_waves", []) if item != wave.get("id")]
            completed_packages = set(continuation.get("completed_packages", []))
            for result in attempt_results:
                if result.agent_run.get("status") == "completed":
                    completed_packages.add(result.agent_run["work_package_id"])
            completed_package_list = sorted(completed_packages)
            pending_packages = [item for item in package_map if item not in completed_package_list]
            patch_paths = [
                item["patch_path"]
                for item in self.storage.list_patch_sets(run_id)
                if item.get("patch_path")
            ]
            continuation.update(
                {
                    "schema_version": "2.2.0",
                    "checkpoint": "wave_complete",
                    "state": "running",
                    "next_action": "execute_waves" if pending else "integrate_and_validate",
                    "completed_waves": completed,
                    "pending_waves": pending,
                    "completed_packages": completed_package_list,
                    "pending_packages": pending_packages,
                    "patch_paths": patch_paths,
                    "summary": f"{wave.get('id')} completed.",
                }
            )
            self.storage.update_run(run_id, continuation_state=continuation)

        integration = self._integrate_and_validate(
            project_id=project_id,
            run_id=run_id,
            git=git,
            base_sha=base_sha,
            patch_sets=patch_sets,
            requirement_bundle=requirement_bundle,
            run_root=run_root,
        )
        return integration

    def _run_wave(
        self,
        *,
        git: GitRuntime,
        run_id: str,
        base_sha: str,
        work_packages: list[dict],
        requirement_bundle: dict,
        patch_root: Path,
        attempt: int,
        failure_context: str,
    ) -> list[AgentAttemptResult]:
        serial_groups = self._serial_groups(work_packages)
        results: list[AgentAttemptResult] = []
        for group in serial_groups:
            if len(group) == 1:
                results.append(
                    self._run_agent_attempt(
                        git=git,
                        run_id=run_id,
                        base_sha=base_sha,
                        work_package=group[0],
                        requirement_bundle=requirement_bundle,
                        patch_root=patch_root,
                        attempt=attempt,
                        failure_context=failure_context,
                    )
                )
                continue
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(group))) as executor:
                futures = [
                    executor.submit(
                        self._run_agent_attempt,
                        git=git,
                        run_id=run_id,
                        base_sha=base_sha,
                        work_package=package,
                        requirement_bundle=requirement_bundle,
                        patch_root=patch_root,
                        attempt=attempt,
                        failure_context=failure_context,
                    )
                    for package in group
                ]
                for future in concurrent.futures.as_completed(futures):
                    results.append(future.result())
        return results

    def _serial_groups(self, work_packages: list[dict]) -> list[list[dict]]:
        groups: list[list[dict]] = []
        current: list[dict] = []
        current_paths: set[str] = set()
        for package in work_packages:
            paths = self._ownership_roots(package)
            if current and current_paths.intersection(paths):
                groups.append(current)
                current = [package]
                current_paths = set(paths)
            else:
                current.append(package)
                current_paths.update(paths)
        if current:
            groups.append(current)
        return groups

    def _ownership_roots(self, work_package: dict) -> set[str]:
        roots = set()
        for output in work_package.get("outputs", []) or []:
            first = str(output).replace("\\", "/").strip("/").split("/", 1)[0]
            roots.add(first or ".")
        if not roots:
            roots.add(str(work_package.get("owner_role", "agent")))
        return roots

    def _run_agent_attempt(
        self,
        *,
        git: GitRuntime,
        run_id: str,
        base_sha: str,
        work_package: dict,
        requirement_bundle: dict,
        patch_root: Path,
        attempt: int,
        failure_context: str,
    ) -> AgentAttemptResult:
        role = work_package.get("owner_role", "agent")
        work_package_id = work_package["id"]
        self.storage.add_event(
            run_id,
            "info",
            role,
            "Agent worktree attempt started.",
            {"work_package_id": work_package_id, "attempt": attempt},
        )
        worktree_payload: dict | None = None
        worktree_record: dict | None = None
        try:
            worktree_payload = git.create_worktree(run_id, role, work_package_id, attempt, base_sha)
            worktree_record = self.storage.save_worktree(
                {
                    "id": str(uuid.uuid4()),
                    "run_id": run_id,
                    "work_package_id": work_package_id,
                    "role": role,
                    "attempt": attempt,
                    "path": worktree_payload["path"],
                    "branch": worktree_payload["branch"],
                    "base_sha": base_sha,
                    "status": "created",
                    "created_at": utc_now(),
                    "cleaned_at": "",
                }
            )
            runtime_result = self.agent_runner.run(
                Path(worktree_payload["path"]),
                work_package,
                requirement_bundle,
                attempt,
                failure_context,
            )
            agent_run = new_agent_run_payload(
                run_id=run_id,
                work_package_id=work_package_id,
                role=role,
                attempt=attempt,
                status=runtime_result.status,
                worktree_path=worktree_payload["path"],
                branch=worktree_payload["branch"],
                summary=runtime_result.summary,
                model_calls=runtime_result.model_calls,
                tool_calls=runtime_result.tool_calls,
                error=runtime_result.error,
            )
            self.storage.save_agent_run(agent_run)
            if runtime_result.status != "completed":
                return AgentAttemptResult(agent_run=agent_run, patch_set=None, worktree=worktree_record)

            package_patch_path = patch_root / work_package_id / f"attempt-{attempt}.patch"
            patch_info = git.write_patch(Path(worktree_payload["path"]), base_sha, package_patch_path)
            status = "empty" if patch_info["empty"] else "captured"
            patch_set = {
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "work_package_id": work_package_id,
                "role": role,
                "attempt": attempt,
                "status": status,
                "patch_path": patch_info["patch_path"],
                "files_changed": patch_info["files_changed"],
                "diff_summary": patch_info["diff_summary"],
                "error": "",
                "created_at": utc_now(),
            }
            self.storage.save_patch_set(patch_set)
            self.storage.add_event(
                run_id,
                "info",
                role,
                "Agent patch captured from git diff.",
                {
                    "work_package_id": work_package_id,
                    "attempt": attempt,
                    "status": status,
                    "files_changed": patch_set["files_changed"],
                },
            )
            return AgentAttemptResult(agent_run=agent_run, patch_set=patch_set, worktree=worktree_record)
        except Exception as exc:
            agent_run = new_agent_run_payload(
                run_id=run_id,
                work_package_id=work_package_id,
                role=role,
                attempt=attempt,
                status="blocked",
                worktree_path=(worktree_payload or {}).get("path", ""),
                branch=(worktree_payload or {}).get("branch", ""),
                summary="Agent attempt failed before patch capture.",
                error=str(exc),
            )
            self.storage.save_agent_run(agent_run)
            return AgentAttemptResult(agent_run=agent_run, patch_set=None, worktree=worktree_record)

    def _run_repair_attempt(
        self,
        git: GitRuntime,
        run_id: str,
        base_sha: str,
        work_package: dict,
        requirement_bundle: dict,
        patch_root: Path,
        failure_context: str,
    ) -> AgentAttemptResult:
        last = AgentAttemptResult(
            agent_run=new_agent_run_payload(
                run_id=run_id,
                work_package_id=work_package["id"],
                role=work_package.get("owner_role", "agent"),
                attempt=self.max_repair_rounds,
                status="blocked",
                summary="Repair attempts exhausted.",
            ),
            patch_set=None,
            worktree=None,
        )
        for attempt in range(2, self.max_repair_rounds + 2):
            last = self._run_agent_attempt(
                git=git,
                run_id=run_id,
                base_sha=base_sha,
                work_package=work_package,
                requirement_bundle=requirement_bundle,
                patch_root=patch_root,
                attempt=attempt,
                failure_context=failure_context,
            )
            if last.agent_run["status"] == "completed" and last.patch_set:
                return last
        return last

    def _integrate_and_validate(
        self,
        *,
        project_id: str,
        run_id: str,
        git: GitRuntime,
        base_sha: str,
        patch_sets: list[dict],
        requirement_bundle: dict,
        run_root: Path,
    ) -> dict:
        integration_tree = git.create_worktree(run_id, "integration", "release-candidate", 1, base_sha)
        integration_path = Path(integration_tree["path"])
        self.storage.save_worktree(
            {
                "id": str(uuid.uuid4()),
                "run_id": run_id,
                "work_package_id": "integration",
                "role": "chief",
                "attempt": 1,
                "path": integration_tree["path"],
                "branch": integration_tree["branch"],
                "base_sha": base_sha,
                "status": "created",
                "created_at": utc_now(),
                "cleaned_at": "",
            }
        )
        applied_patch_sets: list[dict] = []
        for patch_set in [item for item in patch_sets if item.get("status") == "captured"]:
            apply_result = git.apply_patch(integration_path, Path(patch_set["patch_path"]))
            step = self.storage.save_integration_step(
                {
                    "run_id": run_id,
                    "step_type": "apply_patch",
                    "status": apply_result["status"],
                    "work_package_id": patch_set["work_package_id"],
                    "patch_set_id": patch_set["id"],
                    "message": apply_result.get("stderr") or apply_result.get("stdout") or apply_result["status"],
                    "payload": {"patch_path": patch_set["patch_path"]},
                }
            )
            if not apply_result["ok"]:
                self.storage.update_patch_set(patch_set["id"], status="conflict", error=step["message"])
                self._save_repair(project_id, run_id, "patch_conflict", patch_set["role"], step["message"])
                return self._block_run(project_id, run_id, f"Patch conflict while applying {patch_set['work_package_id']}.")
            applied_patch_sets.append(patch_set)
            self.storage.update_patch_set(patch_set["id"], status="applied")
        current = self.storage.get_run(run_id)
        self.storage.update_run(
            run_id,
            continuation_state={
                **current.get("continuation_state", {}),
                "schema_version": "2.2.0",
                "checkpoint": "integration_complete",
                "state": "integration",
                "next_action": "test_gate",
                "applied_patch_ids": [item["id"] for item in applied_patch_sets],
                "pending_patch_ids": [
                    item["id"]
                    for item in patch_sets
                    if item.get("status") == "captured" and item["id"] not in {applied["id"] for applied in applied_patch_sets}
                ],
                "patch_paths": [
                    item["patch_path"]
                    for item in self.storage.list_patch_sets(run_id)
                    if item.get("patch_path")
                ],
                "summary": "Patch integration completed.",
            },
        )

        test_results = self.test_runner.run(integration_path, repair_round=0)
        for result in test_results:
            self.storage.save_test_run(run_id, result)
        current = self.storage.get_run(run_id)
        self.storage.update_run(
            run_id,
            continuation_state={
                **current.get("continuation_state", {}),
                "schema_version": "2.2.0",
                "checkpoint": "test_gate_complete",
                "state": "test_gate",
                "next_action": "quality_gate",
                "summary": "Executable test gate completed.",
            },
        )
        if not test_results or any(item["status"] != "passed" for item in test_results):
            failure_context = "\n".join(
                f"{item.get('command')}: {item.get('stderr') or item.get('stdout')}" for item in test_results
            )
            self._save_repair(project_id, run_id, "tests_failed", "qa-automation", failure_context)
            repaired = self._run_test_repair_loop(
                project_id=project_id,
                run_id=run_id,
                git=git,
                base_sha=base_sha,
                integration_path=integration_path,
                requirement_bundle=requirement_bundle,
                patch_sets=applied_patch_sets,
                run_root=run_root,
                failure_context=failure_context,
            )
            if not repaired["ok"]:
                return self._block_run(project_id, run_id, "Tests failed after repair attempts.")

        quality = evaluate_project_quality(integration_path, requirement_bundle)
        quality["run_id"] = run_id
        quality["id"] = f"VAL-{uuid.uuid4().hex[:12]}"
        write_quality_report(integration_path, quality)
        self.storage.save_validation(run_id, quality)
        history = list(self.storage.get_run(run_id).get("quality_gate_history", []))
        history.append(
            {
                "gate": quality.get("gate"),
                "status": quality.get("status"),
                "score": quality.get("score"),
                "anti_shit_score": quality.get("anti_shit_score"),
                "created_at": quality.get("created_at"),
                "finding_count": len(quality.get("findings", [])),
            }
        )
        self.storage.update_run(
            run_id,
            quality_gate_history=history,
            continuation_state={
                **self.storage.get_run(run_id).get("continuation_state", {}),
                "schema_version": "2.2.0",
                "checkpoint": "quality_gate_complete",
                "state": "quality_gate",
                "next_action": "release_candidate" if quality["status"] == "passed" else "repair_quality",
                "summary": quality.get("summary", ""),
            },
        )
        if quality["status"] != "passed":
            self._save_repair(project_id, run_id, "quality_gates_failed", "chief", quality["summary"])
            return self._block_run(project_id, run_id, "Quality gates failed after integration.")

        final_sha = git.commit_all(integration_path, f"v2 release candidate {run_id[:8]}")
        release_patch_path = run_root / "release-candidate.patch"
        release_info = git.write_patch(integration_path, base_sha, release_patch_path)
        manifest = {
            "schema_version": "2.0.0",
            "kind": "v2-release-candidate-manifest",
            "run_id": run_id,
            "base_sha": base_sha,
            "final_sha": final_sha,
            "release_patch_path": str(release_patch_path),
            "changed_files": release_info["files_changed"],
            "patch_sets": [item["id"] for item in applied_patch_sets],
            "test_runs": self.storage.list_test_runs(run_id),
            "quality_gate": {
                "status": quality.get("status"),
                "score": quality.get("score"),
                "anti_shit_score": quality.get("anti_shit_score"),
            },
        }
        (run_root / "release-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=True), encoding="utf-8")
        candidate = self._create_release_candidate(project_id, run_id, quality, manifest)
        if candidate.get("decision") != "GO":
            summary = "Release candidate safeguards blocked automatic GO."
            self.storage.update_run(
                run_id,
                status="blocked",
                chief_summary=summary,
            continuation_state={
                **self.storage.get_run(run_id).get("continuation_state", {}),
                "schema_version": "2.2.0",
                "checkpoint": "release_candidate_complete",
                "state": "blocked",
                "next_action": "repair_release_safeguards",
                "release_manifest_path": str(run_root / "release-manifest.json"),
                "release_patch_path": str(release_patch_path),
                "summary": summary,
            },
            )
            self.storage.update_project(project_id, status="blocked")
            self.storage.add_event(
                run_id,
                "error",
                "chief",
                summary,
                {"release_candidate_id": candidate["id"], "blockers": candidate.get("blockers", [])},
            )
            return {
                "run": self.storage.get_run(run_id),
                "quality": quality,
                "repair_tasks": self.storage.list_project_repairs(project_id),
                "release_candidate": candidate,
            }
        self.storage.update_run(
            run_id,
            status="release_candidate_ready",
            chief_summary="Chief produced a tested release candidate.",
            continuation_state={
                **self.storage.get_run(run_id).get("continuation_state", {}),
                "schema_version": "2.2.0",
                "checkpoint": "release_candidate_complete",
                "state": "release_candidate_ready",
                "next_action": "approval",
                "release_manifest_path": str(run_root / "release-manifest.json"),
                "release_patch_path": str(release_patch_path),
                "summary": "Release candidate is ready for approval.",
            },
        )
        self.storage.update_project(project_id, status="release_candidate_ready")
        self.storage.add_event(
            run_id,
            "info",
            "chief",
            "V2 real execution finished with GO release candidate.",
            {"release_candidate_id": candidate["id"], "changed_files": release_info["files_changed"]},
        )
        return {
            "run": self.storage.get_run(run_id),
            "quality": quality,
            "repair_tasks": self.storage.list_project_repairs(project_id),
            "release_candidate": candidate,
        }

    def _run_test_repair_loop(
        self,
        *,
        project_id: str,
        run_id: str,
        git: GitRuntime,
        base_sha: str,
        integration_path: Path,
        requirement_bundle: dict,
        patch_sets: list[dict],
        run_root: Path,
        failure_context: str,
    ) -> dict:
        qa_package = {
            "id": "WP-REPAIR-tests",
            "title": "Repair failing test loop",
            "owner_role": "qa-automation",
            "subsystem_id": "verification",
            "requirement_ids": [item.get("id") for item in requirement_bundle.get("atoms", [])],
            "dependencies": [],
            "outputs": ["tests", "reports/v2/qa-evidence.json"],
            "status": "ready",
        }
        for repair_round in range(1, self.max_repair_rounds + 1):
            current = self.storage.get_run(run_id)
            self.storage.update_run(
                run_id,
                repair_round_count=max(int(current.get("repair_round_count", 0) or 0), repair_round),
                continuation_state={
                    **current.get("continuation_state", {}),
                    "schema_version": "2.2.0",
                    "checkpoint": "test_repair_started",
                    "state": "repairing",
                    "next_action": "repair_tests",
                    "summary": f"Running test repair round {repair_round}.",
                },
            )
            repair_base_sha = git.commit_all(integration_path, f"v2 repair base {run_id[:8]} round {repair_round}")
            attempt = self._run_agent_attempt(
                git=git,
                run_id=run_id,
                base_sha=repair_base_sha,
                work_package=qa_package,
                requirement_bundle=requirement_bundle,
                patch_root=run_root / "patches",
                attempt=repair_round + 1,
                failure_context=failure_context,
            )
            if not attempt.patch_set or attempt.patch_set.get("status") != "captured":
                continue
            apply_result = git.apply_patch(integration_path, Path(attempt.patch_set["patch_path"]))
            self.storage.save_integration_step(
                {
                    "run_id": run_id,
                    "step_type": "repair_patch",
                    "status": apply_result["status"],
                    "work_package_id": attempt.patch_set["work_package_id"],
                    "patch_set_id": attempt.patch_set["id"],
                    "message": apply_result.get("stderr") or apply_result.get("stdout") or apply_result["status"],
                    "payload": {"repair_round": repair_round},
                }
            )
            if not apply_result["ok"]:
                self.storage.update_patch_set(attempt.patch_set["id"], status="conflict", error=apply_result["stderr"])
                continue
            patch_sets.append(attempt.patch_set)
            self.storage.update_patch_set(attempt.patch_set["id"], status="applied")
            current = self.storage.get_run(run_id)
            self.storage.update_run(
                run_id,
                continuation_state={
                    **current.get("continuation_state", {}),
                    "schema_version": "2.2.0",
                    "checkpoint": "patch_capture_complete",
                    "patch_paths": [
                        item["patch_path"]
                        for item in self.storage.list_patch_sets(run_id)
                        if item.get("patch_path")
                    ],
                },
            )
            results = self.test_runner.run(integration_path, repair_round=repair_round)
            for result in results:
                self.storage.save_test_run(run_id, result)
            if results and all(item["status"] == "passed" for item in results):
                return {"ok": True}
            failure_context = "\n".join(f"{item.get('command')}: {item.get('stderr') or item.get('stdout')}" for item in results)
        return {"ok": False}

    def _create_release_candidate(self, project_id: str, run_id: str, quality: dict, manifest: dict) -> dict:
        blockers = [item for item in quality.get("findings", []) if item.get("severity") in {"critical", "high"}]
        run = self.storage.get_run(run_id)
        requirement_bundle = self.storage.get_requirement_bundle(project_id)
        coverage = requirement_bundle.get("coverage", {})
        test_count = len(manifest.get("test_runs", []))
        safeguards: list[dict] = []
        if coverage.get("must_total", 0) and coverage.get("must_coverage_percent", 0) < 100:
            safeguards.append(
                {
                    "code": "release_must_mapping_gap",
                    "severity": "critical",
                    "category": "release-safeguard",
                    "message": "Not all Must requirements map to work packages.",
                    "evidence": coverage.get("uncovered_must", []),
                    "owner": "chief",
                    "repair_role": "chief",
                }
            )
        if test_count <= 0:
            safeguards.append(
                {
                    "code": "release_missing_test_evidence",
                    "severity": "critical",
                    "category": "release-safeguard",
                    "message": "Release candidate has no executable test evidence.",
                    "evidence": [],
                    "owner": "qa-automation",
                    "repair_role": "qa-automation",
                }
            )
        if quality.get("release_candidate_allowed") is not True:
            safeguards.append(
                {
                    "code": "release_quality_not_allowed",
                    "severity": "critical",
                    "category": "release-safeguard",
                    "message": "Quality gate did not explicitly allow release candidate creation.",
                    "evidence": [quality.get("status", "")],
                    "owner": "chief",
                    "repair_role": "chief",
                }
            )
        blockers = blockers + safeguards
        allowed = not blockers and quality.get("status") == "passed" and test_count > 0
        candidate = {
            "id": str(uuid.uuid4()),
            "project_id": project_id,
            "run_id": run_id,
            "status": "ready" if allowed else "blocked",
            "decision": "GO" if allowed else "NO_GO",
            "gate_score": int(quality.get("score", 0)),
            "blockers": blockers,
            "created_at": utc_now(),
            "quality_gate": {
                "status": quality.get("status"),
                "score": quality.get("score"),
                "anti_shit_score": quality.get("anti_shit_score"),
            },
            "release_patch_path": manifest["release_patch_path"],
            "manifest_path": str(Path(manifest["release_patch_path"]).parent / "release-manifest.json"),
            "base_sha": manifest["base_sha"],
            "final_sha": manifest["final_sha"],
            "changed_files": manifest["changed_files"],
            "test_evidence_count": test_count,
            "automation": {
                "decomposition_score": run.get("decomposition_score", 0),
                "autonomy_level": run.get("autonomy_level", 2),
                "repair_round_count": run.get("repair_round_count", 0),
                "quality_gate_history": run.get("quality_gate_history", []),
                "continuation_state": run.get("continuation_state", {}),
            },
            "release_safeguards": {
                "must_requirements_mapped": coverage.get("must_total", 0) == 0 or coverage.get("must_coverage_percent", 0) == 100,
                "required_subsystem_gates_passed": quality.get("status") == "passed",
                "tests_exist": test_count > 0,
                "fallback_artifacts_detected": any(
                    item.get("code") == "fallback_or_normalized_artifacts" for item in quality.get("findings", [])
                ),
                "safeguard_findings": safeguards,
            },
        }
        return self.storage.save_release_candidate(candidate)

    def _save_repair(self, project_id: str, run_id: str, finding_code: str, role: str, reason: str) -> dict:
        repair = RepairTask(
            id=str(uuid.uuid4()),
            project_id=project_id,
            run_id=run_id,
            finding_code=finding_code,
            assigned_role=role or "chief",
            status="ready",
            reason=(reason or "")[:4000],
        )
        return self.storage.save_repair_task(repair.to_dict())

    def _block_run(self, project_id: str, run_id: str, reason: str) -> dict:
        quality = {
            "id": f"VAL-{uuid.uuid4().hex[:12]}",
            "run_id": run_id,
            "gate": "v2-execution",
            "status": "failed",
            "score": 0,
            "findings": [
                {
                    "code": "v2_execution_blocked",
                    "severity": "critical",
                    "category": "execution",
                    "message": reason,
                    "evidence": [],
                    "owner": "chief",
                    "repair_role": "chief",
                }
            ],
            "evidence": [],
            "created_at": utc_now(),
            "metrics": {},
            "anti_shit_score": 0,
            "release_candidate_allowed": False,
            "summary": reason,
        }
        self.storage.save_validation(run_id, quality)
        history = list(self.storage.get_run(run_id).get("quality_gate_history", []))
        history.append(
            {
                "gate": quality.get("gate"),
                "status": quality.get("status"),
                "score": quality.get("score"),
                "anti_shit_score": quality.get("anti_shit_score"),
                "created_at": quality.get("created_at"),
                "finding_count": len(quality.get("findings", [])),
            }
        )
        candidate = {
            "id": str(uuid.uuid4()),
            "project_id": project_id,
            "run_id": run_id,
            "status": "blocked",
            "decision": "NO_GO",
            "gate_score": 0,
            "blockers": quality["findings"],
            "created_at": utc_now(),
            "quality_gate": {"status": "failed", "score": 0, "anti_shit_score": 0},
            "release_patch_path": "",
            "manifest_path": "",
            "base_sha": "",
            "final_sha": "",
            "changed_files": [],
            "test_evidence_count": 0,
            "automation": {
                "decomposition_score": self.storage.get_run(run_id).get("decomposition_score", 0),
                "autonomy_level": self.storage.get_run(run_id).get("autonomy_level", 2),
                "repair_round_count": self.storage.get_run(run_id).get("repair_round_count", 0),
                "quality_gate_history": history,
                "continuation_state": self.storage.get_run(run_id).get("continuation_state", {}),
            },
        }
        candidate = self.storage.save_release_candidate(candidate)
        self.storage.update_run(
            run_id,
            status="blocked",
            chief_summary=reason,
            quality_gate_history=history,
            continuation_state={
                **self.storage.get_run(run_id).get("continuation_state", {}),
                "state": "blocked",
                "next_action": "repair",
                "summary": reason,
            },
        )
        self.storage.update_project(project_id, status="blocked")
        self.storage.add_event(run_id, "error", "chief", reason, {"release_candidate_id": candidate["id"]})
        return {
            "run": self.storage.get_run(run_id),
            "quality": quality,
            "repair_tasks": self.storage.list_project_repairs(project_id),
            "release_candidate": candidate,
        }
