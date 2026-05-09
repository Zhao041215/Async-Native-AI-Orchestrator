from __future__ import annotations

from dataclasses import asdict, dataclass, replace


DEFAULT_PROVIDER_BODY_LIMIT_BYTES = 950_000


@dataclass(frozen=True)
class AITaskBudget:
    task_kind: str
    max_input_chars: int
    max_output_tokens: int
    timeout_seconds: int
    reasoning_effort: str
    retry_attempts: int
    model_tier: str
    allow_degraded: bool
    critical: bool

    def to_dict(self) -> dict:
        return asdict(self)

    def with_retry_attempts(self, attempts: int) -> "AITaskBudget":
        return replace(self, retry_attempts=max(1, int(attempts or self.retry_attempts)))

    def with_input_limit(self, max_input_chars: int) -> "AITaskBudget":
        return replace(self, max_input_chars=max(1024, int(max_input_chars or self.max_input_chars)))


AI_TASK_BUDGETS: dict[str, AITaskBudget] = {
    "requirements_analysis": AITaskBudget("requirements_analysis", 9000, 1600, 90, "medium", 3, "tier_strong", False, True),
    "architecture_design": AITaskBudget("architecture_design", 12000, 2400, 120, "high", 3, "tier_strong", False, True),
    "package_planning": AITaskBudget("package_planning", 10000, 2200, 90, "medium", 3, "tier_strong", False, True),
    "code_generation": AITaskBudget("code_generation", 16000, 4500, 150, "high", 3, "tier_strong", False, True),
    "test_generation": AITaskBudget("test_generation", 12000, 2600, 120, "medium", 3, "tier_standard", False, True),
    "security_review": AITaskBudget("security_review", 12000, 1800, 90, "medium", 3, "tier_strong", False, True),
    "integration_merge": AITaskBudget("integration_merge", 16000, 3600, 150, "high", 3, "tier_strong", False, True),
    "code_review": AITaskBudget("code_review", 18000, 2600, 120, "high", 3, "tier_strong", False, True),
    "failure_analysis": AITaskBudget("failure_analysis", 14000, 3200, 120, "high", 3, "tier_strong", False, True),
    "release_notes": AITaskBudget("release_notes", 12000, 2200, 75, "medium", 2, "tier_standard", True, True),
    "context_summary": AITaskBudget("context_summary", 8000, 1000, 60, "low", 2, "tier_fast", True, False),
}

AI_TASK_BUDGETS["repair"] = AI_TASK_BUDGETS["failure_analysis"]
AI_TASK_BUDGETS["review"] = AI_TASK_BUDGETS["code_review"]


def get_ai_task_budget(task_kind: str) -> AITaskBudget:
    return AI_TASK_BUDGETS.get(task_kind, AI_TASK_BUDGETS["code_generation"])


def resolve_ai_task_budget(task_kind: str, scale_profile: dict | None = None) -> AITaskBudget:
    budget = get_ai_task_budget(task_kind)
    profile = scale_profile or {}
    profile_limit = int(profile.get("context_budget_chars") or budget.max_input_chars)
    target_hint = int(profile.get("target_loc_hint") or 0)
    expanded_limit = _profile_task_input_limit(budget.task_kind, profile_limit, target_hint)
    attempts = max(int(budget.retry_attempts), int(profile.get("ai_retry_attempts") or budget.retry_attempts))
    return budget.with_input_limit(expanded_limit).with_retry_attempts(attempts)


def _profile_task_input_limit(task_kind: str, profile_limit: int, target_hint: int) -> int:
    if target_hint >= 100_000:
        fractions = {
            "requirements_analysis": 0.55,
            "architecture_design": 0.60,
            "package_planning": 0.62,
            "code_generation": 0.46,
            "test_generation": 0.40,
            "security_review": 0.40,
            "integration_merge": 0.46,
            "code_review": 0.48,
            "failure_analysis": 0.42,
            "release_notes": 0.34,
            "context_summary": 0.24,
        }
    elif target_hint >= 60_000:
        fractions = {
            "requirements_analysis": 0.46,
            "architecture_design": 0.52,
            "package_planning": 0.54,
            "code_generation": 0.40,
            "test_generation": 0.34,
            "security_review": 0.34,
            "integration_merge": 0.40,
            "code_review": 0.42,
            "failure_analysis": 0.36,
            "release_notes": 0.30,
            "context_summary": 0.20,
        }
    else:
        return get_ai_task_budget(task_kind).max_input_chars
    return min(profile_limit, max(get_ai_task_budget(task_kind).max_input_chars, int(profile_limit * fractions.get(task_kind, 0.38))))


def list_ai_task_budgets() -> list[dict]:
    seen: set[str] = set()
    budgets = []
    for budget in AI_TASK_BUDGETS.values():
        if budget.task_kind in seen:
            continue
        seen.add(budget.task_kind)
        budgets.append(budget.to_dict())
    return budgets


def ai_policy_payload_limits() -> dict:
    return {
        "schema_version": "6.1",
        "provider_body_limit_bytes": DEFAULT_PROVIDER_BODY_LIMIT_BYTES,
        "token_estimate_ratio": "1 token ~= 4 chars",
        "oversize_policy": "trim_context_then_ai_context_summary_then_recovering_block",
    }


def is_core_task(task_kind: str) -> bool:
    return get_ai_task_budget(task_kind).critical
