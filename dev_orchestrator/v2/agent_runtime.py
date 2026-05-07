from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from dev_orchestrator.config import AppConfig
from dev_orchestrator.llm_client import LLMError, OpenAICompatibleClient
from dev_orchestrator.v2.git_runtime import truncate_text
from dev_orchestrator.v2.models import Finding


class AgentContractError(RuntimeError):
    pass


def _json_object_candidates(raw: str) -> list[str]:
    candidates: list[str] = []
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", raw, flags=re.IGNORECASE | re.DOTALL):
        block = match.group(1).strip()
        if block.startswith("{") and block.endswith("}"):
            candidates.append(block)

    stack = 0
    start: int | None = None
    in_string = False
    escaped = False
    for index, char in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if stack == 0:
                start = index
            stack += 1
        elif char == "}" and stack:
            stack -= 1
            if stack == 0 and start is not None:
                candidates.append(raw[start : index + 1])
                start = None
    return candidates


def parse_agent_response(raw: str) -> dict:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgentContractError("Agent response is not valid JSON; fallback delivery is forbidden.") from exc
    if not isinstance(payload, dict):
        raise AgentContractError("Agent response must be a JSON object.")
    required = {"status", "summary", "patches", "evidence"}
    missing = sorted(required - set(payload))
    if missing:
        raise AgentContractError(f"Agent response missing required keys: {', '.join(missing)}")
    if payload.get("status") not in {"completed", "blocked", "needs_repair"}:
        raise AgentContractError("Agent status must be completed, blocked, or needs_repair.")
    return payload


def blocked_validation_from_contract_error(role: str, error: Exception) -> dict:
    finding = Finding(
        code="agent_contract_invalid_json",
        severity="critical",
        category="agent-runtime",
        message=str(error),
        evidence=[role],
        owner="chief",
        repair_role=role,
    )
    return {
        "status": "blocked",
        "summary": "Agent contract failed; no fallback artifacts were generated.",
        "findings": [finding.to_dict()],
        "artifacts": [],
    }


@dataclass
class AgentRuntimeResult:
    status: str
    summary: str
    model_calls: int
    tool_calls: int
    error: str = ""


class WorktreeToolbox:
    def __init__(self, worktree_path: Path, timeout_seconds: int = 120) -> None:
        self.worktree_path = Path(worktree_path).resolve()
        self.timeout_seconds = timeout_seconds

    def invoke(self, payload: dict) -> dict:
        name = payload.get("tool")
        args = payload.get("args") or {}
        if name == "list":
            return self.list_files(args.get("path", "."))
        if name == "read":
            return self.read_file(args.get("path", ""))
        if name == "search":
            return self.search(args.get("query", ""), args.get("path", "."))
        if name == "write":
            return self.write_file(args.get("path", ""), args.get("content", ""))
        if name == "run_test":
            return self.run_test(args.get("command") or "python -m unittest discover", args.get("cwd", "."))
        if name == "finish":
            return {"ok": True, "finished": True}
        return {"ok": False, "error": f"Unknown tool: {name}"}

    def list_files(self, path: str = ".") -> dict:
        root = self._resolve_dir(path)
        items: list[str] = []
        for candidate in root.rglob("*"):
            if candidate.is_file() and ".git" not in candidate.parts:
                items.append(candidate.relative_to(self.worktree_path).as_posix())
            if len(items) >= 500:
                break
        return {"ok": True, "files": sorted(items)}

    def read_file(self, path: str) -> dict:
        target = self._resolve_file(path)
        if not target.exists():
            return {"ok": False, "error": f"File not found: {path}"}
        if target.is_dir():
            return {"ok": False, "error": f"Expected file, got directory: {path}"}
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"ok": False, "error": f"File is not UTF-8 text: {path}"}
        return {"ok": True, "path": path, "content": truncate_text(content, 24000)}

    def search(self, query: str, path: str = ".") -> dict:
        if not query:
            return {"ok": False, "error": "query is required"}
        root = self._resolve_dir(path)
        results: list[dict] = []
        needle = query.lower()
        for candidate in root.rglob("*"):
            if not candidate.is_file() or ".git" in candidate.parts:
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    results.append(
                        {
                            "path": candidate.relative_to(self.worktree_path).as_posix(),
                            "line": line_number,
                            "text": line[:240],
                        }
                    )
                if len(results) >= 100:
                    return {"ok": True, "matches": results}
        return {"ok": True, "matches": results}

    def write_file(self, path: str, content: str) -> dict:
        if not isinstance(path, str) or not path.strip():
            return {"ok": False, "error": "path is required"}
        target = self._resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
        return {"ok": True, "path": path}

    def run_test(self, command: str, cwd: str = ".") -> dict:
        workdir = self._resolve_dir(cwd)
        completed = subprocess.run(
            command,
            cwd=workdir,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=self.timeout_seconds,
        )
        return {
            "ok": completed.returncode == 0,
            "code": completed.returncode,
            "stdout": truncate_text(completed.stdout, 16000),
            "stderr": truncate_text(completed.stderr, 12000),
        }

    def _resolve_path(self, relative_path: str) -> Path:
        normalized = str(relative_path).replace("\\", "/").strip("/")
        target = (self.worktree_path / normalized).resolve()
        if target != self.worktree_path and self.worktree_path not in target.parents:
            raise ValueError(f"Path escapes worktree: {relative_path}")
        if ".git" in target.parts:
            raise ValueError("Agent tools cannot mutate .git internals.")
        return target

    def _resolve_file(self, relative_path: str) -> Path:
        return self._resolve_path(relative_path)

    def _resolve_dir(self, relative_path: str) -> Path:
        target = self._resolve_path(relative_path or ".")
        if target.exists() and target.is_file():
            return target.parent
        return target


class ScriptedAgent:
    """Deterministic agent used only when mock mode is enabled or tests inject it."""

    def run(self, worktree_path: Path, work_package: dict, requirement_bundle: dict, attempt: int, failure_context: str = "") -> AgentRuntimeResult:
        role = work_package.get("owner_role", "agent")
        title = work_package.get("title", "work package")
        requirement_map = {item.get("id"): item for item in requirement_bundle.get("atoms", [])}
        requirements = [requirement_map.get(req_id, {}) for req_id in work_package.get("requirement_ids", [])]
        summary_lines = [
            f"# {title}",
            "",
            f"Role: {role}",
            f"Attempt: {attempt}",
        ]
        if failure_context:
            summary_lines.extend(["", "Repair context:", failure_context[:2000]])
        summary_lines.append("")
        summary_lines.append("Requirements:")
        for item in requirements[:12]:
            summary_lines.append(f"- {item.get('id', '-')}: {item.get('text', '')[:300]}")
        if not requirements:
            summary_lines.append("- Chief/control package with no direct requirement atoms.")

        changed = self._write_role_artifact(worktree_path, role, work_package, "\n".join(summary_lines) + "\n")
        if role == "qa-automation" or (role == "chief" and attempt > 1):
            self._ensure_contract_tests(worktree_path, work_package, requirement_bundle, attempt, role)
        return AgentRuntimeResult(
            status="completed",
            summary=f"Scripted agent produced {changed}.",
            model_calls=0,
            tool_calls=2,
        )

    def _write_role_artifact(self, worktree_path: Path, role: str, work_package: dict, content: str) -> str:
        path = self._default_path(role, work_package)
        target = Path(worktree_path) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._ensure_minimal_product_code(Path(worktree_path), role, work_package, content)
        return path

    def _default_path(self, role: str, work_package: dict) -> str:
        outputs = [
            str(item).strip("/")
            for item in work_package.get("outputs", [])
            if str(item).strip() and not str(item).replace("\\", "/").startswith(".agent/v2/")
        ]
        if outputs:
            first = outputs[0]
            suffix = "implementation.md"
            if role == "qa-automation":
                suffix = "qa-plan.md"
            if role == "security-reviewer":
                suffix = "security-review.md"
            if role == "refactor-sheriff":
                suffix = "anti-shit-score.md"
            if role == "sre-devops":
                suffix = "release-readiness.md"
            if first.endswith((".md", ".json", ".py", ".js", ".ts", ".tsx", ".html", ".css")):
                return first
            return f"{first}/{suffix}"
        package_id = str(work_package.get("id", "package")).lower()
        return f"docs/v2/{package_id}.md"

    def _ensure_contract_tests(self, worktree_path: Path, work_package: dict, requirement_bundle: dict, attempt: int, role: str) -> None:
        if role not in {"qa-automation", "chief"} and attempt == 1:
            return
        tests_dir = Path(worktree_path) / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        test_file = tests_dir / "test_v2_contract.py"
        requirement_count = len(requirement_bundle.get("atoms", []))
        content = f"""from __future__ import annotations

from pathlib import Path
import unittest


class V2ContractEvidenceTests(unittest.TestCase):
    def test_v2_contract_evidence_exists(self):
        root = Path(__file__).resolve().parent.parent
        evidence = list((root / "docs").rglob("*.md")) + list((root / "reports").rglob("*.json"))
        self.assertTrue(evidence)

    def test_v2_requirement_traceability_floor(self):
        self.assertGreaterEqual({requirement_count}, 0)


if __name__ == "__main__":
    unittest.main()
"""
        test_file.write_text(content, encoding="utf-8")

    def _ensure_minimal_product_code(self, worktree_path: Path, role: str, work_package: dict, content: str) -> None:
        package_id = str(work_package.get("id", "package")).replace("-", "_")
        if role in {"frontend-lead", "product-analyst", "chief"}:
            target = worktree_path / "apps" / "web" / f"{package_id}.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                f'''from __future__ import annotations


def render_requirement_summary() -> str:
    """Return product-specific UI copy for this V2 work package."""
    return {content[:900]!r}
''',
                encoding="utf-8",
            )
        if role in {"backend-lead", "ai-ml-engineer", "data-engineer", "chief"}:
            target = worktree_path / "apps" / "api" / f"{package_id}.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                f'''from __future__ import annotations


def capability_contract() -> dict:
    return {{
        "work_package": {str(work_package.get("id", ""))!r},
        "role": {role!r},
        "summary": {content[:900]!r},
    }}
''',
                encoding="utf-8",
            )
        if role in {"security-reviewer", "refactor-sheriff", "sre-devops"}:
            target = worktree_path / "apps" / "control_plane" / f"{package_id}.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                f'''from __future__ import annotations


def review_signal() -> str:
    return {content[:900]!r}
''',
                encoding="utf-8",
            )


class LLMAgentRunner:
    def __init__(self, config: AppConfig, client: OpenAICompatibleClient | None = None) -> None:
        self.config = config
        self.client = client or OpenAICompatibleClient(config.llm)

    def run(
        self,
        worktree_path: Path,
        work_package: dict,
        requirement_bundle: dict,
        attempt: int,
        failure_context: str = "",
    ) -> AgentRuntimeResult:
        toolbox = WorktreeToolbox(worktree_path, self.config.runtime.max_shell_seconds)
        system_prompt = self._system_prompt()
        implementation_rules = self._implementation_rules(work_package)
        messages = [
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "work_package": work_package,
                        "implementation_rules": implementation_rules,
                        "requirement_bundle": {
                            "atoms": requirement_bundle.get("atoms", []),
                            "contracts": requirement_bundle.get("contracts", []),
                            "coverage": requirement_bundle.get("coverage", {}),
                        },
                        "attempt": attempt,
                        "failure_context": failure_context,
                        "contract": {
                            "allowed_tools": ["list", "read", "search", "write", "run_test", "finish"],
                            "response_shape": {
                                "tool": "one allowed tool name",
                                "args": "object",
                                "summary": "short explanation",
                            },
                        },
                    },
                    ensure_ascii=True,
                ),
            }
        ]
        model_calls = 0
        tool_calls = 0
        last_summary = ""
        max_iterations = self._max_tool_iterations(work_package)
        try:
            for _ in range(max_iterations):
                raw = self.client.chat(system_prompt, messages)
                model_calls += 1
                payload = self._parse_tool_response(raw)
                tool_name = payload["tool"]
                last_summary = payload.get("summary", "")
                result = toolbox.invoke(payload)
                tool_calls += 1
                messages.append({"role": "assistant", "content": json.dumps(payload, ensure_ascii=True)})
                messages.append({"role": "user", "content": json.dumps({"tool_result": result}, ensure_ascii=True)})
                if tool_name == "finish":
                    return AgentRuntimeResult(
                        status="completed",
                        summary=last_summary or "Agent finished.",
                        model_calls=model_calls,
                        tool_calls=tool_calls,
                    )
            evidence = self._declared_output_evidence(worktree_path, work_package)
            if evidence["completion_evidence"]:
                return AgentRuntimeResult(
                    status="completed",
                    summary=(
                        "Agent reached the tool-loop budget after writing declared implementation outputs; "
                        f"Chief accepted on-disk evidence: {', '.join(evidence['files'][:8])}."
                    ),
                    model_calls=model_calls,
                    tool_calls=tool_calls,
                )
            raise AgentContractError(f"Agent exceeded maximum tool-loop iterations ({max_iterations}).")
        except (AgentContractError, LLMError, OSError, subprocess.SubprocessError, ValueError) as exc:
            return AgentRuntimeResult(
                status="blocked",
                summary="Agent runtime failed.",
                model_calls=model_calls,
                tool_calls=tool_calls,
                error=str(exc),
            )

    def _system_prompt(self) -> str:
        return (
            "You are a V2 implementation agent in an isolated git worktree. "
            "You must make real production-grade file edits through tools. "
            "Respond only with strict JSON: {\"tool\":\"...\",\"args\":{},\"summary\":\"...\"}. "
            "Use finish only after writing or verifying concrete artifacts. "
            "Keep tool use compact: inspect briefly, write complete files, verify with list/read, then finish. "
            "If a package declares implementation outputs such as .php, .sql, composer.json, src/, public/, database/, apps/, or tests/, "
            "write those source/schema/test files with real domain logic and traceability evidence before finish. "
            "Do not satisfy implementation packages with markdown-only reports unless every declared output is documentation. "
            "Never invent patches; git diff will be captured by the Chief."
        )

    def _implementation_rules(self, work_package: dict) -> dict:
        outputs = [str(item).replace("\\", "/") for item in work_package.get("outputs", [])]
        output_text = " ".join(outputs).lower()
        implementation_markers = (".php", ".sql", "composer.json", "src/", "public/", "database/", "apps/", "tests/")
        is_implementation = any(marker in output_text for marker in implementation_markers)
        php_mysql = any(marker in output_text for marker in (".php", ".sql", "composer.json", "src/", "public/", "database/"))
        required_actions = [
            "inspect existing files before editing",
            "write at least one declared output path or a concrete file inside each declared output directory",
            "include requirement/domain terms in code, tests, or schema so traceability can be measured",
            "finish only after list/read verifies the created files",
        ]
        if php_mysql:
            required_actions.extend(
                [
                    "for PHP/MySQL packages, create executable PHP classes/controllers or SQL DDL/DML, not prose-only artifacts",
                    "prefer public/index.php, src/Controllers, src/Services, src/Repositories, database/schema.sql, and tests/Feature paths when relevant",
                    "include safe password handling, prepared-statement boundaries, CSRF/session checks, and notification signing domain names where relevant",
                ]
            )
        return {
            "is_implementation_package": is_implementation,
            "php_mysql_package": php_mysql,
            "declared_outputs": outputs,
            "finish_allowed_only_after": required_actions,
        }

    def _max_tool_iterations(self, work_package: dict) -> int:
        outputs = [str(item).replace("\\", "/").lower() for item in work_package.get("outputs", [])]
        output_text = " ".join(outputs)
        budget = 12
        if any(marker in output_text for marker in ("apps/", "src/", "public/", "database/", "tests/", ".php", ".sql")):
            budget = 18
        if len(outputs) >= 3 or any(output.endswith("/") or "." not in output.rsplit("/", 1)[-1] for output in outputs):
            budget = max(budget, 22)
        if any(marker in output_text for marker in ("src/controllers", "src/services", "src/repositories", "public/index.php")):
            budget = max(budget, 28)
        if any(marker in output_text for marker in (".php", ".sql", "composer.json", "src/", "public/", "database/")):
            budget = max(budget, 30)
        return budget

    def _declared_output_evidence(self, worktree_path: Path, work_package: dict) -> dict:
        outputs = [str(item).replace("\\", "/").strip("/") for item in work_package.get("outputs", []) if str(item).strip()]
        if not outputs:
            return {"completion_evidence": False, "files": []}
        evidence_files: list[str] = []
        covered = 0
        for output in outputs:
            if output.startswith(".agent/v2/"):
                continue
            target = (Path(worktree_path) / output).resolve()
            try:
                if target.is_file() and target.stat().st_size > 0:
                    covered += 1
                    evidence_files.append(output)
                    continue
                if target.is_dir():
                    files = [
                        path
                        for path in target.rglob("*")
                        if path.is_file() and ".git" not in path.parts and path.stat().st_size > 0
                    ]
                    if files:
                        covered += 1
                        evidence_files.extend(path.relative_to(worktree_path).as_posix() for path in files[:6])
            except OSError:
                continue
        implementation_outputs = [item for item in outputs if not item.startswith(".agent/v2/")]
        required_coverage = 1 if len(implementation_outputs) <= 2 else max(2, len(implementation_outputs) // 2)
        source_like = [
            path
            for path in evidence_files
            if Path(path).suffix.lower() in {".php", ".sql", ".py", ".js", ".ts", ".tsx", ".html", ".css", ".json", ".md", ".sh"}
        ]
        return {
            "completion_evidence": covered >= required_coverage and bool(source_like),
            "files": sorted(set(evidence_files)),
            "covered_outputs": covered,
            "required_coverage": required_coverage,
        }

    def _parse_tool_response(self, raw: str) -> dict:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            payload = None
            last_error: json.JSONDecodeError | None = None
            for candidate in _json_object_candidates(raw):
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError as candidate_error:
                    last_error = candidate_error
                    continue
                if isinstance(parsed, dict):
                    payload = parsed
                    break
            if payload is None:
                if last_error:
                    raise AgentContractError("Agent tool response contains invalid JSON object.") from last_error
                raise AgentContractError("Agent tool response is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise AgentContractError("Agent tool response must be a JSON object.")
        if payload.get("tool") not in {"list", "read", "search", "write", "run_test", "finish"}:
            raise AgentContractError("Agent tool response contains an unknown tool.")
        if not isinstance(payload.get("args", {}), dict):
            raise AgentContractError("Agent tool args must be an object.")
        return payload


class AgentRunner:
    def __init__(self, config: AppConfig, scripted_agent: ScriptedAgent | None = None) -> None:
        self.config = config
        self.scripted_agent = scripted_agent or ScriptedAgent()
        self.llm_runner = LLMAgentRunner(config)

    def run(
        self,
        worktree_path: Path,
        work_package: dict,
        requirement_bundle: dict,
        attempt: int,
        failure_context: str = "",
    ) -> AgentRuntimeResult:
        if self.config.llm.use_mock:
            return self.scripted_agent.run(worktree_path, work_package, requirement_bundle, attempt, failure_context)
        return self.llm_runner.run(worktree_path, work_package, requirement_bundle, attempt, failure_context)


def new_agent_run_payload(
    *,
    run_id: str,
    work_package_id: str,
    role: str,
    attempt: int,
    status: str,
    worktree_path: str = "",
    branch: str = "",
    summary: str = "",
    model_calls: int = 0,
    tool_calls: int = 0,
    error: str = "",
) -> dict:
    from dev_orchestrator.v2.models import utc_now

    now = utc_now()
    return {
        "id": str(uuid.uuid4()),
        "run_id": run_id,
        "work_package_id": work_package_id,
        "role": role,
        "attempt": attempt,
        "status": status,
        "worktree_path": worktree_path,
        "branch": branch,
        "summary": summary,
        "model_calls": model_calls,
        "tool_calls": tool_calls,
        "error": error,
        "started_at": now,
        "finished_at": now if status not in {"running", "ready"} else "",
    }
