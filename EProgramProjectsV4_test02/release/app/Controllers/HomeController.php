<?php
declare(strict_types=1);

final class HomeController
{
    public function index(): void
    {
        Response::html(<<<HTML
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>V4_test02</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <main class="shell">
    <section class="hero">
      <p class="eyebrow">V4 clean release</p>
      <h1>V4_test02</h1>
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
    }
}
