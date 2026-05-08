<?php
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
