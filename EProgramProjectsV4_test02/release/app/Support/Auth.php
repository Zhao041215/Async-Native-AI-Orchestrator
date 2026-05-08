<?php
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
