from __future__ import annotations

import os
import re
import json
from pathlib import Path
from typing import Any

from dev_orchestrator.v5.release_generator import materialize_php_mysql_single_dir
from dev_orchestrator.v4.models import slugify


class PackageMaterializer:
    _WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root

    def execute_package(
        self,
        project: dict[str, Any],
        run: dict[str, Any],
        package: dict[str, Any],
        product_contract: dict[str, Any],
    ) -> dict[str, Any]:
        project_root = self.project_root(project)
        release_root = project_root / product_contract.get("release_root", "release")
        release_root.mkdir(parents=True, exist_ok=True)
        stack_pack = product_contract["stack_pack"]
        if stack_pack == "php_mysql_single_dir":
            solution_graph = (run.get("metadata") or {}).get("solution_graph") or {}
            materialize_php_mysql_single_dir(self, project, run, product_contract, release_root, solution_graph)
        else:
            self._ensure_generic_release(project, release_root, product_contract)

        evidence_dir = project_root / ".v4" / "packages"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_dir / f"{package['package_key']}.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "schema_version": "4.5",
                    "package_key": package["package_key"],
                    "domain": package["domain"],
                    "role": package["role"],
                    "subsystem": package.get("subsystem", package["domain"]),
                    "release_root": product_contract.get("release_root", "release"),
                    "allowed_paths": package.get("allowed_paths", []),
                    "forbidden_paths": package.get("forbidden_paths", []),
                    "requirements": package.get("requirements", []),
                    "acceptance_evidence": ["release materialized", "package boundary recorded"],
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "project_root": str(project_root),
            "release_root": str(release_root),
            "evidence_path": str(evidence_path),
            "package_key": package["package_key"],
            "subsystem": package.get("subsystem", package["domain"]),
            "allowed_paths": package.get("allowed_paths", []),
            "forbidden_paths": package.get("forbidden_paths", []),
            "requirements_mapping": package.get("requirements", []),
            "patch_manifest": {
                "changed_files": self._release_files_for_package(release_root, package),
                "boundary": {
                    "allowed_paths": package.get("allowed_paths", []),
                    "forbidden_paths": package.get("forbidden_paths", []),
                },
            },
            "acceptance_evidence": ["release materialized", "package boundary recorded"],
        }

    def project_root(self, project: dict[str, Any]) -> Path:
        configured = str(project.get("project_path") or "").strip()
        if configured:
            if self._can_use_configured_project_path(configured):
                return Path(configured).expanduser().resolve()
            if self._looks_like_windows_absolute_path(configured):
                return self._fallback_project_root(project)
            candidate = (self.workspace_root / Path(configured)).expanduser().resolve()
            if self._is_within_workspace(candidate):
                return candidate
        return self._fallback_project_root(project)

    def _fallback_project_root(self, project: dict[str, Any]) -> Path:
        return (self.workspace_root / slugify(project.get("name") or project.get("title") or project["id"])).resolve()

    def _can_use_configured_project_path(self, configured: str) -> bool:
        candidate = Path(configured)
        if os.name == "nt":
            return candidate.is_absolute()
        return candidate.is_absolute() and not self._looks_like_windows_absolute_path(configured)

    def _looks_like_windows_absolute_path(self, configured: str) -> bool:
        return bool(self._WINDOWS_ABSOLUTE_PATH.match(configured))

    def _is_within_workspace(self, candidate: Path) -> bool:
        try:
            return candidate.is_relative_to(self.workspace_root)
        except AttributeError:  # pragma: no cover
            workspace = str(self.workspace_root)
            return str(candidate).startswith(workspace)

    def _write_once(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(text, encoding="utf-8")

    def _release_files(self, release_root: Path) -> list[str]:
        files = []
        for path in sorted(release_root.rglob("*")):
            if path.is_file():
                files.append(str(path.relative_to(release_root)))
            if len(files) >= 200:
                break
        return files

    def _release_files_for_package(self, release_root: Path, package: dict[str, Any]) -> list[str]:
        allowed = [str(item).replace("\\", "/").removeprefix("release/") for item in package.get("allowed_paths", [])]
        forbidden = [str(item).replace("\\", "/").removeprefix("release/") for item in package.get("forbidden_paths", [])]
        files = []
        for relative in self._release_files(release_root):
            normalized = relative.replace("\\", "/")
            if allowed and not any(self._match_path(normalized, pattern) for pattern in allowed):
                continue
            if forbidden and any(self._match_path(normalized, pattern) for pattern in forbidden):
                continue
            files.append(relative)
        return files

    def _match_path(self, path: str, pattern: str) -> bool:
        from fnmatch import fnmatch

        pattern = pattern or ""
        return fnmatch(path, pattern)

    def _ensure_php_mysql_single_dir(self, project: dict[str, Any], release_root: Path) -> None:
        title = str(project.get("title") or project.get("name") or "V4 Product")
        slug = slugify(title, "v4_product").replace("-", "_")
        self._write_once(
            release_root / "index.php",
            f"""<?php
declare(strict_types=1);

require_once __DIR__ . '/app/Support/Env.php';
require_once __DIR__ . '/app/Support/Response.php';
require_once __DIR__ . '/app/Support/Database.php';
require_once __DIR__ . '/app/Support/Auth.php';
require_once __DIR__ . '/app/Support/Csrf.php';
require_once __DIR__ . '/app/Support/Validator.php';
require_once __DIR__ . '/app/Repositories/EmployeeRepository.php';
require_once __DIR__ . '/app/Services/DashboardService.php';
require_once __DIR__ . '/app/Controllers/HomeController.php';
require_once __DIR__ . '/app/Controllers/AdminController.php';
require_once __DIR__ . '/app/Controllers/EmployeeController.php';

Env::load(__DIR__ . '/.env');
Auth::start();

$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
$method = strtoupper($_SERVER['REQUEST_METHOD'] ?? 'GET');

if ($path === '/health') {{
    Response::json([
        'status' => 'ok',
        'service' => '{slug}',
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

if ($path === '/employees' && $method === 'GET') {{
    (new EmployeeController())->index();
    return;
}}

if ($path === '/admin/employees/create' && $method === 'GET') {{
    (new EmployeeController())->create();
    return;
}}

if ($path === '/admin/employees' && $method === 'POST') {{
    (new EmployeeController())->store();
    return;
}}

if (preg_match('#^/admin/employees/(\\d+)/edit$#', $path, $matches) && $method === 'GET') {{
    (new EmployeeController())->edit((int) $matches[1]);
    return;
}}

if (preg_match('#^/admin/employees/(\\d+)$#', $path, $matches) && $method === 'POST') {{
    (new EmployeeController())->update((int) $matches[1]);
    return;
}}

if (preg_match('#^/admin/employees/(\\d+)/delete$#', $path, $matches) && $method === 'POST') {{
    (new EmployeeController())->delete((int) $matches[1]);
    return;
}}

Response::notFound('Route not found.');
""",
        )
        self._write_once(
            release_root / ".env.example",
            f"""APP_ENV=production
APP_NAME={slug}
APP_KEY=change-me
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME={slug}
DB_USER=root
DB_PASSWORD=
""",
        )
        self._write_once(
            release_root / ".htaccess",
            """Options -Indexes
DirectoryIndex index.php

<FilesMatch "^\\.env">
  Require all denied
</FilesMatch>

RewriteEngine On
RewriteCond %{REQUEST_FILENAME} !-f
RewriteCond %{REQUEST_FILENAME} !-d
RewriteRule ^ index.php [QSA,L]

RedirectMatch 403 ^/(app|config|database|storage|tests)(/|$)
""",
        )
        self._write_once(
            release_root / ".user.ini",
            """open_basedir=.:/tmp/
expose_php=0
display_errors=0
log_errors=1
""",
        )
        self._write_once(
            release_root / "nginx.sample.conf",
            """location ~ ^/(app|config|database|storage|tests)/ {
    deny all;
}

location / {
    try_files $uri $uri/ /index.php?$query_string;
}

location ~ /\\.env {
    deny all;
}
""",
        )
        self._write_once(
            release_root / "app" / "Support" / "Env.php",
            """<?php
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
""",
        )
        self._write_once(
            release_root / "app" / "Support" / "Response.php",
            """<?php
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
""",
        )
        self._write_once(
            release_root / "app" / "Support" / "Database.php",
            """<?php
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
""",
        )
        self._write_once(
            release_root / "app" / "Support" / "Auth.php",
            """<?php
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
""",
        )
        self._write_once(
            release_root / "app" / "Support" / "Csrf.php",
            """<?php
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
""",
        )
        self._write_once(
            release_root / "app" / "Support" / "Validator.php",
            """<?php
declare(strict_types=1);

final class Validator
{
    public static function employee(array $input): array
    {
        $errors = [];
        $data = [
            'name' => trim((string) ($input['name'] ?? '')),
            'employee_no' => strtoupper(trim((string) ($input['employee_no'] ?? ''))),
            'gender' => trim((string) ($input['gender'] ?? '')),
            'department' => trim((string) ($input['department'] ?? '')),
            'position' => trim((string) ($input['position'] ?? '')),
            'phone' => trim((string) ($input['phone'] ?? '')),
            'email' => trim((string) ($input['email'] ?? '')),
            'hire_date' => trim((string) ($input['hire_date'] ?? '')),
            'status' => trim((string) ($input['status'] ?? 'active')),
        ];
        foreach (['name', 'employee_no', 'department', 'position', 'hire_date'] as $field) {
            if ($data[$field] === '') {
                $errors[$field] = 'This field is required.';
            }
        }
        if (!in_array($data['gender'], ['male', 'female'], true)) {
            $errors['gender'] = 'Choose a valid gender.';
        }
        if (!in_array($data['status'], ['active', 'inactive'], true)) {
            $errors['status'] = 'Choose a valid status.';
        }
        if ($data['phone'] !== '' && !preg_match('/^[0-9+\\-\\s]{6,30}$/', $data['phone'])) {
            $errors['phone'] = 'Phone format is invalid.';
        }
        if ($data['email'] !== '' && !filter_var($data['email'], FILTER_VALIDATE_EMAIL)) {
            $errors['email'] = 'Email format is invalid.';
        }
        if ($data['hire_date'] !== '' && !preg_match('/^\\d{4}-\\d{2}-\\d{2}$/', $data['hire_date'])) {
            $errors['hire_date'] = 'Use YYYY-MM-DD.';
        }
        return [$data, $errors];
    }
}
""",
        )
        self._write_once(
            release_root / "app" / "Controllers" / "HomeController.php",
            f"""<?php
declare(strict_types=1);

final class HomeController
{{
    public function index(): void
    {{
        Response::html(<<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <main class="shell">
    <section class="hero">
      <p class="eyebrow">V4 clean release</p>
      <h1>{title}</h1>
      <p>This product was generated as a single-directory deployable PHP/MySQL release.</p>
      <div class="actions">
        <a class="button" href="/admin/login">Admin Login</a>
        <a class="button secondary" href="/health">Health</a>
      </div>
    </section>
    <section class="panel">
      <h2>Deployment contract</h2>
      <ul>
        <li>Upload the release directory contents to the web root.</li>
        <li>Copy .env.example to .env and fill database credentials.</li>
        <li>Import migration and seed SQL files in order.</li>
      </ul>
    </section>
  </main>
</body>
</html>
HTML);
    }}
}}
""",
        )
        self._write_once(
            release_root / "app" / "Controllers" / "AdminController.php",
            """<?php
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
        $username = trim((string) ($_POST['username'] ?? ''));
        $password = (string) ($_POST['password'] ?? '');
        try {
            $pdo = Database::pdo();
            $statement = $pdo->prepare('SELECT id, username, password_hash, display_name FROM admin_users WHERE username = ? AND active = 1 LIMIT 1');
            $statement->execute([$username]);
            $admin = $statement->fetch();
            if (!$admin || !password_verify($password, (string) $admin['password_hash'])) {
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
            $cards = [
                ['label' => 'Total employees', 'value' => 0],
                ['label' => 'Active employees', 'value' => 0],
                ['label' => 'Departments', 'value' => 0],
            ];
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
      <a href="/employees">Employees</a>
      <form action="/admin/logout" method="post">{$csrf}<button type="submit">Logout {$name}</button></form>
    </nav>
    <section class="hero compact">
      <p class="eyebrow">Operations</p>
      <h1>Personnel dashboard</h1>
      <p>Review workforce totals and jump into employee maintenance.</p>
      <a class="button" href="/admin/employees/create">New employee</a>
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
""",
        )
        self._write_once(
            release_root / "app" / "Repositories" / "EmployeeRepository.php",
            """<?php
declare(strict_types=1);

final class EmployeeRepository
{
    public function __construct(private ?PDO $pdo = null)
    {
        $this->pdo = $pdo ?: Database::pdo();
    }

    public function paginate(string $keyword = '', int $page = 1, int $perPage = 10): array
    {
        $offset = max(0, ($page - 1) * $perPage);
        $where = '';
        $params = [];
        if ($keyword !== '') {
            $where = 'WHERE name LIKE ? OR employee_no LIKE ? OR department LIKE ?';
            $like = '%' . $keyword . '%';
            $params = [$like, $like, $like];
        }
        $count = $this->pdo->prepare("SELECT COUNT(*) AS total FROM employees {$where}");
        $count->execute($params);
        $total = (int) ($count->fetch()['total'] ?? 0);
        $statement = $this->pdo->prepare("SELECT * FROM employees {$where} ORDER BY id DESC LIMIT {$perPage} OFFSET {$offset}");
        $statement->execute($params);
        return ['items' => $statement->fetchAll(), 'total' => $total, 'page' => $page, 'pages' => max(1, (int) ceil($total / $perPage))];
    }

    public function find(int $id): ?array
    {
        $statement = $this->pdo->prepare('SELECT * FROM employees WHERE id = ? LIMIT 1');
        $statement->execute([$id]);
        $row = $statement->fetch();
        return $row ?: null;
    }

    public function create(array $data): int
    {
        $statement = $this->pdo->prepare('INSERT INTO employees (name, employee_no, gender, department, position, phone, email, hire_date, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)');
        $statement->execute([$data['name'], $data['employee_no'], $data['gender'], $data['department'], $data['position'], $data['phone'], $data['email'], $data['hire_date'], $data['status']]);
        return (int) $this->pdo->lastInsertId();
    }

    public function update(int $id, array $data): void
    {
        $statement = $this->pdo->prepare('UPDATE employees SET name = ?, employee_no = ?, gender = ?, department = ?, position = ?, phone = ?, email = ?, hire_date = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?');
        $statement->execute([$data['name'], $data['employee_no'], $data['gender'], $data['department'], $data['position'], $data['phone'], $data['email'], $data['hire_date'], $data['status'], $id]);
    }

    public function delete(int $id): void
    {
        $statement = $this->pdo->prepare('DELETE FROM employees WHERE id = ?');
        $statement->execute([$id]);
    }

    public function employeeNoExists(string $employeeNo, ?int $exceptId = null): bool
    {
        $sql = 'SELECT id FROM employees WHERE employee_no = ?';
        $params = [$employeeNo];
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
        $total = (int) $this->pdo->query('SELECT COUNT(*) AS value FROM employees')->fetch()['value'];
        $active = (int) $this->pdo->query("SELECT COUNT(*) AS value FROM employees WHERE status = 'active'")->fetch()['value'];
        $departments = (int) $this->pdo->query('SELECT COUNT(DISTINCT department) AS value FROM employees')->fetch()['value'];
        return ['total' => $total, 'active' => $active, 'departments' => $departments];
    }
}
""",
        )
        self._write_once(
            release_root / "app" / "Services" / "DashboardService.php",
            """<?php
declare(strict_types=1);

final class DashboardService
{
    public function __construct(private ?EmployeeRepository $employees = null)
    {
        $this->employees = $employees ?: new EmployeeRepository();
    }

    public function cards(): array
    {
        $stats = $this->employees->statistics();
        return [
            ['label' => 'Total employees', 'value' => $stats['total']],
            ['label' => 'Active employees', 'value' => $stats['active']],
            ['label' => 'Departments', 'value' => $stats['departments']],
        ];
    }
}
""",
        )
        self._write_once(
            release_root / "app" / "Controllers" / "EmployeeController.php",
            """<?php
declare(strict_types=1);

final class EmployeeController
{
    private EmployeeRepository $employees;

    public function __construct()
    {
        $this->employees = new EmployeeRepository();
    }

    public function index(): void
    {
        $keyword = trim((string) ($_GET['q'] ?? ''));
        $page = max(1, (int) ($_GET['page'] ?? 1));
        try {
            $data = $this->employees->paginate($keyword, $page);
        } catch (Throwable $error) {
            $data = ['items' => [], 'total' => 0, 'page' => 1, 'pages' => 1];
        }
        $rows = '';
        foreach ($data['items'] as $employee) {
            $id = (int) $employee['id'];
            $name = htmlspecialchars((string) $employee['name'], ENT_QUOTES, 'UTF-8');
            $no = htmlspecialchars((string) $employee['employee_no'], ENT_QUOTES, 'UTF-8');
            $department = htmlspecialchars((string) $employee['department'], ENT_QUOTES, 'UTF-8');
            $position = htmlspecialchars((string) $employee['position'], ENT_QUOTES, 'UTF-8');
            $status = htmlspecialchars((string) $employee['status'], ENT_QUOTES, 'UTF-8');
            $csrf = Csrf::field();
            $actions = '';
            if (Auth::user()) {
                $actions = '<a href="/admin/employees/' . $id . '/edit">Edit</a>'
                    . '<form action="/admin/employees/' . $id . '/delete" method="post" onsubmit="return confirm(&quot;Delete this employee?&quot;)">'
                    . $csrf
                    . '<button type="submit">Delete</button></form>';
            }
            $rows .= '<tr><td>' . $no . '</td><td>' . $name . '</td><td>' . $department . '</td><td>' . $position . '</td><td>' . $status . '</td><td class="actions-cell">' . $actions . '</td></tr>';
        }
        if ($rows === '') {
            $rows = '<tr><td colspan="6">No employees found.</td></tr>';
        }
        $q = htmlspecialchars($keyword, ENT_QUOTES, 'UTF-8');
        $create = Auth::user() ? '<a class="button" href="/admin/employees/create">New employee</a>' : '<a class="button" href="/admin/login">Admin login</a>';
        Response::html(<<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Employees</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <main class="shell">
    <nav class="topbar"><a href="/">Home</a><a href="/employees">Employees</a><a href="/admin/dashboard">Dashboard</a></nav>
    <section class="panel">
      <div class="section-title"><h1>Employees</h1>{$create}</div>
      <form class="search" method="get" action="/employees">
        <input name="q" value="{$q}" placeholder="Search by name, number, department">
        <button type="submit">Search</button>
      </form>
      <table><thead><tr><th>No.</th><th>Name</th><th>Department</th><th>Position</th><th>Status</th><th>Actions</th></tr></thead><tbody>{$rows}</tbody></table>
    </section>
  </main>
</body>
</html>
HTML);
    }

    public function create(): void
    {
        Auth::requireAdmin();
        $this->form('Create employee', '/admin/employees', []);
    }

    public function store(): void
    {
        Auth::requireAdmin();
        Csrf::verify();
        [$data, $errors] = Validator::employee($_POST);
        if (!$errors && $this->employees->employeeNoExists($data['employee_no'])) {
            $errors['employee_no'] = 'Employee number already exists.';
        }
        if ($errors) {
            $this->form('Create employee', '/admin/employees', $data, $errors);
            return;
        }
        $this->employees->create($data);
        header('Location: /employees');
        exit;
    }

    public function edit(int $id): void
    {
        Auth::requireAdmin();
        $employee = $this->employees->find($id);
        if (!$employee) {
            Response::notFound('Employee not found.');
        }
        $this->form('Edit employee', "/admin/employees/{$id}", $employee);
    }

    public function update(int $id): void
    {
        Auth::requireAdmin();
        Csrf::verify();
        [$data, $errors] = Validator::employee($_POST);
        if (!$errors && $this->employees->employeeNoExists($data['employee_no'], $id)) {
            $errors['employee_no'] = 'Employee number already exists.';
        }
        if ($errors) {
            $this->form('Edit employee', "/admin/employees/{$id}", $data, $errors);
            return;
        }
        $this->employees->update($id, $data);
        header('Location: /employees');
        exit;
    }

    public function delete(int $id): void
    {
        Auth::requireAdmin();
        Csrf::verify();
        $this->employees->delete($id);
        header('Location: /employees');
        exit;
    }

    private function form(string $title, string $action, array $values, array $errors = []): void
    {
        $field = fn(string $key, string $default = '') => htmlspecialchars((string) ($values[$key] ?? $default), ENT_QUOTES, 'UTF-8');
        $error = fn(string $key) => isset($errors[$key]) ? '<small class="error">' . htmlspecialchars($errors[$key], ENT_QUOTES, 'UTF-8') . '</small>' : '';
        $csrf = Csrf::field();
        $gender = $field('gender', 'male');
        $status = $field('status', 'active');
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
    <nav class="topbar"><a href="/employees">Employees</a><a href="/admin/dashboard">Dashboard</a></nav>
    <form class="panel form-grid" action="{$action}" method="post">
      <h1>{$title}</h1>
      {$csrf}
      <label>Name<input name="name" value="{$field('name')}" required>{$error('name')}</label>
      <label>Employee no<input name="employee_no" value="{$field('employee_no')}" required>{$error('employee_no')}</label>
      <label>Gender<select name="gender"><option value="male">Male</option><option value="female">Female</option></select>{$error('gender')}</label>
      <label>Department<input name="department" value="{$field('department')}" required>{$error('department')}</label>
      <label>Position<input name="position" value="{$field('position')}" required>{$error('position')}</label>
      <label>Phone<input name="phone" value="{$field('phone')}" pattern="[0-9+\\-\\s]{6,30}">{$error('phone')}</label>
      <label>Email<input name="email" type="email" value="{$field('email')}">{$error('email')}</label>
      <label>Hire date<input name="hire_date" type="date" value="{$field('hire_date')}" required>{$error('hire_date')}</label>
      <label>Status<select name="status"><option value="active">Active</option><option value="inactive">Inactive</option></select>{$error('status')}</label>
      <button type="submit">Save</button>
    </form>
  </main>
  <script>
    document.querySelector('[name="gender"]').value = "{$gender}";
    document.querySelector('[name="status"]').value = "{$status}";
  </script>
</body>
</html>
HTML);
    }
}
""",
        )
        self._write_once(
            release_root / "assets" / "app.css",
            """body {
  margin: 0;
  font-family: Arial, Helvetica, sans-serif;
  color: #1f2937;
  background: #f6f7fb;
}
.shell, .login-shell {
  width: min(920px, calc(100% - 32px));
  margin: 0 auto;
  padding: 48px 0;
}
.hero, .panel, .login-card {
  background: #fff;
  border: 1px solid #d9dee8;
  border-radius: 8px;
  padding: 28px;
  box-shadow: 0 12px 30px rgba(16, 24, 40, 0.08);
}
.panel {
  margin-top: 18px;
}
.eyebrow {
  color: #475569;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 12px;
}
.actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.button, button {
  display: inline-flex;
  align-items: center;
  min-height: 40px;
  padding: 0 16px;
  border-radius: 6px;
  border: 1px solid #1f2937;
  background: #1f2937;
  color: #fff;
  text-decoration: none;
  cursor: pointer;
}
.button.secondary {
  background: #fff;
  color: #1f2937;
}
label {
  display: grid;
  gap: 6px;
  margin: 14px 0;
}
input {
  min-height: 38px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 0 10px;
}
.error {
  color: #b42318;
}
.topbar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 18px;
}
.topbar a {
  color: #1f2937;
  font-weight: 700;
  text-decoration: none;
}
.topbar form {
  margin-left: auto;
}
.compact {
  padding: 22px;
}
.stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-top: 18px;
}
.stat {
  background: #fff;
  border: 1px solid #d9dee8;
  border-radius: 8px;
  padding: 18px;
}
.stat span {
  display: block;
  color: #64748b;
  font-size: 13px;
}
.stat strong {
  display: block;
  margin-top: 8px;
  font-size: 28px;
}
.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.search {
  display: flex;
  gap: 10px;
  margin: 16px 0;
}
.search input {
  flex: 1;
}
table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
}
th, td {
  border-bottom: 1px solid #e2e8f0;
  padding: 10px;
  text-align: left;
}
th {
  color: #475569;
  font-size: 13px;
}
.actions-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.actions-cell form {
  display: inline;
}
.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.form-grid h1 {
  grid-column: 1 / -1;
}
.form-grid button {
  width: fit-content;
}
select {
  min-height: 40px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 0 10px;
  background: #fff;
}
small.error {
  display: block;
}
@media (max-width: 720px) {
  .stats, .form-grid {
    grid-template-columns: 1fr;
  }
  .section-title, .search, .topbar {
    align-items: stretch;
    flex-direction: column;
  }
  table {
    display: block;
    overflow-x: auto;
  }
}
""",
        )
        self._write_once(
            release_root / "config" / "database.php",
            """<?php
declare(strict_types=1);

return [
    'host' => Env::get('DB_HOST', '127.0.0.1'),
    'port' => Env::get('DB_PORT', '3306'),
    'database' => Env::get('DB_NAME', ''),
    'username' => Env::get('DB_USER', 'root'),
    'password' => Env::get('DB_PASSWORD', ''),
];
""",
        )
        self._write_once(
            release_root / "database" / "migrations" / "001_init.sql",
            """CREATE TABLE IF NOT EXISTS admin_users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(80) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  display_name VARCHAR(120) NOT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS employees (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  employee_no VARCHAR(80) NOT NULL UNIQUE,
  gender ENUM('male', 'female') NOT NULL,
  department VARCHAR(120) NOT NULL,
  position VARCHAR(120) NOT NULL,
  phone VARCHAR(40) NULL,
  email VARCHAR(160) NULL,
  hire_date DATE NOT NULL,
  status ENUM('active', 'inactive') NOT NULL DEFAULT 'active',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NULL DEFAULT NULL,
  INDEX idx_employees_department (department),
  INDEX idx_employees_status (status),
  INDEX idx_employees_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS audit_events (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  actor VARCHAR(120) NOT NULL,
  event_type VARCHAR(120) NOT NULL,
  payload JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
""",
        )
        self._write_once(
            release_root / "database" / "seeders" / "001_seed.sql",
            """INSERT INTO admin_users (username, password_hash, display_name, active)
VALUES ('admin', '$2y$10$X8T4mxCZh7htMZVrL81a4.yT2Rwjd7s1N46T0jnA.F6pV3OyHq/2S', 'System Administrator', 1)
ON DUPLICATE KEY UPDATE display_name = VALUES(display_name), active = VALUES(active);

INSERT INTO employees (name, employee_no, gender, department, position, phone, email, hire_date, status) VALUES
('Alice Chen', 'EMP-1001', 'female', 'Operations', 'Operations Manager', '13800000001', 'alice@example.com', '2022-03-01', 'active'),
('Ben Wang', 'EMP-1002', 'male', 'Engineering', 'Backend Engineer', '13800000002', 'ben@example.com', '2021-06-15', 'active'),
('Cathy Liu', 'EMP-1003', 'female', 'Finance', 'Accountant', '13800000003', 'cathy@example.com', '2020-10-20', 'inactive')
ON DUPLICATE KEY UPDATE name = VALUES(name), department = VALUES(department), position = VALUES(position), status = VALUES(status);
""",
        )
        self._write_once(
            release_root / "storage" / "logs" / ".gitkeep",
            "",
        )
        self._write_once(
            release_root / "tests" / "smoke.http",
            """GET /health
GET /
GET /admin/login
""",
        )
        self._write_once(
            release_root / "README.md",
            """# Clean Release

Upload every file inside this release directory to your website root.

## Baota deployment

1. Upload the contents of `release/` to the website root directory.
2. Select PHP 8.2.
3. Import `database/migrations/001_init.sql`.
4. Import `database/seeders/001_seed.sql`.
5. Copy `.env.example` to `.env` and fill `DB_HOST`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`.
6. Visit `/health`, `/`, and `/admin/login`.

Default seed account: `admin` / `admin123456`.
""",
        )

    def _ensure_generic_release(self, project: dict[str, Any], release_root: Path, product_contract: dict[str, Any]) -> None:
        title = str(project.get("title") or project.get("name") or "V4 Product")
        self._write_once(
            release_root / "README.md",
            f"""# {title}

This is a V4 clean release for stack pack `{product_contract['stack_pack']}`.

Upload or start this release directory according to the stack-specific command below.

Home: `{product_contract.get('home_entry', '/')}`
Health: `{product_contract.get('health_entry', '/health')}`
""",
        )
        self._write_once(
            release_root / ".env.example",
            """APP_ENV=production
DATABASE_URL=postgresql://user:password@localhost:5432/app
""",
        )
        self._write_once(
            release_root / "index.html",
            f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title></head>
<body>
  <main>
    <h1>{title}</h1>
    <p>V4 clean release generated for {product_contract['stack_pack']}.</p>
    <a href="{product_contract.get('health_entry', '/health')}">Health</a>
  </main>
</body>
</html>
""",
        )
