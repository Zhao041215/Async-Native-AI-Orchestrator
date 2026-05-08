from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from dev_orchestrator.v4.models import new_id, slugify


TEMPLATE_LEAK_TERMS = (
    "employee",
    "employees",
    "employee_no",
    "personnel",
    "department",
    "hire_date",
    "EMP-1001",
    "Alice Chen",
    "Ben Wang",
    "Cathy Liu",
    "\u5458\u5de5",
    "\u4eba\u5458",
    "\u90e8\u95e8",
)

HR_REQUIREMENT_TERMS = (
    "employee",
    "personnel",
    "hr",
    "human resource",
    "\u5458\u5de5",
    "\u4eba\u5458",
    "\u4eba\u4e8b",
    "\u90e8\u95e8",
)

KNOWLEDGE_TERMS = (
    "knowledge base",
    "knowledge",
    "wiki",
    "article",
    "document",
    "content",
    "kb",
    "\u77e5\u8bc6\u5e93",
    "\u6587\u6863",
    "\u6587\u7ae0",
    "\u68c0\u7d22",
    "\u5185\u5bb9",
)

AI_DATA_TERMS = (
    "rag",
    "embedding",
    "vector",
    "llm",
    "model",
    "\u5411\u91cf",
    "\u5d4c\u5165",
    "\u6a21\u578b",
)


def parse_ai_json(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = str((payload or {}).get("raw_response") or "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}


def build_requirements_understanding(
    *,
    project: dict[str, Any],
    requirement_text: str,
    stack_decision: dict[str, Any],
    ai_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ai_json = parse_ai_json(ai_payload)
    provided = ai_json.get("requirements_understanding") if isinstance(ai_json.get("requirements_understanding"), dict) else {}
    domain_model = ai_json.get("domain_model") if isinstance(ai_json.get("domain_model"), dict) else {}
    text = str(requirement_text or project.get("description") or project.get("title") or project.get("name") or "").strip()
    lowered = text.lower()
    product_kind = str(provided.get("product_kind") or domain_model.get("product_kind") or _detect_product_kind(lowered))
    product_name = _product_name(project, text, product_kind)
    entities = _entities_from_ai(domain_model)
    if not entities:
        entities = _default_entities(product_kind, text)
    workflows = _workflows(product_kind, entities)
    pages = _pages(product_name, entities)
    expected_terms = _expected_terms(product_kind, product_name, entities, workflows)
    allow_hr_terms = product_kind == "personnel_management" or any(term in lowered for term in HR_REQUIREMENT_TERMS)
    ambiguity_questions = []
    if len(text) < 12 and not entities:
        ambiguity_questions.append("Describe the main business object and at least one core workflow.")
    if stack_decision.get("stack_pack") == "" and not stack_decision.get("ok"):
        ambiguity_questions.append("Choose a deployment stack or describe the deployment environment.")
    return {
        "schema_version": "5.0",
        "ok": not ambiguity_questions,
        "product_name": product_name,
        "product_kind": product_kind,
        "summary": provided.get("summary")
        or f"{product_name} is a demand-driven {product_kind.replace('_', ' ')} product generated from the submitted requirements.",
        "target_users": provided.get("target_users") or _target_users(product_kind),
        "primary_goal": provided.get("primary_goal") or _primary_goal(product_kind),
        "business_capabilities": _capabilities(product_kind),
        "entities": entities,
        "workflows": workflows,
        "pages": pages,
        "constraints": {
            "stack_pack": stack_decision.get("stack_pack", ""),
            "deployment_mode": stack_decision.get("deployment_mode", ""),
        },
        "expected_terms": expected_terms,
        "forbidden_leak_terms": [] if allow_hr_terms else list(TEMPLATE_LEAK_TERMS),
        "ambiguity_questions": ambiguity_questions,
    }


def build_solution_graph(
    understanding: dict[str, Any],
    product_contract: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    entities = list(understanding.get("entities") or [])
    packages = [
        _solution_package("WP-UNDERSTAND-010", "Requirements understanding and domain model", "planner", "requirements", "WAVE-001", ["release/.v5/**"], [], 80),
        _solution_package("WP-ARCH-020", "Product architecture, routes, and boundaries", "architect", "architecture", "WAVE-001", ["release/.v5/**", "release/config/**", "release/README.md"], [], 120),
        _solution_package("WP-CONFIG-030", "Environment, auth, security, and deploy substrate", "backend", "config", "WAVE-002", ["release/.env.example", "release/.htaccess", "release/.user.ini", "release/nginx.sample.conf", "release/app/Support/**", "release/config/**"], ["release/database/seeders/**"], 180),
        _solution_package("WP-DATA-040", "Demand-specific database model and seed data", "backend", "data", "WAVE-002", ["release/database/**", "release/config/domain.php", "release/app/Repositories/**"], ["release/assets/**"], 220),
        _solution_package("WP-BACKEND-050", "Demand-specific backend routes and services", "backend", "backend", "WAVE-002", ["release/index.php", "release/app/**", "release/config/domain.php"], ["release/assets/**"], 300),
        _solution_package("WP-UI-060", "Demand-specific browser pages and workflows", "frontend", "frontend", "WAVE-002", ["release/index.php", "release/assets/**", "release/app/Controllers/**", "release/config/domain.php"], ["release/database/**"], 320),
        _solution_package("WP-SECURITY-070", "Auth, CSRF, access control, and sensitive-file protection", "security", "security", "WAVE-003", ["release/.htaccess", "release/.user.ini", "release/nginx.sample.conf", "release/app/**"], ["release/database/seeders/**"], 160),
        _solution_package("WP-TEST-080", "Smoke, route, schema, and anti-template verification", "qa", "test", "WAVE-003", ["release/tests/**", "release/README.md"], ["release/database/migrations/**"], 180),
        _solution_package("WP-RELEASE-090", "Clean release and deployment guide", "release", "release", "WAVE-004", ["release/**"], [], 120),
    ]
    for index, entity in enumerate(entities, start=1):
        package = _solution_package(
            f"WP-ENTITY-{index:03d}",
            f"{entity.get('plural_label', entity.get('label', entity.get('key', 'entity')))} feature slice",
            "backend" if index % 2 else "frontend",
            "feature",
            "WAVE-002",
            ["release/config/domain.php", "release/app/**", "release/database/**", "release/assets/**"],
            [],
            260,
        )
        package["entity_key"] = entity.get("key", "")
        package["subsystem"] = entity.get("key", "domain")
        packages.append(package)
    return {
        "schema_version": "5.0",
        "product": {
            "name": understanding.get("product_name", "V5 Product"),
            "kind": understanding.get("product_kind", "business_app"),
            "summary": understanding.get("summary", ""),
            "stack_pack": product_contract.get("stack_pack", ""),
            "deployment_mode": product_contract.get("deployment_mode", ""),
        },
        "entities": entities,
        "workflows": understanding.get("workflows", []),
        "pages": understanding.get("pages", []),
        "packages": packages,
        "waves": _waves_from_packages(packages),
        "quality_contract": {
            "must_match_requirements": True,
            "must_pass_anti_template_gate": True,
            "must_generate_clean_release": True,
            "effective_loc_target": int(config.get("effective_loc_target") or 0),
        },
    }


def build_template_leak_report(
    release_root: Path,
    requirements_text: str,
    understanding: dict[str, Any] | None,
) -> dict[str, Any]:
    understanding = understanding or {}
    text = _release_text(release_root)
    lowered_release = text.lower()
    lowered_requirements = str(requirements_text or "").lower()
    forbidden_terms = [
        term for term in understanding.get("forbidden_leak_terms", TEMPLATE_LEAK_TERMS) if str(term).strip()
    ]
    leaks = []
    for term in forbidden_terms:
        lowered_term = str(term).lower()
        if lowered_term and lowered_term in lowered_release and lowered_term not in lowered_requirements:
            leaks.append(term)
    expected_terms = [str(term) for term in understanding.get("expected_terms", []) if str(term).strip()]
    missing_expected = []
    for term in expected_terms[:30]:
        lowered_term = term.lower()
        encoded_term = json.dumps(term, ensure_ascii=True).strip('"').lower()
        if len(lowered_term) >= 2 and lowered_term not in lowered_release and encoded_term not in lowered_release:
            missing_expected.append(term)
    return {
        "schema_version": "5.0",
        "ok": not leaks and not missing_expected,
        "leaks": sorted(set(leaks)),
        "missing_expected_terms": sorted(set(missing_expected)),
        "expected_terms": expected_terms,
        "checked_files": _release_file_count(release_root),
    }


def _detect_product_kind(lowered: str) -> str:
    if any(term in lowered for term in HR_REQUIREMENT_TERMS):
        return "personnel_management"
    if any(term in lowered for term in KNOWLEDGE_TERMS):
        return "knowledge_base"
    if any(term in lowered for term in ("crm", "customer", "lead", "\u5ba2\u6237", "\u9500\u552e")):
        return "crm"
    if any(term in lowered for term in ("ticket", "work order", "\u5de5\u5355", "\u5ba2\u670d")):
        return "ticketing"
    if any(term in lowered for term in ("inventory", "stock", "warehouse", "\u5e93\u5b58", "\u4ed3\u5e93")):
        return "inventory"
    return "business_app"


def _product_name(project: dict[str, Any], text: str, product_kind: str) -> str:
    title = str(project.get("title") or "").strip()
    if title:
        return title
    name = str(project.get("name") or "").strip()
    if name and not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        return name
    for line in text.splitlines():
        stripped = line.strip(" #-\t")
        if 4 <= len(stripped) <= 48:
            return stripped
    defaults = {
        "knowledge_base": "Knowledge Base Management",
        "crm": "CRM Management",
        "ticketing": "Ticket Management",
        "inventory": "Inventory Management",
        "personnel_management": "Personnel Management",
    }
    return defaults.get(product_kind, "Demand Driven Product")


def _entities_from_ai(domain_model: dict[str, Any]) -> list[dict[str, Any]]:
    raw_entities = domain_model.get("entities")
    if not isinstance(raw_entities, list):
        return []
    entities = []
    for item in raw_entities[:8]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or item.get("key") or "Record")
        key = slugify(str(item.get("key") or item.get("name") or label), "record").replace("-", "_")
        fields = item.get("fields") if isinstance(item.get("fields"), list) else []
        normalized_fields = [_normalize_field(field) for field in fields if isinstance(field, dict)]
        if not normalized_fields:
            normalized_fields = _record_fields()
        entities.append(
            {
                "key": key,
                "table": key,
                "label": label,
                "plural_label": str(item.get("plural_label") or label),
                "route": slugify(str(item.get("route") or key), key).replace("_", "-"),
                "search_placeholder": str(item.get("search_placeholder") or f"Search {label}"),
                "fields": normalized_fields,
                "seed_records": item.get("seed_records") if isinstance(item.get("seed_records"), list) else [],
            }
        )
    return entities


def _default_entities(product_kind: str, text: str) -> list[dict[str, Any]]:
    if product_kind == "knowledge_base":
        return [
            _entity(
                "articles",
                "knowledge_articles",
                "\u77e5\u8bc6\u6587\u7ae0",
                "\u6587\u7ae0\u7ba1\u7406",
                "articles",
                [
                    _field("title", "\u6807\u9898", "text", required=True, searchable=True, unique=True),
                    _field("summary", "\u6458\u8981", "textarea", searchable=True),
                    _field("content", "\u5185\u5bb9", "textarea", required=True, searchable=True),
                    _field("category", "\u5206\u7c7b", "text", required=True, searchable=True),
                    _field("tags", "\u6807\u7b7e", "text", searchable=True),
                    _field("status", "\u72b6\u6001", "select", required=True, default="draft", options=["draft", "published", "archived"]),
                ],
                [
                    {
                        "title": "\u77e5\u8bc6\u5e93\u4e0a\u7ebf\u6307\u5357",
                        "summary": "\u5e2e\u52a9\u7ba1\u7406\u5458\u5feb\u901f\u7406\u89e3\u77e5\u8bc6\u5e93\u7684\u521d\u59cb\u914d\u7f6e\u3002",
                        "content": "\u8fd9\u662f\u4e00\u7bc7\u7531 V5 \u6839\u636e\u77e5\u8bc6\u5e93\u9700\u6c42\u751f\u6210\u7684\u793a\u4f8b\u6587\u7ae0\u3002",
                        "category": "\u64cd\u4f5c\u624b\u518c",
                        "tags": "\u4e0a\u7ebf,\u90e8\u7f72,\u7ba1\u7406",
                        "status": "published",
                    }
                ],
            ),
            _entity(
                "categories",
                "knowledge_categories",
                "\u77e5\u8bc6\u5206\u7c7b",
                "\u5206\u7c7b\u7ba1\u7406",
                "categories",
                [
                    _field("name", "\u5206\u7c7b\u540d\u79f0", "text", required=True, searchable=True, unique=True),
                    _field("description", "\u5206\u7c7b\u8bf4\u660e", "textarea", searchable=True),
                    _field("sort_order", "\u6392\u5e8f", "number", default="10"),
                ],
                [{"name": "\u64cd\u4f5c\u624b\u518c", "description": "\u9762\u5411\u5185\u90e8\u7528\u6237\u7684\u77e5\u8bc6\u6587\u6863", "sort_order": 10}],
            ),
            _entity(
                "tags",
                "knowledge_tags",
                "\u77e5\u8bc6\u6807\u7b7e",
                "\u6807\u7b7e\u7ba1\u7406",
                "tags",
                [
                    _field("name", "\u6807\u7b7e\u540d\u79f0", "text", required=True, searchable=True, unique=True),
                    _field("description", "\u6807\u7b7e\u8bf4\u660e", "textarea", searchable=True),
                ],
                [{"name": "\u90e8\u7f72", "description": "\u4e0e\u90e8\u7f72\u548c\u4e0a\u7ebf\u76f8\u5173\u7684\u6587\u7ae0"}],
            ),
        ]
    if product_kind == "crm":
        return [
            _entity("customers", "crm_customers", "Customer", "Customers", "customers", _customer_fields(), [{"name": "Acme Inc.", "contact": "Jane", "status": "active"}]),
            _entity("opportunities", "crm_opportunities", "Opportunity", "Opportunities", "opportunities", _opportunity_fields(), []),
        ]
    if product_kind == "ticketing":
        return [_entity("tickets", "service_tickets", "Ticket", "Tickets", "tickets", _ticket_fields(), [{"title": "Initial support request", "priority": "normal", "status": "open"}])]
    if product_kind == "inventory":
        return [_entity("items", "inventory_items", "Item", "Items", "items", _inventory_fields(), [{"name": "Sample Item", "sku": "SKU-001", "status": "active"}])]
    if product_kind == "personnel_management":
        return [_entity("staff_records", "staff_records", "Staff Record", "Staff Records", "staff", _staff_fields(), [])]
    return [_entity("records", "business_records", "Record", "Records", "records", _record_fields(), [{"title": _fallback_record_title(text), "status": "active"}])]


def _entity(key: str, table: str, label: str, plural_label: str, route: str, fields: list[dict[str, Any]], seed_records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "key": key,
        "table": table,
        "label": label,
        "plural_label": plural_label,
        "route": route,
        "search_placeholder": f"Search {plural_label}",
        "fields": fields,
        "seed_records": seed_records,
    }


def _field(name: str, label: str, field_type: str, *, required: bool = False, searchable: bool = False, unique: bool = False, default: str = "", options: list[str] | None = None) -> dict[str, Any]:
    return {
        "name": slugify(name, "field").replace("-", "_"),
        "label": label,
        "type": field_type,
        "required": required,
        "searchable": searchable,
        "unique": unique,
        "default": default,
        "options": options or [],
    }


def _normalize_field(field: dict[str, Any]) -> dict[str, Any]:
    return _field(
        str(field.get("name") or field.get("key") or "field"),
        str(field.get("label") or field.get("name") or "Field"),
        str(field.get("type") or "text"),
        required=bool(field.get("required")),
        searchable=bool(field.get("searchable")),
        unique=bool(field.get("unique")),
        default=str(field.get("default") or ""),
        options=[str(item) for item in field.get("options", [])] if isinstance(field.get("options"), list) else [],
    )


def _record_fields() -> list[dict[str, Any]]:
    return [
        _field("title", "Title", "text", required=True, searchable=True, unique=True),
        _field("description", "Description", "textarea", searchable=True),
        _field("status", "Status", "select", required=True, default="active", options=["active", "archived"]),
    ]


def _customer_fields() -> list[dict[str, Any]]:
    return [
        _field("name", "Customer name", "text", required=True, searchable=True, unique=True),
        _field("contact", "Primary contact", "text", searchable=True),
        _field("phone", "Phone", "text"),
        _field("status", "Status", "select", required=True, default="active", options=["active", "inactive"]),
    ]


def _opportunity_fields() -> list[dict[str, Any]]:
    return [
        _field("title", "Opportunity", "text", required=True, searchable=True),
        _field("customer", "Customer", "text", required=True, searchable=True),
        _field("stage", "Stage", "select", required=True, default="new", options=["new", "qualified", "won", "lost"]),
    ]


def _ticket_fields() -> list[dict[str, Any]]:
    return [
        _field("title", "Title", "text", required=True, searchable=True, unique=True),
        _field("description", "Description", "textarea", required=True, searchable=True),
        _field("priority", "Priority", "select", required=True, default="normal", options=["low", "normal", "high"]),
        _field("status", "Status", "select", required=True, default="open", options=["open", "processing", "closed"]),
    ]


def _inventory_fields() -> list[dict[str, Any]]:
    return [
        _field("name", "Item name", "text", required=True, searchable=True),
        _field("sku", "SKU", "text", required=True, searchable=True, unique=True),
        _field("quantity", "Quantity", "number", default="0"),
        _field("status", "Status", "select", required=True, default="active", options=["active", "disabled"]),
    ]


def _staff_fields() -> list[dict[str, Any]]:
    return [
        _field("name", "Name", "text", required=True, searchable=True),
        _field("role", "Role", "text", searchable=True),
        _field("status", "Status", "select", required=True, default="active", options=["active", "inactive"]),
    ]


def _fallback_record_title(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip(" -#\t")
        if stripped:
            return stripped[:80]
    return "Initial business record"


def _target_users(product_kind: str) -> list[str]:
    if product_kind == "knowledge_base":
        return ["knowledge administrators", "internal readers", "content maintainers"]
    return ["administrators", "operators", "auditors"]


def _primary_goal(product_kind: str) -> str:
    if product_kind == "knowledge_base":
        return "Manage, classify, search, and publish reusable knowledge content."
    return "Operate the submitted business workflow through a deployable admin product."


def _capabilities(product_kind: str) -> list[str]:
    common = ["admin login", "CRUD management", "search", "audit trail", "health check", "single-directory deployment"]
    if product_kind == "knowledge_base":
        return ["article management", "category management", "tag management", "full-text style search", *common]
    return common


def _workflows(product_kind: str, entities: list[dict[str, Any]]) -> list[dict[str, str]]:
    if product_kind == "knowledge_base":
        return [
            {"key": "author_article", "title": "Author and publish knowledge articles"},
            {"key": "classify_content", "title": "Classify content with categories and tags"},
            {"key": "search_knowledge", "title": "Search reusable knowledge from the home and admin views"},
        ]
    return [{"key": f"manage_{entity['key']}", "title": f"Manage {entity.get('plural_label', entity['key'])}"} for entity in entities[:4]]


def _pages(product_name: str, entities: list[dict[str, Any]]) -> list[dict[str, str]]:
    pages = [
        {"key": "home", "title": product_name, "route": "/"},
        {"key": "health", "title": "Health", "route": "/health"},
        {"key": "admin_login", "title": "Admin Login", "route": "/admin/login"},
        {"key": "admin_dashboard", "title": "Dashboard", "route": "/admin/dashboard"},
    ]
    for entity in entities:
        route = str(entity.get("route") or entity.get("key"))
        pages.append({"key": f"{entity['key']}_list", "title": str(entity.get("plural_label") or entity["key"]), "route": f"/{route}"})
    return pages


def _expected_terms(product_kind: str, product_name: str, entities: list[dict[str, Any]], workflows: list[dict[str, str]]) -> list[str]:
    terms = [product_name]
    for entity in entities:
        terms.extend([str(entity.get("label", "")), str(entity.get("plural_label", "")), str(entity.get("route", ""))])
        for field in entity.get("fields", [])[:8]:
            terms.append(str(field.get("label", "")))
    for workflow in workflows:
        terms.append(str(workflow.get("title", "")))
    return [term for term in terms if term]


def _solution_package(
    package_key: str,
    title: str,
    role: str,
    domain: str,
    wave_key: str,
    allowed_paths: list[str],
    forbidden_paths: list[str],
    estimated_loc: int,
) -> dict[str, Any]:
    return {
        "id": new_id(),
        "package_key": package_key,
        "title": title,
        "role": role,
        "domain": domain,
        "subsystem": domain,
        "wave_key": wave_key,
        "depends_on": [],
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
        "estimated_effective_loc": estimated_loc,
    }


def _waves_from_packages(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = sorted({package["wave_key"] for package in packages}, key=lambda item: int(item.split("-")[-1]))
    return [
        {
            "id": new_id(),
            "wave_key": key,
            "sequence": int(key.split("-")[-1]),
            "status": "queued",
            "package_count": sum(1 for package in packages if package["wave_key"] == key),
        }
        for key in keys
    ]


def _release_text(release_root: Path) -> str:
    chunks: list[str] = []
    if not release_root.exists():
        return ""
    for path in sorted(release_root.rglob("*")):
        if path.is_dir() or path.suffix.lower() not in {".php", ".html", ".css", ".js", ".sql", ".md", ".json", ".http"}:
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
    return "\n".join(chunks)


def _release_file_count(release_root: Path) -> int:
    if not release_root.exists():
        return 0
    return sum(1 for path in release_root.rglob("*") if path.is_file())
