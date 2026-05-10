CREATE TYPE user_role AS ENUM ('ADMIN','EDITOR','USER');
CREATE TYPE article_status AS ENUM ('DRAFT','PUBLISHED','ARCHIVED');
CREATE TYPE image_kind AS ENUM ('COVER','CONTENT','AVATAR','OTHER');

CREATE TABLE users (
  id text PRIMARY KEY, email varchar(191) UNIQUE NOT NULL, username varchar(64) UNIQUE NOT NULL,
  password_hash varchar(255) NOT NULL, role user_role NOT NULL DEFAULT 'USER', bio text,
  avatar_url varchar(500), is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE categories (
  id text PRIMARY KEY, name varchar(80) UNIQUE NOT NULL, slug varchar(100) UNIQUE NOT NULL,
  description text, sort_order int NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE tags (
  id text PRIMARY KEY, name varchar(50) UNIQUE NOT NULL, slug varchar(80) UNIQUE NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE images (
  id text PRIMARY KEY, uploader_id text REFERENCES users(id) ON DELETE SET NULL,
  article_id text, url varchar(500) NOT NULL, alt varchar(200), kind image_kind NOT NULL DEFAULT 'OTHER',
  mime_type varchar(100), size_bytes int, width int, height int, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE articles (
  id text PRIMARY KEY, author_id text NOT NULL REFERENCES users(id),
  category_id text REFERENCES categories(id) ON DELETE SET NULL, title varchar(200) NOT NULL,
  slug varchar(220) UNIQUE NOT NULL, summary varchar(500), content_md text NOT NULL, content_html text,
  cover_image_id text UNIQUE, status article_status NOT NULL DEFAULT 'DRAFT', published_at timestamptz,
  view_count int NOT NULL DEFAULT 0, search_text text, created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT fk_cover_image FOREIGN KEY (cover_image_id) REFERENCES images(id) ON DELETE SET NULL
);
ALTER TABLE images ADD CONSTRAINT fk_images_article FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE SET NULL;
CREATE TABLE article_tags (
  article_id text NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  tag_id text NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (article_id, tag_id)
);
CREATE INDEX idx_users_role_active ON users(role,is_active);
CREATE INDEX idx_categories_sort_order ON categories(sort_order);
CREATE INDEX idx_articles_author_created ON articles(author_id,created_at DESC);
CREATE INDEX idx_articles_category_published ON articles(category_id,published_at DESC);
CREATE INDEX idx_articles_status_published ON articles(status,published_at DESC);
CREATE INDEX idx_article_tags_tag_id ON article_tags(tag_id);
CREATE INDEX idx_images_uploader_created ON images(uploader_id,created_at DESC);
CREATE INDEX idx_images_article_id ON images(article_id);
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_articles_search_text_gin ON articles USING gin (to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(summary,'') || ' ' || coalesce(search_text,'')));
CREATE INDEX idx_articles_title_trgm ON articles USING gin (title gin_trgm_ops);