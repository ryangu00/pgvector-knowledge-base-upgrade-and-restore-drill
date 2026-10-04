-- Example schema only; replace these paths with the actual isolated copy paths.
UPDATE sources SET local_path = replace(local_path, 'source-content/', 'drill-content/')
WHERE local_path LIKE 'source-content/%';
UPDATE config SET value = 'drill-content/' WHERE key = 'sync.repo_path';
