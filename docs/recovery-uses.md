# 3. Restores as an operating tool

Dated recovery uses from 2026-07-04 through 2026-10-02; these are distinct baselines.

### Problem
A restore path that is only ever exercised in anger will be broken in anger. Ours was exercised four ways on the dates below; this chapter records them as evidence that the chain works and as patterns.

### What we built or changed
Nothing new: the backup chain of chapter 1 and the isolation pattern of chapter 2, reused.

### Use 1: reconcile before deleting 688 GB of WAL (2026-07-04)
See chapter 1: four-value reconcile (4/4) before any archive trimming.

### Use 2: recovering overwritten rows after an incident (2026-09-27)
A background daemon was re-enabled after weeks. Its first sync treated files as the source of truth and wrote them into the database. Result: 6 pages whose full bodies existed only in the database were overwritten by empty shells or by rewritten versions (the largest page went from 174,798 characters to 433), and 31 pages lost a confidential privacy marker that only the database held. Zero pages were deleted.

Recovery procedure:
1. Stop the daemon (about 5 minutes after the first sync).
2. Take the original rows from two places: the version-history table and an isolated restore of the 04:00 backup (`scripts/extract-rows-from-backup.sh`: the drill script with an extra step that dumps a given list of slugs as JSON rows, tolerating that production changed since 04:00).
3. Write back through the normal write path: for the shell-only pages, use the pre-sync database body as the body and keep the file's fact table; for rewritten pages, keep the file version and append the original database text; for the 31 pages, put the privacy marker back.
4. Verify only on facts, not on the writer's say-so:
   - the set of pages with a recovery commit equals the expected set exactly (6 body pages + 31 marker pages, 2 overlapping = 35 unique);
   - every page's frontmatter after recovery equals frontmatter before, plus the privacy marker, minus the redundant slug key;
   - no line of the pre-sync body is missing, and every fact block is present.
5. Result: set equality true, no lost body lines or fact blocks; frontmatter differences limited to a provenance stamp being added, the privacy marker restored, a redundant key removed, and a date-format normalization.
6. Same day, a full read-only reconciliation of the live database against the file mirror (21,765 pages): 9,835 identical, 11,644 present in the database but absent from the files, 283 with database-only frontmatter keys (230 of them the privacy marker), 3 with body text missing from the file. This is the size of the problem the append-only guard ([sibling book](https://github.com/ryangu00/keeping-agent-knowledge-files-intact)) had to solve before the daemon could be re-enabled.

### Use 3: the rollback anchor (2026-09-29)
For the upgrade (chapters 6-7): a physical backup taken at the freeze (6.3 GB) was the only rollback after the queue-protocol migration. The rehearsal copy was restored from the 04:00 backup (5.7 GB), promoted to writable, migrated, then thrown away and restored again twice, to prove "back to schema 144".

### Use 4: crash recovery (2026-09-29)
After the migration, with a re-embedding job running, the host lost power. PostgreSQL crash recovery restored service; a per-page comparison on 2026-10-02 against the post-cut-over baseline found no losses and no resurrections: 2,081 new pages (session archives), 1 changed page (a daily page legitimately rewritten).

### What did not work
- Relying on the file mirror as a backup of the database: the mirror was missing content that only the database held (11,644 pages), so it could not have been used to recover the 6 overwritten pages.

### Pitfalls

| Symptom | Root cause | Fix |
|---|---|---|
| Recovered page looks right but the confidential marker is gone | Frontmatter merge overwrote the database-only key | Compare frontmatter as dictionaries before and after, with an explicit allowed-differences list |
| "All pages restored" declared from the script's own output | Writer reported success | Re-derive the expected set independently and compare for equality |

### The rule
Make the restore chain do real work (incident recovery, rehearsals, rollback anchors); verify any recovery by set equality and per-field comparison, not by the recovery script's own report.

See [offline reproduction](offline-reproduction.md) for the public adaptations and validation boundary.
