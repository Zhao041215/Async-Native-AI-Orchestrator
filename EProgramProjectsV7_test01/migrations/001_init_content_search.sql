CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE "Article"
ADD COLUMN IF NOT EXISTS search_vector tsvector GENERATED ALWAYS AS (
  setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
  setweight(to_tsvector('simple', coalesce(excerpt, '')), 'B') ||
  setweight(to_tsvector('simple', coalesce(contentMd, '')), 'C')
) STORED;

CREATE INDEX IF NOT EXISTS idx_article_search_vector
ON "Article" USING GIN (search_vector);

CREATE INDEX IF NOT EXISTS idx_article_title_trgm
ON "Article" USING GIN (title gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_article_active_updated
ON "Article" (status, deletedAt, updatedAt DESC);

CREATE INDEX IF NOT EXISTS idx_article_category_active_updated
ON "Article" (categoryId, status, deletedAt, updatedAt DESC);

CREATE INDEX IF NOT EXISTS idx_category_sort_active
ON "Category" (sortOrder ASC, deletedAt ASC, createdAt DESC);

INSERT INTO "Admin" (id, username, "passwordHash", "createdAt", "updatedAt")
SELECT 'seed_admin', 'admin', 'CHANGE_ME_HASH', now(), now()
WHERE NOT EXISTS (SELECT 1 FROM "Admin" WHERE username = 'admin');