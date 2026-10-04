SELECT
  (SELECT count(*) FROM sources WHERE local_path LIKE 'source-content/%') +
  (SELECT count(*) FROM config WHERE value LIKE '%source-content/%');
