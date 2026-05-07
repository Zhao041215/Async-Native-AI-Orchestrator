from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class StackPack:
    id: str
    title: str
    description: str
    deployment_mode: str
    release_root: str
    api_only_default: bool
    home_entry: str
    health_entry: str
    admin_entry: str
    config_strategy: str
    database_strategy: str
    browser_required: bool
    required_files: tuple[str, ...]
    test_commands: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


STACK_PACKS: dict[str, StackPack] = {
    "php_mysql_single_dir": StackPack(
        id="php_mysql_single_dir",
        title="PHP 8.2 + MySQL single directory",
        description="Baota and virtual-host friendly PHP/MySQL product with index.php at release root.",
        deployment_mode="single_directory_upload",
        release_root="release",
        api_only_default=False,
        home_entry="/",
        health_entry="/health",
        admin_entry="/admin/login",
        config_strategy="env_file",
        database_strategy="migration_plus_seed",
        browser_required=True,
        required_files=(
            "index.php",
            ".env.example",
            ".htaccess",
            ".user.ini",
            "nginx.sample.conf",
            "README.md",
            "database/migrations/001_init.sql",
            "database/seeders/001_seed.sql",
        ),
        test_commands=("php -l index.php",),
    ),
    "laravel_mysql": StackPack(
        id="laravel_mysql",
        title="Laravel + MySQL",
        description="Standard Laravel-style PHP enterprise application.",
        deployment_mode="docker_compose",
        release_root="release",
        api_only_default=False,
        home_entry="/",
        health_entry="/health",
        admin_entry="/admin/login",
        config_strategy="env_file",
        database_strategy="migration_plus_seed",
        browser_required=True,
        required_files=("README.md", ".env.example", "composer.json"),
        test_commands=("php artisan test",),
    ),
    "node_express_mysql": StackPack(
        id="node_express_mysql",
        title="Node.js + Express + MySQL",
        description="Express API with an operational admin surface.",
        deployment_mode="node_service",
        release_root="release",
        api_only_default=False,
        home_entry="/",
        health_entry="/health",
        admin_entry="/admin/login",
        config_strategy="env_file",
        database_strategy="migration_plus_seed",
        browser_required=True,
        required_files=("README.md", ".env.example", "package.json", "src/server.js"),
        test_commands=("npm test",),
    ),
    "react_node_mysql": StackPack(
        id="react_node_mysql",
        title="React + Node.js + MySQL",
        description="Separated React console and Node API.",
        deployment_mode="docker_compose",
        release_root="release",
        api_only_default=False,
        home_entry="/",
        health_entry="/health",
        admin_entry="/admin/login",
        config_strategy="env_file",
        database_strategy="migration_plus_seed",
        browser_required=True,
        required_files=("README.md", ".env.example", "package.json"),
        test_commands=("npm test",),
    ),
    "nextjs_prisma_postgres": StackPack(
        id="nextjs_prisma_postgres",
        title="Next.js + Prisma + PostgreSQL",
        description="Modern SaaS control plane with tenant-ready Postgres data model.",
        deployment_mode="docker_compose",
        release_root="release",
        api_only_default=False,
        home_entry="/",
        health_entry="/health",
        admin_entry="/admin/login",
        config_strategy="env_file",
        database_strategy="prisma_migration_seed",
        browser_required=True,
        required_files=("README.md", ".env.example", "package.json", "prisma/schema.prisma"),
        test_commands=("npm test",),
    ),
    "python_fastapi_postgres": StackPack(
        id="python_fastapi_postgres",
        title="FastAPI + PostgreSQL",
        description="Python API/data service stack for AI, RAG, and automation workloads.",
        deployment_mode="docker_compose",
        release_root="release",
        api_only_default=True,
        home_entry="/",
        health_entry="/health",
        admin_entry="/docs",
        config_strategy="env_file",
        database_strategy="alembic_migration_seed",
        browser_required=False,
        required_files=("README.md", ".env.example", "pyproject.toml", "app/main.py"),
        test_commands=("pytest",),
    ),
    "static_spa_api": StackPack(
        id="static_spa_api",
        title="Static SPA + external API",
        description="Static browser bundle for lightweight tools and public-facing utility sites.",
        deployment_mode="static_bundle",
        release_root="release",
        api_only_default=False,
        home_entry="/",
        health_entry="/health.html",
        admin_entry="/",
        config_strategy="static_config",
        database_strategy="external_api",
        browser_required=True,
        required_files=("README.md", "index.html", "assets/app.js"),
        test_commands=("npx playwright test",),
    ),
}


def list_stack_packs() -> list[dict[str, Any]]:
    return [pack.to_dict() for pack in STACK_PACKS.values()]


def get_stack_pack(stack_pack_id: str) -> StackPack:
    if stack_pack_id not in STACK_PACKS:
        raise KeyError(f"unknown stack pack: {stack_pack_id}")
    return STACK_PACKS[stack_pack_id]


def decide_stack_pack(
    requirement_text: str,
    requested_stack_pack: str = "auto",
    deployment_mode: str = "",
    api_only: bool | None = None,
) -> dict[str, Any]:
    requested = (requested_stack_pack or "auto").strip()
    lowered = requirement_text.lower()
    if requested != "auto":
        if requested not in STACK_PACKS:
            return {
                "ok": False,
                "stack_pack": "",
                "reason": "requested stack pack is not registered",
                "evidence": [requested],
                "no_go_reason": "unknown_stack_pack",
            }
        pack = STACK_PACKS[requested]
        return {
            "ok": True,
            "stack_pack": pack.id,
            "reason": "explicit user stack pack selection",
            "evidence": [requested],
            "deployment_mode": deployment_mode or pack.deployment_mode,
        }

    rules: list[tuple[str, tuple[str, ...], str]] = [
        (
            "php_mysql_single_dir",
            ("baota", "bt panel", "php", "mysql", "virtual host", "shared host", "宝塔", "虚拟主机", "后台"),
            "traditional PHP/MySQL or Baota-friendly deployment language detected",
        ),
        (
            "python_fastapi_postgres",
            ("ai", "rag", "embedding", "model", "fastapi", "python", "data pipeline", "知识库", "模型", "向量"),
            "AI/data service language detected",
        ),
        (
            "react_node_mysql",
            ("react", "front-end separation", "frontend separation", "前后端分离"),
            "React plus API deployment language detected",
        ),
        (
            "node_express_mysql",
            ("express", "node", "inventory api", "order api"),
            "Node/Express API language detected",
        ),
        (
            "nextjs_prisma_postgres",
            ("saas", "tenant", "rbac", "sso", "audit", "dashboard", "multi-tenant", "多租户", "控制台"),
            "SaaS control-plane language detected",
        ),
        (
            "static_spa_api",
            ("static", "spa", "landing", "tool site", "静态", "查询工具"),
            "static browser application language detected",
        ),
    ]
    for stack_pack_id, keywords, reason in rules:
        evidence = [keyword for keyword in keywords if keyword in lowered]
        if evidence:
            return {
                "ok": True,
                "stack_pack": stack_pack_id,
                "reason": reason,
                "evidence": evidence,
                "deployment_mode": deployment_mode or STACK_PACKS[stack_pack_id].deployment_mode,
            }

    if api_only is True:
        return {
            "ok": True,
            "stack_pack": "python_fastapi_postgres",
            "reason": "api_only project defaults to FastAPI/Postgres when no stack is given",
            "evidence": ["api_only=true"],
            "deployment_mode": deployment_mode or STACK_PACKS["python_fastapi_postgres"].deployment_mode,
        }

    return {
        "ok": False,
        "stack_pack": "",
        "reason": "requirements do not contain enough stack or deployment evidence",
        "evidence": [],
        "no_go_reason": "ambiguous_stack_pack",
    }


def build_product_contract(
    stack_pack_id: str,
    deployment_mode: str = "",
    api_only: bool | None = None,
) -> dict[str, Any]:
    pack = get_stack_pack(stack_pack_id)
    resolved_api_only = pack.api_only_default if api_only is None else bool(api_only)
    return {
        "schema_version": "4.0",
        "stack_pack": pack.id,
        "deployment_mode": deployment_mode or pack.deployment_mode,
        "api_only": resolved_api_only,
        "release_root": pack.release_root,
        "home_entry": pack.home_entry,
        "health_entry": pack.health_entry,
        "admin_entry": pack.admin_entry,
        "config_strategy": pack.config_strategy,
        "database_strategy": pack.database_strategy,
        "browser_required": bool(pack.browser_required and not resolved_api_only),
        "required_files": list(pack.required_files),
        "test_commands": list(pack.test_commands),
    }

