from __future__ import annotations

from collections import defaultdict, deque


CHIEF_ROLES = {
    "chief": {
        "label": "Chief Brain",
        "mission": "Owns project intent, DAG control, repair routing, and release candidate decisions.",
    },
    "product-analyst": {
        "label": "Product Analyst",
        "mission": "Turns PRD text into requirement atoms and acceptance contracts.",
    },
    "system-architect": {
        "label": "System Architect",
        "mission": "Owns system boundaries, architecture decisions, and cross-subsystem contracts.",
    },
    "frontend-lead": {
        "label": "Frontend Lead",
        "mission": "Builds user-facing product surfaces without PRD leakage or generic template UI.",
    },
    "backend-lead": {
        "label": "Backend Lead",
        "mission": "Builds APIs, domain services, authorization, and integration boundaries.",
    },
    "ai-ml-engineer": {
        "label": "AI/ML Engineer",
        "mission": "Builds AI capability slices, evaluation interfaces, and explainability hooks.",
    },
    "data-engineer": {
        "label": "Data Engineer",
        "mission": "Builds data ingestion, governance, feature, and retention pipelines.",
    },
    "qa-automation": {
        "label": "QA Automation",
        "mission": "Creates contract-aware executable verification and regression evidence.",
    },
    "security-reviewer": {
        "label": "Security Reviewer",
        "mission": "Finds privacy, tenant isolation, compliance, and abuse risks.",
    },
    "sre-devops": {
        "label": "SRE/DevOps",
        "mission": "Owns production runtime, deployment, observability, and release assembly.",
    },
    "refactor-sheriff": {
        "label": "Refactor Sheriff",
        "mission": "Blocks shit-code, template slop, duplication, oversized files, and drift.",
    },
}


def build_execution_waves(work_packages: list[dict]) -> list[dict]:
    by_id = {item["id"]: dict(item) for item in work_packages}
    indegree = {item_id: 0 for item_id in by_id}
    children: dict[str, list[str]] = defaultdict(list)
    for item in work_packages:
        for dependency in item.get("dependencies", []):
            if dependency not in by_id:
                continue
            indegree[item["id"]] += 1
            children[dependency].append(item["id"])

    ready = deque(sorted([item_id for item_id, value in indegree.items() if value == 0]))
    waves: list[dict] = []
    visited: set[str] = set()
    wave_index = 1
    while ready:
        current_ids = list(ready)
        ready.clear()
        packages = [by_id[item_id] for item_id in current_ids if item_id not in visited]
        if packages:
            waves.append(
                {
                    "id": f"WAVE-{wave_index:03d}",
                    "parallel": len(packages) > 1,
                    "work_package_ids": [item["id"] for item in packages],
                    "roles": sorted({item.get("owner_role", "") for item in packages}),
                }
            )
            wave_index += 1
        for item_id in current_ids:
            if item_id in visited:
                continue
            visited.add(item_id)
            for child in sorted(children.get(item_id, [])):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)

    missing = [item_id for item_id in by_id if item_id not in visited]
    if missing:
        waves.append(
            {
                "id": f"WAVE-{wave_index:03d}",
                "parallel": False,
                "work_package_ids": missing,
                "roles": sorted({by_id[item_id].get("owner_role", "") for item_id in missing}),
                "cycle_warning": True,
            }
        )
    return waves


def build_dag(work_packages: list[dict]) -> dict:
    waves = build_execution_waves(work_packages)
    return {
        "schema_version": "2.0.0",
        "kind": "chief-controlled-dag",
        "control_role": "chief",
        "roles": CHIEF_ROLES,
        "work_packages": work_packages,
        "waves": waves,
        "rules": [
            "Only chief can advance workflow state.",
            "Implementation agents produce patch sets, not final approval.",
            "Fallback or normalized artifacts block release candidates.",
            "Must requirements require code, test, and review evidence.",
        ],
    }


def summarize_chief_plan(requirement_bundle: dict) -> str:
    coverage = requirement_bundle.get("coverage", {})
    packages = requirement_bundle.get("work_packages", [])
    role_count = len({item.get("owner_role", "") for item in packages})
    return (
        "Chief generated a production V2 delivery DAG with "
        f"{len(requirement_bundle.get('atoms', []))} requirement atoms, "
        f"{len(packages)} work packages, {role_count} active roles, and "
        f"{coverage.get('must_coverage_percent', 0)}% Must requirement mapping."
    )
