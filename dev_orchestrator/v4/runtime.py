from __future__ import annotations

from pathlib import Path
from typing import Any

from dev_orchestrator.v4.models import slugify


class PackageMaterializer:
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
            self._ensure_php_mysql_single_dir(project, release_root)
        else:
            self._ensure_generic_release(project, release_root, product_contract)

        evidence_dir = project_root / ".v4" / "packages"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_dir / f"{package['package_key']}.json"
        evidence_path.write_text(
            (
                "{\n"
                f'  "package_key": "{package["package_key"]}",\n'
                f'  "domain": "{package["domain"]}",\n'
                f'  "role": "{package["role"]}",\n'
                f'  "release_root": "{product_contract.get("release_root", "release")}"\n'
                "}\n"
            ),
            encoding="utf-8",
        )
        return {
            "project_root": str(project_root),
            "release_root": str(release_root),
            "evidence_path": str(evidence_path),
            "package_key": package["package_key"],
        }

    def project_root(self, project: dict[str, Any]) -> Path:
        configured = str(project.get("project_path") or "").strip()
        if configured:
            return Path(configured).resolve()
        return (self.workspace_root / slugify(project.get("name") or project.get("title") or project["id"])).resolve()

    def _write_once(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(text, encoding="utf-8")

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
require_once __DIR__ . '/app/Controllers/HomeController.php';
require_once __DIR__ . '/app/Controllers/AdminController.php';

Env::load(__DIR__ . '/.env');

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
            Response::html('<h1>Welcome, ' . htmlspecialchars((string) $admin['display_name'], ENT_QUOTES, 'UTF-8') . '</h1><p>The application is connected to the database.</p>');
        } catch (Throwable $error) {
            $this->loginForm('Database is not configured yet. Check .env and imported SQL files.');
        }
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
