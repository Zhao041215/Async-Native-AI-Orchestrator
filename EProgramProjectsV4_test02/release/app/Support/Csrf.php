<?php
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
