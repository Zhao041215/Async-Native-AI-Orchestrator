<?php
declare(strict_types=1);

require_once __DIR__ . '/app/Support/Env.php';
require_once __DIR__ . '/app/Support/Response.php';
require_once __DIR__ . '/app/Support/Database.php';
require_once __DIR__ . '/app/Controllers/HomeController.php';
require_once __DIR__ . '/app/Controllers/AdminController.php';

Env::load(__DIR__ . '/.env');

$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
$method = strtoupper($_SERVER['REQUEST_METHOD'] ?? 'GET');

if ($path === '/health') {
    Response::json([
        'status' => 'ok',
        'service' => 'v4_test02',
        'stack_pack' => 'php_mysql_single_dir',
        'release_root' => 'single_directory_upload'
    ]);
}

if ($path === '/' && $method === 'GET') {
    (new HomeController())->index();
    return;
}

if ($path === '/admin/login' && $method === 'GET') {
    (new AdminController())->loginForm();
    return;
}

if ($path === '/admin/login' && $method === 'POST') {
    (new AdminController())->login();
    return;
}

Response::notFound('Route not found.');
