# Clean Release

Upload every file inside this release directory to your website root.

## Baota deployment

1. Upload the contents of `release/` to the website root directory.
2. Select PHP 8.2.
3. Import `database/migrations/001_init.sql`.
4. Import `database/seeders/001_seed.sql`.
5. Copy `.env.example` to `.env` and fill `DB_HOST`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`.
6. Visit `/health`, `/`, and `/admin/login`.

Default seed account: `admin` / `admin123456`.
