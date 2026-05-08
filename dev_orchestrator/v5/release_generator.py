from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from dev_orchestrator.v4.models import slugify


ADMIN_PASSWORD_HASH = "$2y$10$X8T4mxCZh7htMZVrL81a4.yT2Rwjd7s1N46T0jnA.F6pV3OyHq/2S"


def materialize_php_mysql_single_dir(
    materializer: Any,
    project: dict[str, Any],
    run: dict[str, Any],
    product_contract: dict[str, Any],
    release_root: Path,
    solution_graph: dict[str, Any],
) -> None:
    domain_spec = _build_domain_spec(project, run, product_contract, solution_graph)
    _reset_release_root(release_root)
    _write(materializer, release_root / "index.php", _render_index_php(domain_spec))
    _write(materializer, release_root / ".env.example", _render_env_example(domain_spec))
    _write(materializer, release_root / ".htaccess", _render_htaccess())
    _write(materializer, release_root / ".user.ini", _render_user_ini())
    _write(materializer, release_root / "nginx.sample.conf", _render_nginx_conf())
    _write(materializer, release_root / "README.md", _render_readme(domain_spec))
    _write(materializer, release_root / "config" / "database.php", _render_database_config())
    _write(materializer, release_root / "config" / "domain.php", _render_domain_config(domain_spec))
    _write(materializer, release_root / "app" / "Support" / "Env.php", _render_env_support())
    _write(materializer, release_root / "app" / "Support" / "Response.php", _render_response_support())
    _write(materializer, release_root / "app" / "Support" / "Database.php", _render_database_support())
    _write(materializer, release_root / "app" / "Support" / "Auth.php", _render_auth_support())
    _write(materializer, release_root / "app" / "Support" / "Csrf.php", _render_csrf_support())
    _write(materializer, release_root / "app" / "Support" / "DomainConfig.php", _render_domain_config_support())
    _write(materializer, release_root / "app" / "Support" / "Validator.php", _render_validator_support())
    _write(materializer, release_root / "app" / "Repositories" / "DomainRepository.php", _render_repository_support())
    _write(materializer, release_root / "app" / "Services" / "DashboardService.php", _render_dashboard_service())
    _write(materializer, release_root / "app" / "Controllers" / "HomeController.php", _render_home_controller())
    _write(materializer, release_root / "app" / "Controllers" / "AdminController.php", _render_admin_controller())
    _write(materializer, release_root / "app" / "Controllers" / "DomainController.php", _render_domain_controller())
    _write(materializer, release_root / "assets" / "app.css", _render_css())
    _write(materializer, release_root / "database" / "migrations" / "001_init.sql", _render_migration_sql(domain_spec))
    _write(materializer, release_root / "database" / "seeders" / "001_seed.sql", _render_seed_sql(domain_spec))
    _write(materializer, release_root / "storage" / "logs" / ".gitkeep", "")
    _write(materializer, release_root / "tests" / "smoke.http", _render_smoke_http())


def _build_domain_spec(
    project: dict[str, Any],
    run: dict[str, Any],
    product_contract: dict[str, Any],
    solution_graph: dict[str, Any],
) -> dict[str, Any]:
    product = solution_graph.get("product") or {}
    entities = list(solution_graph.get("entities") or [])
    if not entities:
        entities = [
            {
                "key": "records",
                "table": "business_records",
                "label": "Record",
                "plural_label": "Records",
                "route": "records",
                "fields": [
                    {"name": "title", "label": "Title", "type": "text", "required": True, "searchable": True, "unique": True, "default": "", "options": []},
                    {"name": "description", "label": "Description", "type": "textarea", "required": False, "searchable": True, "unique": False, "default": "", "options": []},
                    {"name": "status", "label": "Status", "type": "select", "required": True, "searchable": False, "unique": False, "default": "active", "options": ["active", "archived"]},
                ],
                "seed_records": [{"title": "Initial record", "description": "Generated from submitted requirements.", "status": "active"}],
            }
        ]
    product_name = str(product.get("name") or project.get("title") or project.get("name") or "Demand Driven Product")
    product_kind = str(product.get("kind") or "business_app")
    summary = str(product.get("summary") or project.get("description") or "Demand-driven product release.")
    return {
        "schema_version": "5.0",
        "product": {
            "name": product_name,
            "kind": product_kind,
            "summary": summary,
            "slug": slugify(product_name, "demand-driven-product"),
            "stack_pack": product_contract.get("stack_pack", ""),
            "deployment_mode": product_contract.get("deployment_mode", ""),
        },
        "entities": [_normalize_entity(entity) for entity in entities],
        "workflows": solution_graph.get("workflows") or [],
        "pages": solution_graph.get("pages") or [],
        "requirements_text": str((run.get("metadata") or {}).get("requirements_text") or project.get("description") or ""),
    }


def _normalize_entity(entity: dict[str, Any]) -> dict[str, Any]:
    fields = []
    for field in entity.get("fields", []):
        fields.append(
            {
                "name": _field_name(str(field.get("name") or "field")),
                "label": str(field.get("label") or field.get("name") or "Field"),
                "type": str(field.get("type") or "text"),
                "required": bool(field.get("required")),
                "searchable": bool(field.get("searchable")),
                "unique": bool(field.get("unique")),
                "default": str(field.get("default") or ""),
                "options": [str(item) for item in field.get("options", [])] if isinstance(field.get("options"), list) else [],
            }
        )
    return {
        "key": _field_name(str(entity.get("key") or "entity")),
        "table": _table_name(str(entity.get("table") or entity.get("key") or "entities")),
        "label": str(entity.get("label") or entity.get("key") or "Record"),
        "plural_label": str(entity.get("plural_label") or entity.get("label") or "Records"),
        "route": _route_name(str(entity.get("route") or entity.get("key") or "records")),
        "search_placeholder": str(entity.get("search_placeholder") or f"Search {entity.get('plural_label') or entity.get('label') or 'records'}"),
        "fields": fields or [
            {"name": "title", "label": "Title", "type": "text", "required": True, "searchable": True, "unique": True, "default": "", "options": []},
            {"name": "description", "label": "Description", "type": "textarea", "required": False, "searchable": True, "unique": False, "default": "", "options": []},
        ],
        "seed_records": entity.get("seed_records") if isinstance(entity.get("seed_records"), list) else [],
    }


def _write(materializer: Any, path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    materializer._write_once(path, text)


def _reset_release_root(release_root: Path) -> None:
    if release_root.exists():
        shutil.rmtree(release_root)
    release_root.mkdir(parents=True, exist_ok=True)


def _field_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    return cleaned or "field"


def _table_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    if not cleaned:
        cleaned = "records"
    if not cleaned.endswith("s"):
        cleaned += "s"
    return cleaned


def _route_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "records"


def _render_index_php(domain_spec: dict[str, Any]) -> str:
    entities_json = json.dumps(domain_spec["entities"], ensure_ascii=True)
    product_name = domain_spec["product"]["name"]
    product_slug = domain_spec["product"]["slug"]
    return f"""<?php
declare(strict_types=1);

require_once __DIR__ . '/app/Support/Env.php';
require_once __DIR__ . '/app/Support/Response.php';
require_once __DIR__ . '/app/Support/Database.php';
require_once __DIR__ . '/app/Support/Auth.php';
require_once __DIR__ . '/app/Support/Csrf.php';
require_once __DIR__ . '/app/Support/DomainConfig.php';
require_once __DIR__ . '/app/Support/Validator.php';
require_once __DIR__ . '/app/Repositories/DomainRepository.php';
require_once __DIR__ . '/app/Services/DashboardService.php';
require_once __DIR__ . '/app/Controllers/HomeController.php';
require_once __DIR__ . '/app/Controllers/AdminController.php';
require_once __DIR__ . '/app/Controllers/DomainController.php';

Env::load(__DIR__ . '/.env');
Auth::start();

$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
$method = strtoupper($_SERVER['REQUEST_METHOD'] ?? 'GET');
$entities = DomainConfig::entities();
$domainController = new DomainController();

if ($path === '/health') {{
    Response::json([
        'status' => 'ok',
        'service' => '{product_slug}',
        'product' => '{product_name}',
        'stack_pack' => 'php_mysql_single_dir',
        'release_root' => 'single_directory_upload'
    ]);
}}

if ($path === '/' && $method === 'GET') {{
    (new HomeController())->index();
    return;
}}

if ($path === '/admin/login' && $method === 'GET') {{
    (new AdminController())->loginForm();
    return;
}}

if ($path === '/admin/login' && $method === 'POST') {{
    (new AdminController())->login();
    return;
}}

if ($path === '/admin/logout' && $method === 'POST') {{
    (new AdminController())->logout();
    return;
}}

if ($path === '/admin/dashboard' && $method === 'GET') {{
    (new AdminController())->dashboard();
    return;
}}

foreach ($entities as $entity) {{
    $route = '/' . ltrim((string) ($entity['route'] ?? ''), '/');
    $adminRoute = '/admin' . $route;
    $key = (string) ($entity['key'] ?? '');
    if ($key === '') {{
        continue;
    }}
    if ($path === $route && $method === 'GET') {{
        $domainController->index($key);
        return;
    }}
    if ($path === $adminRoute . '/create' && $method === 'GET') {{
        $domainController->create($key);
        return;
    }}
    if ($path === $adminRoute && $method === 'POST') {{
        $domainController->store($key);
        return;
    }}
    if (preg_match('#^' . preg_quote($adminRoute, '#') . '/(\\d+)/edit$#', $path, $matches) && $method === 'GET') {{
        $domainController->edit($key, (int) $matches[1]);
        return;
    }}
    if (preg_match('#^' . preg_quote($adminRoute, '#') . '/(\\d+)$#', $path, $matches) && $method === 'POST') {{
        $domainController->update($key, (int) $matches[1]);
        return;
    }}
    if (preg_match('#^' . preg_quote($adminRoute, '#') . '/(\\d+)/delete$#', $path, $matches) && $method === 'POST') {{
        $domainController->delete($key, (int) $matches[1]);
        return;
    }}
}}

Response::notFound('Route not found.');
"""


def _render_domain_config(domain_spec: dict[str, Any]) -> str:
    payload = json.dumps(domain_spec, ensure_ascii=True, indent=2)
    return f"""<?php
declare(strict_types=1);

return json_decode(<<<'JSON'
{payload}
JSON, true);
"""


def _render_env_example(domain_spec: dict[str, Any]) -> str:
    slug = domain_spec["product"]["slug"]
    db_name = slug.replace("-", "_")
    return f"""APP_ENV=production
APP_NAME={slug}
APP_KEY=change-me
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME={db_name}
DB_USER=root
DB_PASSWORD=
"""


def _render_htaccess() -> str:
    return """Options -Indexes
DirectoryIndex index.php

<FilesMatch "^\\.env">
  Require all denied
</FilesMatch>

RewriteEngine On
RewriteCond %{REQUEST_FILENAME} !-f
RewriteCond %{REQUEST_FILENAME} !-d
RewriteRule ^ index.php [QSA,L]

RedirectMatch 403 ^/(app|config|database|storage|tests)(/|$)
"""


def _render_user_ini() -> str:
    return """open_basedir=.:/tmp/
expose_php=0
display_errors=0
log_errors=1
"""


def _render_nginx_conf() -> str:
    return """location ~ ^/(app|config|database|storage|tests)/ {
    deny all;
}

location / {
    try_files $uri $uri/ /index.php?$query_string;
}

location ~ /\\.env {
    deny all;
}
"""


def _render_readme(domain_spec: dict[str, Any]) -> str:
    product = domain_spec["product"]
    entities = ", ".join(entity["plural_label"] for entity in domain_spec["entities"])
    return f"""# Clean Release

Upload every file inside this release directory to your website root.

## Product

{product['name']}

## Core domain

{entities}

## Baota deployment

1. Upload the contents of `release/` to the website root directory.
2. Select PHP 8.2.
3. Import `database/migrations/001_init.sql`.
4. Import `database/seeders/001_seed.sql`.
5. Copy `.env.example` to `.env` and fill `DB_HOST`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`.
6. Visit `/health`, `/`, and `/admin/login`.

Default seed account: `admin` / `admin123456`.
"""


def _render_database_config() -> str:
    return """<?php
declare(strict_types=1);

return [
    'host' => Env::get('DB_HOST', '127.0.0.1'),
    'port' => Env::get('DB_PORT', '3306'),
    'database' => Env::get('DB_NAME', ''),
    'username' => Env::get('DB_USER', 'root'),
    'password' => Env::get('DB_PASSWORD', ''),
];
"""


def _render_domain_config_support() -> str:
    return """<?php
declare(strict_types=1);

final class DomainConfig
{
    private static ?array $config = null;

    public static function all(): array
    {
        if (self::$config === null) {
            self::$config = require __DIR__ . '/../../config/domain.php';
        }
        return self::$config;
    }

    public static function entities(): array
    {
        return self::all()['entities'] ?? [];
    }

    public static function entityByKey(string $key): ?array
    {
        foreach (self::entities() as $entity) {
            if (($entity['key'] ?? '') === $key) {
                return $entity;
            }
        }
        return null;
    }
}
"""


def _render_env_support() -> str:
    return """<?php
declare(strict_types=1);

final class Env
{
    private static array $values = [];

    public static function load(string $path): void
    {
        if (!is_file($path)) {
            return;
        }
        foreach (file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
            $trimmed = trim((string) $line);
            if ($trimmed === '' || str_starts_with($trimmed, '#') || !str_contains($trimmed, '=')) {
                continue;
            }
            [$key, $value] = array_map('trim', explode('=', $trimmed, 2));
            self::$values[$key] = $value;
        }
    }

    public static function get(string $key, string $default = ''): string
    {
        return self::$values[$key] ?? getenv($key) ?: $default;
    }
}
"""


def _render_response_support() -> str:
    return """<?php
declare(strict_types=1);

final class Response
{
    public static function json(array $payload, int $status = 200): void
    {
        http_response_code($status);
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        exit;
    }

    public static function html(string $html, int $status = 200): void
    {
        http_response_code($status);
        header('Content-Type: text/html; charset=utf-8');
        echo $html;
        exit;
    }

    public static function notFound(string $message): void
    {
        self::html('<h1>404</h1><p>' . htmlspecialchars($message, ENT_QUOTES, 'UTF-8') . '</p>', 404);
    }
}
"""


def _render_database_support() -> str:
    return """<?php
declare(strict_types=1);

final class Database
{
    public static function pdo(): PDO
    {
        $host = Env::get('DB_HOST', '127.0.0.1');
        $port = Env::get('DB_PORT', '3306');
        $name = Env::get('DB_NAME', '');
        $user = Env::get('DB_USER', 'root');
        $password = Env::get('DB_PASSWORD', '');
        $dsn = "mysql:host={$host};port={$port};dbname={$name};charset=utf8mb4";
        return new PDO($dsn, $user, $password, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
    }
}
"""


def _render_auth_support() -> str:
    return """<?php
declare(strict_types=1);

final class Auth
{
    public static function start(): void
    {
        if (session_status() !== PHP_SESSION_ACTIVE) {
            session_start();
        }
    }

    public static function login(array $admin): void
    {
        $_SESSION['admin'] = [
            'id' => (int) $admin['id'],
            'username' => (string) $admin['username'],
            'display_name' => (string) $admin['display_name'],
        ];
    }

    public static function logout(): void
    {
        $_SESSION = [];
        if (session_status() === PHP_SESSION_ACTIVE) {
            session_destroy();
        }
    }

    public static function user(): ?array
    {
        return isset($_SESSION['admin']) && is_array($_SESSION['admin']) ? $_SESSION['admin'] : null;
    }

    public static function requireAdmin(): array
    {
        $admin = self::user();
        if (!$admin) {
            header('Location: /admin/login');
            exit;
        }
        return $admin;
    }
}
"""


def _render_csrf_support() -> str:
    return """<?php
declare(strict_types=1);

final class Csrf
{
    public static function token(): string
    {
        if (empty($_SESSION['_csrf'])) {
            $_SESSION['_csrf'] = bin2hex(random_bytes(32));
        }
        return (string) $_SESSION['_csrf'];
    }

    public static function field(): string
    {
        return '<input type="hidden" name="_csrf" value="' . htmlspecialchars(self::token(), ENT_QUOTES, 'UTF-8') . '">';
    }

    public static function verify(): void
    {
        $posted = (string) ($_POST['_csrf'] ?? '');
        $token = (string) ($_SESSION['_csrf'] ?? '');
        if ($posted === '' || $token === '' || !hash_equals($token, $posted)) {
            Response::html('<h1>Invalid request</h1><p>CSRF token mismatch.</p>', 419);
        }
    }
}
"""


def _render_validator_support() -> str:
    return """<?php
declare(strict_types=1);

final class Validator
{
    public static function login(array $input): array
    {
        $data = [
            'username' => trim((string) ($input['username'] ?? '')),
            'password' => (string) ($input['password'] ?? ''),
        ];
        $errors = [];
        if ($data['username'] === '') {
            $errors['username'] = 'This field is required.';
        }
        if ($data['password'] === '') {
            $errors['password'] = 'This field is required.';
        }
        return [$data, $errors];
    }

    public static function entity(array $entity, array $input): array
    {
        $data = [];
        $errors = [];
        foreach ($entity['fields'] as $field) {
            $name = (string) $field['name'];
            $type = (string) ($field['type'] ?? 'text');
            $raw = $input[$name] ?? ($field['default'] ?? '');
            $value = is_array($raw) ? '' : trim((string) $raw);
            if ($type === 'number' && $value !== '' && !preg_match('/^-?\\d+$/', $value)) {
                $errors[$name] = 'Use a valid number.';
            } elseif ($type === 'date' && $value !== '' && !preg_match('/^\\d{4}-\\d{2}-\\d{2}$/', $value)) {
                $errors[$name] = 'Use YYYY-MM-DD.';
            } elseif ($type === 'select') {
                $options = array_map('strval', $field['options'] ?? []);
                if ($value !== '' && $options && !in_array($value, $options, true)) {
                    $errors[$name] = 'Choose a valid option.';
                }
            }
            if (($field['required'] ?? false) && $value === '') {
                $errors[$name] = 'This field is required.';
            }
            $data[$name] = $value;
        }
        return [$data, $errors];
    }
}
"""


def _render_repository_support() -> str:
    return """<?php
declare(strict_types=1);

final class DomainRepository
{
    public function __construct(private ?PDO $pdo = null)
    {
        $this->pdo = $pdo ?: Database::pdo();
    }

    public function paginate(array $entity, string $keyword = '', int $page = 1, int $perPage = 10): array
    {
        $table = $this->table($entity);
        $offset = max(0, ($page - 1) * $perPage);
        $searchable = $this->searchableFields($entity);
        $where = '';
        $params = [];
        if ($keyword !== '' && $searchable) {
            $where = 'WHERE ' . implode(' OR ', array_map(fn($field) => $this->column($field) . ' LIKE ?', $searchable));
            $like = '%' . $keyword . '%';
            $params = array_fill(0, count($searchable), $like);
        }
        $count = $this->pdo->prepare("SELECT COUNT(*) AS total FROM {$table} {$where}");
        $count->execute($params);
        $total = (int) ($count->fetch()['total'] ?? 0);
        $statement = $this->pdo->prepare("SELECT * FROM {$table} {$where} ORDER BY id DESC LIMIT {$perPage} OFFSET {$offset}");
        $statement->execute($params);
        return ['items' => $statement->fetchAll(), 'total' => $total, 'page' => $page, 'pages' => max(1, (int) ceil($total / $perPage))];
    }

    public function find(array $entity, int $id): ?array
    {
        $table = $this->table($entity);
        $statement = $this->pdo->prepare("SELECT * FROM {$table} WHERE id = ? LIMIT 1");
        $statement->execute([$id]);
        $row = $statement->fetch();
        return $row ?: null;
    }

    public function create(array $entity, array $data): int
    {
        $table = $this->table($entity);
        $columns = [];
        $values = [];
        foreach ($entity['fields'] as $field) {
            $columns[] = $this->column($field['name']);
            $values[] = $this->valueForStorage($field, $data[$field['name']] ?? '');
        }
        $placeholders = implode(', ', array_fill(0, count($columns), '?'));
        $statement = $this->pdo->prepare("INSERT INTO {$table} (" . implode(', ', $columns) . ") VALUES ({$placeholders})");
        $statement->execute($values);
        return (int) $this->pdo->lastInsertId();
    }

    public function update(array $entity, int $id, array $data): void
    {
        $table = $this->table($entity);
        $assignments = [];
        $values = [];
        foreach ($entity['fields'] as $field) {
            $assignments[] = $this->column($field['name']) . ' = ?';
            $values[] = $this->valueForStorage($field, $data[$field['name']] ?? '');
        }
        $values[] = $id;
        $statement = $this->pdo->prepare("UPDATE {$table} SET " . implode(', ', $assignments) . ", updated_at = CURRENT_TIMESTAMP WHERE id = ?");
        $statement->execute($values);
    }

    public function delete(array $entity, int $id): void
    {
        $table = $this->table($entity);
        $statement = $this->pdo->prepare("DELETE FROM {$table} WHERE id = ?");
        $statement->execute([$id]);
    }

    public function uniqueValueExists(array $entity, string $fieldName, string $value, ?int $exceptId = null): bool
    {
        $table = $this->table($entity);
        $field = $this->field($entity, $fieldName);
        if (!$field || !($field['unique'] ?? false) || $value === '') {
            return false;
        }
        $sql = "SELECT id FROM {$table} WHERE " . $this->column($fieldName) . ' = ?';
        $params = [$value];
        if ($exceptId) {
            $sql .= ' AND id <> ?';
            $params[] = $exceptId;
        }
        $statement = $this->pdo->prepare($sql . ' LIMIT 1');
        $statement->execute($params);
        return (bool) $statement->fetch();
    }

    public function statistics(): array
    {
        $stats = [];
        foreach (DomainConfig::entities() as $entity) {
            $table = $this->table($entity);
            $label = (string) ($entity['plural_label'] ?? $entity['label'] ?? $entity['key']);
            $stats[] = [
                'label' => $label,
                'value' => (int) $this->pdo->query("SELECT COUNT(*) AS value FROM {$table}")->fetch()['value'],
            ];
        }
        return $stats;
    }

    private function field(array $entity, string $fieldName): ?array
    {
        foreach ($entity['fields'] as $field) {
            if (($field['name'] ?? '') === $fieldName) {
                return $field;
            }
        }
        return null;
    }

    private function searchableFields(array $entity): array
    {
        return array_values(array_filter(array_map(
            fn($field) => ($field['searchable'] ?? false) ? (string) $field['name'] : '',
            $entity['fields']
        )));
    }

    private function table(array $entity): string
    {
        return $this->column((string) ($entity['table'] ?? $entity['key'] ?? 'records'));
    }

    private function column(string $name): string
    {
        if (!preg_match('/^[A-Za-z0-9_]+$/', $name)) {
            throw new RuntimeException('Invalid SQL identifier.');
        }
        return '`' . $name . '`';
    }

    private function valueForStorage(array $field, mixed $value): mixed
    {
        $type = (string) ($field['type'] ?? 'text');
        if ($type === 'number') {
            return $value === '' ? 0 : (int) $value;
        }
        return (string) $value;
    }
}
"""


def _render_dashboard_service() -> str:
    return """<?php
declare(strict_types=1);

final class DashboardService
{
    public function __construct(private ?DomainRepository $repository = null)
    {
        $this->repository = $repository ?: new DomainRepository();
    }

    public function cards(): array
    {
        return $this->repository->statistics();
    }
}
"""


def _render_home_controller() -> str:
    return """<?php
declare(strict_types=1);

final class HomeController
{
    public function index(): void
    {
        $config = DomainConfig::all();
        $product = $config['product'] ?? [];
        $entities = $config['entities'] ?? [];
        $workflows = $config['workflows'] ?? [];
        $title = htmlspecialchars((string) ($product['name'] ?? 'Demand Driven Product'), ENT_QUOTES, 'UTF-8');
        $summary = htmlspecialchars((string) ($product['summary'] ?? 'Demand-driven product release.'), ENT_QUOTES, 'UTF-8');
        $entityCards = '';
        foreach ($entities as $entity) {
            $label = htmlspecialchars((string) ($entity['plural_label'] ?? $entity['label'] ?? $entity['key']), ENT_QUOTES, 'UTF-8');
            $route = htmlspecialchars('/' . ltrim((string) ($entity['route'] ?? ''), '/'), ENT_QUOTES, 'UTF-8');
            $entityCards .= '<a class="card-link" href="' . $route . '"><strong>' . $label . '</strong><small>Open module</small></a>';
        }
        $workflowCards = '';
        foreach ($workflows as $workflow) {
            $workflowCards .= '<article class="card"><strong>' . htmlspecialchars((string) ($workflow['title'] ?? ''), ENT_QUOTES, 'UTF-8') . '</strong></article>';
        }
        Response::html(<<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{$title}</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <main class="shell">
    <section class="hero">
      <p class="eyebrow">V5 demand-driven release</p>
      <h1>{$title}</h1>
      <p>{$summary}</p>
      <div class="actions">
        <a class="button" href="/admin/login">Admin Login</a>
        <a class="button secondary" href="/health">Health</a>
      </div>
    </section>
    <section class="panel">
      <h2>Modules</h2>
      <div class="cards">{$entityCards}</div>
    </section>
    <section class="panel">
      <h2>Workflows</h2>
      <div class="cards">{$workflowCards}</div>
    </section>
  </main>
</body>
</html>
HTML);
    }
}
"""


def _render_admin_controller() -> str:
    return """<?php
declare(strict_types=1);

final class AdminController
{
    public function loginForm(string $error = ''): void
    {
        $message = $error ? '<p class="error">' . htmlspecialchars($error, ENT_QUOTES, 'UTF-8') . '</p>' : '';
        $csrf = Csrf::field();
        Response::html(<<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Admin Login</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <main class="login-shell">
    <form class="login-card" action="/admin/login" method="post">
      <h1>Admin Login</h1>
      {$message}
      {$csrf}
      <label>Username<input name="username" autocomplete="username" required></label>
      <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
      <button type="submit">Sign in</button>
    </form>
  </main>
</body>
</html>
HTML);
    }

    public function login(): void
    {
        Csrf::verify();
        [$data, $errors] = Validator::login($_POST);
        if ($errors) {
            $this->loginForm('Username and password are required.');
            return;
        }
        try {
            $pdo = Database::pdo();
            $statement = $pdo->prepare('SELECT id, username, password_hash, display_name FROM admin_users WHERE username = ? AND active = 1 LIMIT 1');
            $statement->execute([$data['username']]);
            $admin = $statement->fetch();
            if (!$admin || !password_verify($data['password'], (string) $admin['password_hash'])) {
                $this->loginForm('Invalid username or password.');
                return;
            }
            Auth::login($admin);
            header('Location: /admin/dashboard');
            exit;
        } catch (Throwable $error) {
            $this->loginForm('Database is not configured yet. Check .env and imported SQL files.');
        }
    }

    public function dashboard(): void
    {
        $admin = Auth::requireAdmin();
        try {
            $cards = (new DashboardService())->cards();
        } catch (Throwable $error) {
            $cards = [];
        }
        $cardHtml = '';
        foreach ($cards as $card) {
            $label = htmlspecialchars((string) $card['label'], ENT_QUOTES, 'UTF-8');
            $value = htmlspecialchars((string) $card['value'], ENT_QUOTES, 'UTF-8');
            $cardHtml .= '<article class="stat"><span>' . $label . '</span><strong>' . $value . '</strong></article>';
        }
        $name = htmlspecialchars((string) $admin['display_name'], ENT_QUOTES, 'UTF-8');
        $csrf = Csrf::field();
        Response::html(<<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Dashboard</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <main class="shell">
    <nav class="topbar">
      <a href="/admin/dashboard">Dashboard</a>
      <a href="/">Home</a>
      <form action="/admin/logout" method="post">{$csrf}<button type="submit">Logout {$name}</button></form>
    </nav>
    <section class="hero compact">
      <p class="eyebrow">Operations</p>
      <h1>Product dashboard</h1>
      <p>Review demand-driven modules and jump into management.</p>
    </section>
    <section class="stats">{$cardHtml}</section>
  </main>
</body>
</html>
HTML);
    }

    public function logout(): void
    {
        Csrf::verify();
        Auth::logout();
        header('Location: /');
        exit;
    }
}
"""


def _render_domain_controller() -> str:
    return """<?php
declare(strict_types=1);

final class DomainController
{
    private DomainRepository $repository;

    public function __construct()
    {
        $this->repository = new DomainRepository();
    }

    public function index(string $entityKey): void
    {
        $entity = $this->entityOr404($entityKey);
        $keyword = trim((string) ($_GET['q'] ?? ''));
        $page = max(1, (int) ($_GET['page'] ?? 1));
        try {
            $data = $this->repository->paginate($entity, $keyword, $page);
        } catch (Throwable $error) {
            $data = ['items' => [], 'total' => 0, 'page' => 1, 'pages' => 1];
        }
        $rows = '';
        foreach ($data['items'] as $item) {
            $id = (int) $item['id'];
            $fields = [];
            foreach ($entity['fields'] as $field) {
                $name = (string) $field['name'];
                $fields[] = htmlspecialchars((string) ($item[$name] ?? ''), ENT_QUOTES, 'UTF-8');
            }
            $actions = '';
            if (Auth::user()) {
                $route = '/admin/' . $entity['route'] . '/' . $id;
                $csrf = Csrf::field();
                $actions = '<a href="' . $route . '/edit">Edit</a>'
                    . '<form action="' . $route . '/delete" method="post" onsubmit="return confirm(&quot;Delete this record?&quot;)">'
                    . $csrf
                    . '<button type="submit">Delete</button></form>';
            }
            $rowCells = '';
            foreach ($fields as $value) {
                $rowCells .= '<td>' . $value . '</td>';
            }
            $rows .= '<tr>' . $rowCells . '<td class="actions-cell">' . $actions . '</td></tr>';
        }
        if ($rows === '') {
            $rows = '<tr><td colspan="' . (count($entity['fields']) + 1) . '">No records found.</td></tr>';
        }
        $headers = '';
        foreach ($entity['fields'] as $field) {
            $headers .= '<th>' . htmlspecialchars((string) $field['label'], ENT_QUOTES, 'UTF-8') . '</th>';
        }
        $create = Auth::user() ? '<a class="button" href="/admin/' . $entity['route'] . '/create">New ' . htmlspecialchars((string) $entity['label'], ENT_QUOTES, 'UTF-8') . '</a>' : '<a class="button" href="/admin/login">Admin login</a>';
        $title = htmlspecialchars((string) $entity['plural_label'], ENT_QUOTES, 'UTF-8');
        $searchPlaceholder = htmlspecialchars((string) $entity['search_placeholder'], ENT_QUOTES, 'UTF-8');
        Response::html(<<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{$title}</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <main class="shell">
    <nav class="topbar"><a href="/">Home</a><a href="/admin/dashboard">Dashboard</a></nav>
    <section class="panel">
      <div class="section-title"><h1>{$title}</h1>{$create}</div>
      <form class="search" method="get" action="/{$entity['route']}">
        <input name="q" value="{$keyword}" placeholder="{$searchPlaceholder}">
        <button type="submit">Search</button>
      </form>
      <table>
        <thead><tr>{$headers}<th>Actions</th></tr></thead>
        <tbody>{$rows}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
HTML);
    }

    public function create(string $entityKey): void
    {
        $entity = $this->entityOr404($entityKey);
        Auth::requireAdmin();
        $this->form($entity, 'Create ' . $entity['label'], '/admin/' . $entity['route'], []);
    }

    public function store(string $entityKey): void
    {
        $entity = $this->entityOr404($entityKey);
        Auth::requireAdmin();
        Csrf::verify();
        [$data, $errors] = Validator::entity($entity, $_POST);
        if (!$errors) {
            foreach ($entity['fields'] as $field) {
                if (($field['unique'] ?? false) && $this->repository->uniqueValueExists($entity, (string) $field['name'], (string) ($data[$field['name']] ?? ''))) {
                    $errors[$field['name']] = 'This value already exists.';
                }
            }
        }
        if ($errors) {
            $this->form($entity, 'Create ' . $entity['label'], '/admin/' . $entity['route'], $data, $errors);
            return;
        }
        $this->repository->create($entity, $data);
        header('Location: /' . $entity['route']);
        exit;
    }

    public function edit(string $entityKey, int $id): void
    {
        $entity = $this->entityOr404($entityKey);
        Auth::requireAdmin();
        $record = $this->repository->find($entity, $id);
        if (!$record) {
            Response::notFound('Record not found.');
        }
        $this->form($entity, 'Edit ' . $entity['label'], '/admin/' . $entity['route'] . '/' . $id, $record);
    }

    public function update(string $entityKey, int $id): void
    {
        $entity = $this->entityOr404($entityKey);
        Auth::requireAdmin();
        Csrf::verify();
        [$data, $errors] = Validator::entity($entity, $_POST);
        if (!$errors) {
            foreach ($entity['fields'] as $field) {
                if (($field['unique'] ?? false) && $this->repository->uniqueValueExists($entity, (string) $field['name'], (string) ($data[$field['name']] ?? ''), $id)) {
                    $errors[$field['name']] = 'This value already exists.';
                }
            }
        }
        if ($errors) {
            $this->form($entity, 'Edit ' . $entity['label'], '/admin/' . $entity['route'] . '/' . $id, $data, $errors);
            return;
        }
        $this->repository->update($entity, $id, $data);
        header('Location: /' . $entity['route']);
        exit;
    }

    public function delete(string $entityKey, int $id): void
    {
        $entity = $this->entityOr404($entityKey);
        Auth::requireAdmin();
        Csrf::verify();
        $this->repository->delete($entity, $id);
        header('Location: /' . $entity['route']);
        exit;
    }

    private function form(array $entity, string $title, string $action, array $values, array $errors = []): void
    {
        $csrf = Csrf::field();
        $error = fn(string $key) => isset($errors[$key]) ? '<small class="error">' . htmlspecialchars($errors[$key], ENT_QUOTES, 'UTF-8') . '</small>' : '';
        $fields = '';
        foreach ($entity['fields'] as $field) {
            $fields .= $this->renderField($field, $values, $error);
        }
        $titleEsc = htmlspecialchars($title, ENT_QUOTES, 'UTF-8');
        Response::html(<<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{$titleEsc}</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <main class="shell">
    <nav class="topbar"><a href="/{$entity['route']}">Back</a><a href="/admin/dashboard">Dashboard</a></nav>
    <form class="panel form-grid" action="{$action}" method="post">
      <h1>{$titleEsc}</h1>
      {$csrf}
      {$fields}
      <button type="submit">Save</button>
    </form>
  </main>
</body>
</html>
HTML);
    }

    private function renderField(array $field, array $values, callable $error): string
    {
        $name = (string) $field['name'];
        $label = htmlspecialchars((string) $field['label'], ENT_QUOTES, 'UTF-8');
        $value = htmlspecialchars((string) ($values[$name] ?? ($field['default'] ?? '')), ENT_QUOTES, 'UTF-8');
        $type = (string) ($field['type'] ?? 'text');
        $options = $field['options'] ?? [];
        $html = '<label>' . $label;
        if ($type === 'textarea') {
            $html .= '<textarea name="' . $name . '">' . $value . '</textarea>';
        } elseif ($type === 'select') {
            $html .= '<select name="' . $name . '">';
            foreach ($options as $option) {
                $optionValue = htmlspecialchars((string) $option, ENT_QUOTES, 'UTF-8');
                $selected = ((string) ($values[$name] ?? ($field['default'] ?? '')) === (string) $option) ? ' selected' : '';
                $html .= '<option value="' . $optionValue . '"' . $selected . '>' . $optionValue . '</option>';
            }
            $html .= '</select>';
        } else {
            $inputType = $type === 'number' ? 'number' : ($type === 'date' ? 'date' : 'text');
            $html .= '<input name="' . $name . '" type="' . $inputType . '" value="' . $value . '"' . (($field['required'] ?? false) ? ' required' : '') . '>';
        }
        $html .= $error($name) . '</label>';
        return $html;
    }

    private function entityOr404(string $entityKey): array
    {
        $entity = DomainConfig::entityByKey($entityKey);
        if (!$entity) {
            Response::notFound('Entity not found.');
        }
        return $entity;
    }
}
"""


def _render_css() -> str:
    return """:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; font-family: Inter, system-ui, sans-serif; background: #f5f2ea; color: #172033; }
a { color: inherit; text-decoration: none; }
button, input, select, textarea { font: inherit; }
.shell { max-width: 1200px; margin: 0 auto; padding: 24px; display: grid; gap: 18px; }
.hero, .panel, .login-card { background: #fff; border: 1px solid #cfd6e2; border-radius: 12px; padding: 20px; box-shadow: 0 1px 0 rgba(0,0,0,.03); }
.hero h1, .panel h1 { margin: 0 0 10px; font-size: 1.9rem; }
.hero p { max-width: 72ch; line-height: 1.55; }
.eyebrow { margin: 0 0 8px; text-transform: uppercase; letter-spacing: .08em; font-size: .75rem; color: #54657f; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
.button, .topbar button, .project-run, .panel button { display: inline-flex; align-items: center; justify-content: center; padding: 10px 14px; border: 1px solid #24324a; border-radius: 10px; background: #24324a; color: #fff; cursor: pointer; }
.button.secondary, .project-run.subtle { background: #fff; color: #24324a; }
.topbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.topbar form { margin: 0; }
.cards, .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.card, .card-link, .stat { border: 1px solid #d7dde8; border-radius: 10px; padding: 14px; background: #fff; }
.card-link { display: grid; gap: 4px; }
.stat span { display: block; color: #5c6677; font-size: .9rem; }
.stat strong { display: block; font-size: 1.1rem; }
table { width: 100%; border-collapse: collapse; background: #fff; }
th, td { border-bottom: 1px solid #e5e9ef; padding: 10px; text-align: left; vertical-align: top; }
.form-grid, .login-card { display: grid; gap: 12px; max-width: 760px; }
label { display: grid; gap: 6px; }
input, textarea, select { width: 100%; border: 1px solid #cfd6e2; border-radius: 8px; padding: 10px 12px; background: #fff; }
textarea { min-height: 140px; resize: vertical; }
.search { display: flex; gap: 10px; margin: 12px 0 18px; }
.error { color: #b00020; }
.section-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.login-shell { min-height: 100vh; display: grid; place-items: center; padding: 24px; }
.actions-cell { display: flex; gap: 8px; flex-wrap: wrap; }
@media (max-width: 720px) {
  .topbar, .section-title, .search { flex-direction: column; align-items: stretch; }
}
"""


def _render_migration_sql(domain_spec: dict[str, Any]) -> str:
    statements = [
        """CREATE TABLE IF NOT EXISTS admin_users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(80) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  display_name VARCHAR(120) NOT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;""",
        """CREATE TABLE IF NOT EXISTS audit_events (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  actor VARCHAR(120) NOT NULL,
  event_type VARCHAR(120) NOT NULL,
  payload JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;""",
    ]
    for entity in domain_spec["entities"]:
        statements.append(_create_table_sql(entity))
    return "\n\n".join(statements) + "\n"


def _create_table_sql(entity: dict[str, Any]) -> str:
    columns = [
        "  id BIGINT AUTO_INCREMENT PRIMARY KEY",
    ]
    for field in entity["fields"]:
        columns.append("  " + _sql_column(field))
    columns.append("  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP")
    columns.append("  updated_at TIMESTAMP NULL DEFAULT NULL")
    for field in entity["fields"]:
        if field.get("searchable"):
            columns.append(f"  INDEX idx_{entity['table']}_{field['name']} (`{field['name']}`)")
        if field.get("unique"):
            columns.append(f"  UNIQUE KEY uniq_{entity['table']}_{field['name']} (`{field['name']}`)")
    return f"""CREATE TABLE IF NOT EXISTS `{entity['table']}` (
{',\n'.join(columns)}
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;"""


def _sql_column(field: dict[str, Any]) -> str:
    sql_type = {
        "text": "VARCHAR(190) NOT NULL",
        "textarea": "TEXT NOT NULL",
        "number": "INT NOT NULL DEFAULT 0",
        "date": "DATE NOT NULL",
        "select": "VARCHAR(80) NOT NULL",
    }.get(str(field.get("type") or "text"), "VARCHAR(190) NOT NULL")
    nullable = " NOT NULL" if field.get("required") else " NULL"
    default = field.get("default")
    default_sql = ""
    if default not in ("", None) and field.get("type") != "textarea":
        if field.get("type") == "number" and str(default).lstrip("-").isdigit():
            default_sql = f" DEFAULT {int(default)}"
        else:
            default_sql = " DEFAULT " + _sql_quote(str(default))
    if field.get("type") == "textarea":
        nullable = " NOT NULL"
    return f"`{field['name']}` {sql_type.split(' ', 1)[0]}{nullable}{default_sql}"


def _render_seed_sql(domain_spec: dict[str, Any]) -> str:
    inserts = [
        """INSERT INTO admin_users (username, password_hash, display_name, active)
VALUES ('admin', '%s', 'System Administrator', 1)
ON DUPLICATE KEY UPDATE display_name = VALUES(display_name), active = VALUES(active);"""
        % ADMIN_PASSWORD_HASH,
    ]
    for entity in domain_spec["entities"]:
        seed_records = entity.get("seed_records") or []
        if not seed_records:
            continue
        columns = [field["name"] for field in entity["fields"]]
        value_rows = []
        for record in seed_records:
            values = [_sql_seed_value(record.get(column, _default_seed_value(field))) for column, field in zip(columns, entity["fields"])]
            value_rows.append("(" + ", ".join(values) + ")")
        updates = []
        for field in entity["fields"]:
            if field.get("unique") or field.get("searchable"):
                updates.append(f"`{field['name']}` = VALUES(`{field['name']}`)")
        if not updates:
            updates.append(f"`{entity['fields'][0]['name']}` = VALUES(`{entity['fields'][0]['name']}`)")
        inserts.append(
            f"INSERT INTO `{entity['table']}` ({', '.join(f'`{column}`' for column in columns)}) VALUES\n"
            + ",\n".join(value_rows)
            + "\nON DUPLICATE KEY UPDATE "
            + ", ".join(updates)
            + ";"
        )
    return "\n\n".join(inserts) + "\n"


def _default_seed_value(field: dict[str, Any]) -> Any:
    if field.get("type") == "number":
        return field.get("default") or 0
    if field.get("type") == "select":
        options = field.get("options") or []
        return field.get("default") or (options[0] if options else "")
    return field.get("default") or ""


def _sql_seed_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(int(value))
    text = str(value)
    return _sql_quote(text)


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _render_smoke_http() -> str:
    return """GET /health HTTP/1.1
Host: localhost

GET / HTTP/1.1
Host: localhost

GET /admin/login HTTP/1.1
Host: localhost
"""
