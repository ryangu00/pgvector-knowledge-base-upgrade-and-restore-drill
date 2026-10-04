-- Synthetic example schema, not an export of the historical application.
CREATE TABLE pages (
  id text PRIMARY KEY, slug text UNIQUE, body text, timeline text,
  frontmatter jsonb, deleted_at timestamptz, updated_at timestamptz,
  fact_count integer DEFAULT 0, tag_count integer DEFAULT 0,
  text_projection_revision text
);
INSERT INTO pages(id,slug,body,timeline,frontmatter) VALUES
 ('a','fixture/alpha','original alpha','','{}'),
 ('b','fixture/beta','original beta','','{}'),
 ('c','fixture/gamma','original gamma','','{}');
CREATE TABLE config(key text PRIMARY KEY, value text);
INSERT INTO config VALUES ('version','144'), ('sync.repo_path','source-content/');
CREATE TABLE sources(local_path text);
INSERT INTO sources VALUES ('source-content/example');
CREATE TABLE jobs(status text);
INSERT INTO jobs VALUES ('active'), ('running');
