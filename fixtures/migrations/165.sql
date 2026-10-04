-- Synthetic SQL migration; this is not one of the historical application migrations.
UPDATE config SET value = '165' WHERE key = 'version';
