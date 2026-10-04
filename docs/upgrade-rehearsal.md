# 6. Rehearsing the database upgrade on a restored copy

2026-09-29 rehearsal unless the previous 2026-08-29 round is named; elapsed migration rates omitted.

### Problem
Upgrade across 12 minor versions of the knowledge base: 21 schema migrations (144 -> 165), none with a down migration; a changed job-queue protocol; a changed write protocol; a fork with local patches. A failed upgrade can only be undone by restoring the pre-upgrade database. So the rehearsal must be faithful enough to find what production would find.

### What we built or changed

**1. A static assessment first (read-only, no database).** Source of every claim recorded as `file:line` at a fixed target commit; explicit list of what was NOT verified (production schema version, actual data size, running service environment, queue state). It analyzed all 21 migrations:

| Class | Migrations | What they do (generic) |
|---|---|---|
| High risk | 3 | (1) job-queue table: exclusive lock, errors if any job is active, installs new protocol triggers; (2) pages get a non-null random revision identifier per row plus snapshot columns: full-table write; (3) one UPDATE over all pages clearing the text-projection revision and embedding signature, scheduling rebuild jobs and replacing the search-vector trigger: keyword retrieval empty until the rebuild |
| Medium | 9 | Widening a CHECK constraint (re-validate scan); rewriting client grants (revokes illegal agent scopes: a real permission change); building a withdrawal table from facts; a statistics object plus ANALYZE and a plain (non-concurrent) CREATE INDEX under statement_timeout 30 s and lock_timeout 2 s; scanning ~22k frontmatter blobs to backfill derived state; writer-guard triggers; effect-table rewrite; a concurrent index |
| Low to medium | 3 | Mostly new tables and indexes |
| Low | 6 | New empty tables or metadata |

Finding stated carefully: none of the 21 migrations directly rewrites page text, timeline or frontmatter or bulk re-embeds; but side effects (revision ids, projection invalidation, derived-state backfills, post-commit effects) exist, so "an upgrade never changes content" must not be claimed. Rollback = whole-database restore plus matching file state; never delete new columns to fake a downgrade.

**2. Two facts established before any rehearsal.**
- The new version's CLI runs pending migrations automatically after it connects. Therefore any process of the new version that connects to production executes all 21 irreversible migrations. Code replacement can happen only inside a write-freeze window, after the backup, as a migration step. During the rehearsal a background watcher polled production's schema version every minute (it must stay 144).
- A near-miss: the instruction said "create a branch and switch back afterwards" without realising the main tree was the live runtime tree loaded by the daemon. Fix: stop the daemon, require isolated git worktrees only, and keep the watcher.

**3. Plan review.** The verdict was REVISE with 3 BLOCKER findings (unknown production schema version: if not 144 the migration set is larger; rollback impossible after the queue migration so it must be stated as a cost; incomplete writer inventory) and many MAJOR (empty-table rehearsals pass migrations 153/160/161 instantly and give a false sense of safety; confidential-file store not isolated by a state-directory override; retrieval degradation window needs an owner decision; the write wrapper needs request-id persistence and pending replay). All were folded into the plan.

**4. The rehearsal bed.**

| Step | Mechanism |
|---|---|
| Restore | Latest daily backup (5.7 GB) to an isolated directory, own port, `listen_addresses=127.0.0.1`, `archive_mode=off`, recovery target immediate with action promote, same refuse-to-run guards as the weekly drill |
| Detach from production paths | In the copy, rewrite every source `local_path` and the sync repository path that pointed at the production content directory to the drill directory; verify `paths_left_pointing_to_prod = 0 + 0` by count query before anything else |
| Cut off egress | Delete credential-like config rows from the copy (1 key removed) so new-version code cannot send content to a cloud model. A second pattern check on values still reported 1 remaining row; its disposition was not recorded (limit) |
| Isolate the process | `env -i`, a dedicated HOME and state directory (not just the application's home variable: some stores use the real home), a guard that asserts before every step that the configured database URL is the drill instance on the drill port, cycle-phase allowlist set to none, background-daemon installation disabled |
| Cancel active jobs in the copy | The migration refuses to run with active jobs; the copy had 2 |
| Production watch | Production schema version recorded before and after every run: 144 each time |
| Faithful completion records | Copy production's migration completion ledger (17 entries up to an older version) into the drill state directory |
| PATH shim | A tiny executable named like the application CLI, first on PATH, that execs the new version |
| Same-size data | The restored copy: 23,698 pages (23,658 live), 135,607 chunks, 2 active jobs, schema 144; the assessment had assumed ~22k pages and 50k+ chunks (chunks off by 2.7x) |

### Procedure as actually run: three migration runs

| Run | Conditions | Result |
|---|---|---|
| run1 | Drill state directory had no completion records; PATH's CLI was the old version | 21 schema migrations applied (-> 165); then the first orchestration step's smoke test shelled out to the CLI on PATH (the old one) and the new queue protocol rejected it: a message of the form `<job queue> protocol 1 required: upgrade every producer and worker before restart`; rc 1 |
| run2 | PATH shim added, but completion records still empty | Because the ledger was empty, the migrator replayed every historical orchestration (from the oldest through the version before the new one), far longer than run3 and with side effects; one ended partial (7 pages whose files were missing); rc 1. In the previous upgrade round the same replay had installed the background daemon as a launchd service |
| run3 (faithful) | Reset copy, production-identical completion records (17 entries), PATH shim | rc 0; 21 schema migrations plus the new orchestration's mechanical check complete; 16 sources reported as needing an optional host action (left pending); per-page outer-join comparison over 21,766 live pages: 0 changes in body, timeline, frontmatter keys and values, privacy marker, facts, tags, soft-delete state |

### The four cut-over preconditions the rehearsal found

| # | Precondition | Found in |
|---|---|---|
| 1 | The application CLI found on PATH must already be the new version before migrating (the orchestration smoke test calls it by name) | run1 |
| 2 | Run on a host that has the migration completion records (both hosts identical); an empty-record host replays all history | run2 |
| 3 | Active jobs must be 0 (the copy had 2; migration 149 errors otherwise) | run1 setup |
| 4 | Any new-version process that connects auto-migrates, so code replacement happens only inside the freeze window | assessment and run1 |

### The hard blocker found by the write-protocol drill
With the default write-through setting on, Postgres source configured with a directory but no registered file owner, every write fails with `owner_unavailable`, including creating a new page and including forced writes. The upstream's prescribed remedy is to claim a file owner, stop all old writers and activate managed writes: a topology redesign that could not be done that evening. The reversible alternative: `sync.write_through = false` (database-only writes). Measured on the drill copy:

| Operation | write-through on | write-through off |
|---|---|---|
| Create new page | rc 1 `owner_unavailable` | rc 0 |
| Update existing page, no revision | rc 1 `owner_unavailable` | rc 1 `revision_conflict` |
| Update existing page with `--force` | rc 1 `owner_unavailable` (database body unchanged) | rc 0 (database body updated) |
| `capture` existing page, no revision | rc 1 `owner_unavailable` | rc 1 `revision_conflict` |
| `capture` existing page with `--force` | rc 1 `owner_unavailable` | rc 0, receipt contains `revision` |
| `get --json` | not applicable (the page was never created) | revision present |

Cost of the alternative: the file mirror and its copies stop receiving new content (they freeze at cut-over time); page history remains in the database's version table and in the backups. The system owner approved it; the switch can be reverted later through the upstream claim/activate path.

### Retrieval degradation (measured as a finding; timings omitted)
- Migration 153 invalidates every projection: until rebuilt, keyword retrieval returns zero results (`degraded: keyword_zero, projection_pending`). The resident server drains the rebuild automatically.
- The rebuild discards the old vectors of re-chunked pages (about 4 chunks per page), so those chunks need re-embedding with the local model; both rebuilds can run in parallel.
- Method for the reader to measure their own rate: start the server against the copy only, sample the pending-page count every 20 seconds for 5 minutes, extrapolate; separately run the embed job for 180 seconds and count rows whose embedding became non-null.
- Result stated qualitatively: after the upgrade, retrieval is degraded for a bounded window that scales with page and chunk counts; local compute only, no paid or cloud calls. (Our measured rates are host-specific and are omitted.)

### Rollback rehearsal
After migration the copy was discarded and re-restored twice, each time returning to schema 144. The cost of rollback is the loss of writes made after the anchor.

### Test-suite findings during the port of the local patches (on the old host, isolated)
Failing unique test names on the clean upstream tag: 112; on the port: 109; port-only failures: 0; clean-only failures: 3. The first full run had 603 vs 627 failures; the difference was environment noise. Fixing the isolation boundary (HOME, state and audit directories, TMPDIR, PATH shims, `GIT_CEILING_DIRECTORIES` pointing at the temp directory) brought the numbers to 113 vs 110 (headline) and 112 vs 109 (unique names). Two incidents:
- Running the full upstream suite executed 4 tests that really create, start and delete launchd jobs and register a fixed-label job, violating a "touch no service" boundary; a pre-run service snapshot did not exist, so we cannot prove no existing job was affected. Fix: skip those with an environment variable and a test-name exclusion, shim `launchctl`, `crontab`, `systemctl` and `service` to refuse, and run the 4 tests separately in a service-boundary run (20 pass, 4 skip, 0 fail).
- A worktree nested inside another git repository made the clean and dirty trees behave differently (the temp directory discovered the outer `.git`).

### Writer inventory (template)
A table of every process that can write to the database: host, writer, how it calls the application (CLI, HTTP gateway, stdio server), frequency, whether it updates existing pages (needs revision or force), stop command, resume command, and an "excluded, with reason" table for readers and non-writers. The most important finding: the single HTTP gateway through which all interactive clients write is the best master switch; separate stdio servers embedded inside other services are independent write paths and must be listed individually. A pitfall we likely hit: the inventory queried launchd's disabled-services list for the wrong user domain on the second host (its uid differed), which would misclassify running services as disabled; the dated daily backup directory showed the backup job had in fact been running.

### The previous upgrade round (2026-08-29): three rehearsal traps
| Trap | What happened | Lesson |
|---|---|---|
| Tilde in an environment variable | A tilde in `APP_HOME` set via `env`/`nohup` is not expanded; invalid home made a sub-step fall back to the real config file; 22 schema migrations leaked into production. Harmless in the end (additive, idempotent; final tables isomorphic to a clean rehearsal copy at 66 tables; count chain continuous) but a process incident | Assert the resolved paths inside the script before every step |
| Empty completion ledger | Full-history replay installed the background daemon as a launchd service in the rehearsal | Rehearse with the production ledger |
| `pg_basebackup` "complete" with 0 bytes | A timeout killed the process group | Report is not probe: verify bytes and a tar listing; macOS has no `setsid`, so use a managed background execution facility rather than assuming `nohup` survives a process-group timeout |
Also: three rollback anchors for that round: a git tag, a logical dump (725 MB, table of contents verified), and a physical backup (4.7 GB, tar verified). A "plus or minus zero" row-count comparison is only valid in a quiet window (the live database took ~8 page writes per hour; wait for 10 consecutive 30-second ticks with zero writes).

### What did not work
- A rehearsal on a database with the wrong size or without the real completion records (runs 1 and 2).
- The assessment's capacity assumption (22k pages, 50k+ chunks).
- Planning a one-step "put the new binary on PATH" switch: about 20 scripts called a legacy CLI by absolute path, and two hosts had different CLI versions on PATH.

### Pitfalls

| Symptom | Root cause | Fix |
|---|---|---|
| Migrations pass instantly in rehearsal | Empty or small copy | Rehearse on a restore of the real backup |
| Rehearsal run reads the real home directory | Application keeps some state in the OS home, not only in the configured home variable | Dedicated HOME plus `env -i` |
| Migration fails with "upgrade every producer and worker" | Smoke test called the old CLI on PATH | PATH shim |
| Rehearsal wants to message a cloud model | Credentials in the copied config | Delete credential-like rows from the copy; assert egress |
| New-page write fails everywhere | Write-through on, no file owner | Decide the write mode before cut-over (table above) |
| Full upstream test suite touches services | Upstream tests really manage launchd jobs | Skip flag, shims, separate run |

### The rule
Rehearse on a restore of the real backup, with the real migration records, with new-version binaries first on PATH, and with every path, credential and port pointing at the copy; record production's schema version before and after every run.

See [offline reproduction](offline-reproduction.md) for the public adaptations and validation boundary.
