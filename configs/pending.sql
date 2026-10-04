SELECT count(*) FROM pages WHERE deleted_at IS NULL AND text_projection_revision IS NULL;
