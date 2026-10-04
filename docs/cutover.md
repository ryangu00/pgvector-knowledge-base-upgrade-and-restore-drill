# 7. Cut-over day and what the rehearsal did not cover

2026-09-29 cut-over and checks through 2026-10-02; baselines changed between stages.

### Problem
A rehearsal can certify the migration; it cannot certify background daemons, wrapper scripts or the service environment. The upgrade was done in a single evening; this chapter records the sequence and the nine surprises that followed.

### What we built or changed
Sequence (freeze -> anchor -> switch -> migrate -> compare -> resume -> patch writers -> smoke):

| Step | Action |
|---|---|
| Freeze | Stop every writer on both hosts: the maintenance daemon, the HTTP gateway, agent gateways, bridge services; verify zero application processes, zero application connections to the database, zero unfinished jobs |
| Anchor | Physical backup with `pg_basebackup` (6.3 GB, a size consistent with a small archive directory inside the data directory; the daily trimming of chapter 1 is what makes `pg_basebackup` usable here); save the migration records and config next to it; record baseline: 21,810 live pages, 11,607 with the confidential marker, 93 facts |
| Switch | Deploy the new release tree on both hosts; switch both CLI entry points to the same tree; keep the old trees in place for offline rollback; the migrated version is one frozen commit plus ported patches, not a moving branch |
| Write mode | `sync.write_through = false` (owner decision) |
| Migrate | Schema 144 -> 165, rc 0, migration mechanical check; per-page comparison: 0 changes |
| Resume | Start the HTTP gateway and gateways first; daemon with the allowlist active and recommend/dispatch/drain paths disabled |
| Patch writers | 14 writer-script compatibility patches applied (see below) |
| Smoke | New page; update existing page without revision (must be rejected); read revision; update with revision |


**Compatibility-patch deployment (`scripts/deploy-compat-patches.py` pattern).** For each staged file: reverse-apply the diff to the staged result to reconstruct the base, compare it byte for byte with the live file, replace only if identical, back up first, keep the permission bits, verify after copying. If the live file changed since staging, skip and report. Default is dry-run. Offline checks (13 of 13 passed, with 0 calls to the real application, the database or any service): 14 patch files, 14 full copies, 7 source hashes unchanged, 0 literal-secret hits. Transition semantics: non-LLM CLI writers that previously overwrote blindly add `--force` to keep their behavior; agents that write through the API must read the revision first; proper compare-and-set conversion later, one writer at a time.

### Results

| Check | Result |
|---|---|
| Per-page comparison right after migration (21,810 live pages) | 0 changes |
| Production baseline after cut-over | 21,811 live pages = 21,810 + 1 new daily page; confidential 11,607; facts 93 |
| Gateway error log after cut-over | 0 application errors |
| Text retrieval | Fully restored; vector gaps closing, remaining under 0.2% a few days later |
| Compatibility patches | 13/13 offline checks |

### What the rehearsal did not cover

| # | Surprise | Cause | Fix |
|---|---|---|---|
| 1 | The maintenance daemon's chunker version went from 6 to 7, which triggered a full re-import of 10,032 files, 65,790 chunks re-cut, 116,291 chunks pending re-embedding. The guard blocked 2 real conflicts, but the import **resurrected 37 pages that had been soft-deleted weeks earlier** (pages marked confidential) and picked up 14 pages that were not in the database (test leftovers, images, old pages) | File import read soft-deleted rows and, after passing the append-only check, upserted and cleared `deleted_at` | Found by the per-page snapshot diff shortly after cut-over: `RESTORED 37, FM_VALUE_CHANGED 19, NEW_PAGES 15` (19 frontmatter value changes, mostly volatile or derived keys; the 15 new pages are 14 picked up plus one new daily page). All 51 pages soft-deleted again at once (a database backup of the revert was taken first); counts then matched baseline (21,811 / 11,607 / 93) |
| 2 | First guard fix (skip and audit when the database row is soft-deleted; recheck inside the lock) received a FAIL review. The image path lacked the check (a soft-deleted image with an identical hash falls into the re-import branch and clears `deleted_at` even if the file is unchanged), as did the managed-sync code branch. Quantified: 4 soft-deleted image pages (3 of them with the file still in the mirror, so a future full re-import would resurrect them), 0 code pages, 50 markdown pages | Same-class gap in a second entry point | Second fix covering images, code and the managed branch, with lock-and-recheck; reviewed again: PASS; targeted tests 313 pass / 0 fail on 7 files (reproduced independently); same source hash on both hosts after deploy. (Guard details belong to [the sibling book](https://github.com/ryangu00/keeping-agent-knowledge-files-intact)) |
| 3 | The write receipt's `written` flag: after the upgrade it only means "the file mirror was written"; with write-through off it is always false. A wrapper script treated `written: true` as the commit signal and reported failure on every interactive write from 09-29 to 10-02, while pages were in fact stored (its read-back gate and git step were skipped) | Changed meaning of a receipt field | Accept `written: true` or a non-empty `revision` (a not-yet-committed request has none) and still gate on an independent read-back |
| 4 | A harmless-looking upstream notice `UPGRADE_AVAILABLE` printed to stderr in non-interactive mode, merged into a receipt-parsing script's input | New release detected upstream | Set self-upgrade mode to off: upgrades only through our planned procedure |
| 5 | A probe failed although the write succeeded | The new global argument parser swallowed the long flag `--quiet` | Use the short `-q` at 5 call sites |
| 6 | After upgrade, five scheduled memory-pipeline scripts calling the CLI (a `#!/usr/bin/env bun` shebang) all returned 127 | The agent gateway's launchd definition has no PATH, so `bun` is not found; the scripts had been failing since at least 2026-07-28, so not a regression, found only because we checked each dependent script after the upgrade. A related trap: testing over ssh gives a different PATH than the service has | Test with the service's real environment (`ps -E -p <pid>`), never ssh's default; fixing it activates pipelines never validated, so it was a separate task |
| 7 | A writer script built shell commands with `os.system` from model-extracted text, and our compatibility patch copied that line | Patches copy existing code including its bugs | Next-day security review: replaced with argument lists, stdin and a slug/bot allowlist on four copies. Review the lines a compatibility patch touches |
| 8 | A config-drift monitor on the host flagged four FAILs the day after we edited a watched config file | Monitored file changed without updating the service catalog | Bump the catalog revision and accept the drift in the same change |
| 9 | 2,180 image pages have an empty projection revision for good | Upstream rebuilds only markdown and code projections, by design | Not a retrieval problem; documented |

### Power loss (chapter 3, use 4) and later checks
On 2026-10-02 the post-incident comparison found: +2,081 pages, 1 changed page, no resurrection, no loss, vector gaps 161. All dependent scripts were checked one by one: none bypassed the new entry point; the two hosts' consumers all worked.

### Pitfalls

| Symptom | Root cause | Fix |
|---|---|---|
| Wrapper reports failure but the page is stored | Receipt semantics changed | Gate on revision plus independent read-back |
| Soft-deleted pages reappear after a re-import | Import path upserts without checking the soft-delete flag | Skip and audit; recheck inside the page lock; cover every entry point (markdown, image, code, managed) |
| A new release triggers a full re-import by itself | Derived-data version bump (chunker) | Treat any derived-data version bump as "a full reprocess will start": snapshot before, diff after the first cycle |
| Service-run script returns 127 | Minimal PATH under launchd | Absolute paths or explicit PATH in the job definition |

### The rule
A rehearsal certifies the migration; a per-page snapshot (hashes of body, timeline and each frontmatter value, privacy marker, fact and tag counts, soft-delete flag) taken before cut-over and diffed immediately after the first background cycle certifies everything else. Run it again after every large derived-data rebuild.

See [offline reproduction](offline-reproduction.md) for the public adaptations and validation boundary.
