from __future__ import annotations

from typing import Any


SCALE_ORDER = {"small": 0, "medium": 1, "large": 2, "xlarge_100k": 3}
ORDERED_SCALES = ("small", "medium", "large", "xlarge_100k")


def choose_larger_scale(current: str, candidate: str) -> str:
    current_name = normalize_scale_name(current)
    candidate_name = normalize_scale_name(candidate)
    return candidate_name if SCALE_ORDER[candidate_name] > SCALE_ORDER[current_name] else current_name


def normalize_scale_name(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "": "medium",
        "auto": "medium",
        "tiny": "small",
        "s": "small",
        "m": "medium",
        "l": "large",
        "xlarge": "xlarge_100k",
        "xl": "xlarge_100k",
        "100k": "xlarge_100k",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in SCALE_ORDER else "medium"


def infer_initial_scale(config: dict[str, Any] | None, description: str = "") -> dict[str, Any]:
    config = config or {}
    requested = str(config.get("target_scale") or "auto").strip().lower() or "auto"
    if requested not in {"", "auto"}:
        return {
            "schema_version": "6.3",
            "stage": "initial",
            "requested_scale": requested,
            "selected_scale": normalize_scale_name(requested),
            "confidence": "explicit",
            "auto": False,
            "reasons": ["user_explicit_scale"],
            "signals": {},
        }
    signals = _text_signals(description)
    selected = _scale_from_score(signals["score"], low_confidence_default="medium")
    return {
        "schema_version": "6.3",
        "stage": "initial",
        "requested_scale": "auto",
        "selected_scale": selected,
        "confidence": signals["confidence"],
        "auto": True,
        "reasons": signals["reasons"] or ["auto_low_confidence_default_medium"],
        "signals": signals,
    }


def infer_scale_from_requirements(requirements: dict[str, Any], requirements_text: str = "") -> dict[str, Any]:
    original_signals = _text_signals(requirements_text)
    text = " ".join(
        [
            requirements_text,
            str(requirements.get("summary", "")),
            " ".join(str(item) for item in requirements.get("goals") or []),
            " ".join(str(item) for item in requirements.get("constraints") or []),
            " ".join(str(item) for item in requirements.get("acceptance_criteria") or []),
            " ".join(str(item) for item in requirements.get("risks") or []),
            " ".join(str(item) for item in requirements.get("expected_terms") or []),
        ]
    )
    signals = _text_signals(text)
    list_count = sum(len(requirements.get(key) or []) for key in ("goals", "users", "constraints", "acceptance_criteria", "risks", "expected_terms") if isinstance(requirements.get(key), list))
    signals["list_item_count"] = list_count
    signals["score"] += list_count
    if list_count >= 24:
        signals["reasons"].append("many_requirement_items")
    selected = _scale_from_score(signals["score"], low_confidence_default="medium")
    if selected == "xlarge_100k" and not _requirements_supports_100k(original_signals, signals):
        selected = "large"
        signals["reasons"].append("xlarge_deferred_until_architecture_or_package_evidence")
    if selected in {"large", "xlarge_100k"} and _looks_like_bounded_crud_product(original_signals, signals):
        selected = "medium"
        signals["reasons"].append("bounded_crud_product_capped_medium")
    return {
        "schema_version": "6.3",
        "stage": "requirements",
        "selected_scale": selected,
        "confidence": signals["confidence"],
        "reasons": signals["reasons"] or ["requirements_low_confidence_default_medium"],
        "signals": signals,
    }


def _requirements_supports_100k(original_signals: dict[str, Any], combined_signals: dict[str, Any]) -> bool:
    original_reasons = set(original_signals.get("reasons") or [])
    original_hits = original_signals.get("keyword_hits") or {}
    if int(original_signals.get("char_count") or 0) >= 8000:
        return True
    if "very_large_requirements_text" in original_reasons or "large_requirements_text" in original_reasons:
        return True
    if int(original_hits.get("scale") or 0) > 0 and int(original_hits.get("operations") or 0) > 0 and int(original_hits.get("security") or 0) > 0:
        return True
    return False


def _looks_like_bounded_crud_product(original_signals: dict[str, Any], combined_signals: dict[str, Any]) -> bool:
    original_text = str(original_signals.get("text") or "")
    combined_text = str(combined_signals.get("text") or "")
    text = f"{original_text} {combined_text}".lower()
    crud_terms = (
        "crud",
        "dashboard",
        "login",
        "logout",
        "table",
        "form",
        "pagination",
        "search",
        "filter",
        "edit",
        "delete",
        "add",
        "库存",
        "产品",
        "入库",
        "出库",
        "查询",
        "记录",
        "分页",
        "搜索",
        "登录",
    )
    enterprise_terms = (
        "multi-tenant",
        "multi tenant",
        "distributed",
        "microservice",
        "multi-region",
        "sharding",
        "kubernetes",
        "payment",
        "billing",
        "warehouse",
        "sso",
        "oauth",
        "compliance",
        "audit trail",
        "100k",
        "tenants",
        "高并发",
        "分布式",
        "多租户",
        "微服务",
    )
    crud_hits = sum(text.count(term) for term in crud_terms)
    enterprise_hits = sum(text.count(term) for term in enterprise_terms)
    original_chars = int(original_signals.get("char_count") or 0)
    combined_hits = combined_signals.get("keyword_hits") or {}
    has_scale_pressure = int(combined_hits.get("scale") or 0) > 0 or enterprise_hits >= 2
    return crud_hits >= 8 and original_chars < 6000 and enterprise_hits == 0 and not has_scale_pressure


def infer_scale_from_architecture(design: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
    directories = layout.get("directories") or []
    entrypoints = layout.get("entrypoints") or []
    module_boundaries = design.get("module_boundaries") or []
    contracts = design.get("integration_contracts") or []
    text = " ".join(
        [
            str(design.get("architecture_summary", "")),
            " ".join(str(item) for item in design.get("technology_choices") or []),
            " ".join(str(item) for item in directories),
            " ".join(str(item) for item in entrypoints),
            " ".join(str(item) for item in module_boundaries),
            " ".join(str(item) for item in contracts),
        ]
    )
    signals = _text_signals(text)
    structure_count = len(directories) + len(entrypoints) + len(module_boundaries) + len(contracts)
    signals["structure_count"] = structure_count
    signals["score"] += structure_count * 2
    if len(contracts) >= 8:
        signals["score"] += 12
        signals["reasons"].append("many_integration_contracts")
    if len(module_boundaries) >= 8:
        signals["score"] += 10
        signals["reasons"].append("many_module_boundaries")
    selected = _scale_from_score(signals["score"], low_confidence_default="medium")
    return {
        "schema_version": "6.3",
        "stage": "architecture",
        "selected_scale": selected,
        "confidence": signals["confidence"],
        "reasons": signals["reasons"] or ["architecture_low_confidence_default_medium"],
        "signals": signals,
    }


def infer_scale_from_package_plan(plan: dict[str, Any]) -> dict[str, Any]:
    packages = plan.get("packages") or []
    waves = plan.get("waves") or []
    roles = {str(package.get("role") or "") for package in packages}
    dependency_count = sum(len(package.get("depends_on") or []) for package in packages)
    path_count = sum(len(package.get("allowed_paths") or []) for package in packages)
    score = len(packages) * 5 + len(waves) * 4 + len(roles) * 4 + dependency_count * 3 + path_count
    reasons: list[str] = []
    if len(packages) >= 24:
        reasons.append("package_count_24_plus")
    elif len(packages) >= 12:
        reasons.append("package_count_12_plus")
    if len(waves) >= 8:
        reasons.append("many_waves")
    if dependency_count >= 24:
        reasons.append("dense_package_dependencies")
    if {"security", "qa", "docs", "db", "backend", "frontend"} <= roles:
        score += 10
        reasons.append("full_delivery_role_surface")
    selected = _scale_from_score(score, low_confidence_default="medium")
    return {
        "schema_version": "6.3",
        "stage": "package_planning",
        "selected_scale": selected,
        "confidence": "high" if reasons else "medium",
        "reasons": reasons or ["package_plan_low_confidence_default_medium"],
        "signals": {
            "score": score,
            "package_count": len(packages),
            "wave_count": len(waves),
            "role_count": len(roles),
            "dependency_count": dependency_count,
            "path_count": path_count,
        },
    }


def merge_inference_history(existing: list[dict[str, Any]] | None, item: dict[str, Any]) -> list[dict[str, Any]]:
    history = list(existing or [])
    history.append(item)
    return history[-20:]


def _scale_from_score(score: int, *, low_confidence_default: str = "medium") -> str:
    if score >= 110:
        return "xlarge_100k"
    if score >= 65:
        return "large"
    if score >= 24:
        return "medium"
    return low_confidence_default


def _text_signals(text: str) -> dict[str, Any]:
    normalized = str(text or "").lower()
    char_count = len(normalized)
    line_count = len([line for line in normalized.splitlines() if line.strip()])
    keyword_groups = {
        "security": ("security", "auth", "permission", "audit", "compliance", "tenant", "privacy", "rbac"),
        "scale": ("100k", "large", "enterprise", "high availability", "distributed", "multi-region", "sharding", "performance"),
        "integration": ("api", "webhook", "integration", "sync", "import", "export", "payment", "email", "third-party"),
        "data": ("database", "migration", "analytics", "report", "warehouse", "search", "index", "cache"),
        "product_surface": ("admin", "dashboard", "mobile", "frontend", "backend", "workflow", "notification", "billing"),
        "operations": ("deploy", "docker", "kubernetes", "monitoring", "backup", "rollback", "observability", "slo"),
        "testing": ("test", "qa", "e2e", "load", "stress", "benchmark", "acceptance"),
    }
    hits: dict[str, int] = {}
    reasons: list[str] = []
    score = min(char_count // 450, 45) + min(line_count, 30)
    for group, keywords in keyword_groups.items():
        count = sum(normalized.count(keyword) for keyword in keywords)
        hits[group] = count
        if count:
            score += min(count * 4, 24)
            reasons.append(f"{group}_signals")
    if char_count >= 18000:
        score += 45
        reasons.append("very_large_requirements_text")
    elif char_count >= 8000:
        score += 26
        reasons.append("large_requirements_text")
    elif char_count >= 3000:
        score += 12
        reasons.append("medium_requirements_text")
    confidence = "high" if len(reasons) >= 4 or char_count >= 8000 else ("medium" if reasons or char_count >= 1200 else "low")
    return {
        "score": score,
        "text": normalized,
        "char_count": char_count,
        "line_count": line_count,
        "keyword_hits": hits,
        "confidence": confidence,
        "reasons": reasons,
    }
