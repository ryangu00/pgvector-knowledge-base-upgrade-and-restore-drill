# Results and conditions

Historical evidence transcribed from the supplied fact sheet. No historical log or production snapshot is shipped. Offline fixture checks are separate and do not reproduce these measurements.

Chapters: [1: backup chain](#chapter-1-backup-chain) / [2: restore drill](#chapter-2-restore-drill) / [3: recovery uses](#chapter-3-recovery-uses) / [4: fork verdicts](#chapter-4-fork-verdicts) / [5: rebase proof](#chapter-5-rebase-proof) / [6: upgrade rehearsal](#chapter-6-upgrade-rehearsal) / [7: cutover](#chapter-7-cutover). [Additional results](#additional-results-recorded-in-prose).

## Chapter 1: backup chain

### What we found (composition of the 344 GB)

Conditions: 2026-07-04 incident; backup logs cover 2026-07-04 through 2026-09-22. See [chapter details](backup-chain.md) for the surrounding procedure and evidence qualifiers.

| Component | Size / rate | Evidence quality |
|---|---|---|
| Write storm from the background maintenance daemon: after an upstream merge the daemon restarted with an anchor that had been stale for 13 days, so every ~2.4-minute cycle ran a full-table UPDATE of the pages table (~9,400 rows then) | 6.04 million row updates in total; 337 MB full-text GIN index rewritten each time; 93% of the WAL records in the storm segments were GIN records; 8.2 GB/h for 16 h (restart at 22:16, storm ended at 14:27 next day when the sync bug was fixed); about 130 GB | Incident write-up; the `pg_waldump` output was not kept |
| Idle noise: `archive_timeout = 300` forces a 16 MB segment every 5 minutes even with no writes | 288 segments/day = about 4.6 GB/day, ~5 GB/day x 22 days = ~100-110 GB | Arithmetic |
| Unattributed remainder | Roughly 100 GB after subtracting 130 + ~110 GB from 344 GB | Arithmetic remainder, not a measured allocation to real writes; the split is approximate |

### Backup chain rules

| Step | Rule |
|---|---|
| 0 | Before backing up, measure the archive directory; alert if it exceeds 50 GB (storm symptom). |
| 1 | Take a low-level backup into a dated directory; same-day rerun first removes the old directory (idempotent). |
| 2 | Copy the backup to the second machine; failure means alert and **no cleanup**. |
| 3 | Only after backup and copy both succeeded, `pg_archivecleanup` on both machines up to the backup's start WAL file (the file named in `backup_label`). |
| 4 | Keep the newest 2 backups (`ls -d ... \| sort -r \| tail -n +3 \| xargs rm -rf`, POSIX-safe; the first version used `head -n -2`, which BSD `head` rejects). |
| Fail-safe | Intended policy: any failed step alerts and skips subsequent cleanup. The historical rsync-status gap violated that intention; see the [public copy-status check](offline-reproduction.md#reusable-backup-and-paused-restore). |

### Results

Conditions: 2026-07-04 incident; backup logs cover 2026-07-04 through 2026-09-22. See [chapter details](backup-chain.md) for the surrounding procedure and evidence qualifiers.

| Measure | Value | Condition |
|---|---|---|
| Archive size before / after trimming | 344 GB / 16 MB (primary); 344 GB / 17 MB (mirror) | 2026-07-04, after the first proven restore |
| Daily backup log, 2026-07-04 to 2026-09-22 | 81 calendar days covered, 82 successful runs (79 at the scheduled 04:00 hour, 3 manual or late), 82 successful copies to the second machine, 1 failed run | Original host's own log |
| Failed run | 2026-07-05 04:00: `psql: command not found` (launchd's minimal PATH; the script called a bare `psql`); alert fired, no WAL trimmed; a manual rerun that afternoon succeeded | Fail-safe worked |
| Backup size over time | 2.3 GB (07-04) -> 2.4 GB (late July, August) -> 2.8 GB (08-28) -> 4.9 GB (09-05) -> 5.0 GB (09-21) | Log lines `backup OK ... size=` |
| Archive size after daily retention | 16 MB on 79 of 82 runs; 32 MB, 48 MB, 160 MB on the other three | Log lines `archive now` |
| `rsync: unexpected end of file` lines | Logged 7 times across the 82 runs; every one of those runs still reported `backup OK` | See pitfall below |

### Pitfalls (symptom -> root cause -> fix)

Conditions: 2026-07-04 incident; backup logs cover 2026-07-04 through 2026-09-22. See [chapter details](backup-chain.md) for the surrounding procedure and evidence qualifiers.

| Symptom | Root cause | Fix |
|---|---|---|
| Backup "succeeds" but cannot be restored | `pg_backup_start` and `pg_backup_stop` ran in different sessions, or `backup_label` was not saved | One psql session with the copy in `\!`; save the label output into the backup directory; check for `START WAL LOCATION` |
| Backup tool packs 344 GB | Archive directory is inside PGDATA | Do not keep large custom directories in PGDATA; use the low-level API with excludes |
| First scheduled run fails with `psql: command not found` | launchd gives a minimal PATH | Use absolute binary paths in the script |
| Alert storm every 30 minutes: "newest WAL is 30-33 minutes old, limit 20" | The monitoring check's threshold (20 min) was derived from the 5-minute shipping interval only, but `archive_timeout` is 30 minutes, so the physical floor is about 35 minutes | Threshold 40 minutes; derive any freshness threshold as producer interval plus transport interval. Quick discriminator: if the "newest file" named in successive alerts keeps advancing, nothing is stuck, there is just nothing to ship |
| `rsync` prints `unexpected end of file` yet the run is OK | The rsync runs inside psql's `\!`; its exit status is not checked | Check rsync's status (or compare file counts); do not rely on the log alone; the restore drill is what certifies each backup |

## Chapter 2: restore drill

### The false failure: mechanism and reproduction

Conditions: 2026-09-27 manual drill and a later manual re-run; the real throwaway-cluster reproduction date was not supplied. See [chapter details](restore-drill.md) for the surrounding procedure and evidence qualifiers.

| `pg_verifybackup` output on a good low-level backup | Count |
|---|---|
| `checksum mismatch for file ...` | 21 |
| `... is present on disk but not in the manifest` | 4 |
| `... has size N on disk but size M in the manifest` | 2 |
| `pg_waldump: could not find any WAL file` and `WAL parsing failed for timeline` | 1 + 1 (these appear because the backup excludes `pg_wal`; the WAL lives in the archive) |
| Backup's `backup_manifest` byte-identical to the stale one in the live directory | yes |
| Restore drill on the same backup | `consistent recovery state reached`, then `pausing at the end of recovery`; 260,000 of 270,000 rows visible (rows committed after the backup ended are expected to be absent) |

### What we built: the drill script

Conditions: 2026-09-27 manual drill and a later manual re-run; the real throwaway-cluster reproduction date was not supplied. See [chapter details](restore-drill.md) for the surrounding procedure and evidence qualifiers.

| Step | What it does | Why |
|---|---|---|
| Select | Newest backup directory containing `backup_label` | Fail if none |
| Preconditions | `lsof` available; chosen port free (loopback) | `lsof` at its absolute path: a restricted PATH hid it and silently skipped the port check |
| Workspace | `mktemp -d` under a drill root, `chmod 700`; remember that only this run created it | Second-resolution names collided; cleanup must only remove what this run created |
| Copy | `rsync` the backup, excluding `backup_manifest` | Avoids the false failure |
| Refuse-to-run guards | Refuse if the copy contains any symlink, a `tablespace_map`, a non-empty `pg_tblspc`, or `postgresql.conf` / `postgresql.auto.conf` containing `data_directory`, `hba_file`, `ident_file`, `external_pid_file`, `include`, `include_dir`, `include_if_exists`, `primary_conninfo`, `primary_slot_name`. The diagnostic prints the setting name only, never the value | Any of these can make the restore write to production storage or connect to the production primary; `primary_conninfo` may hold a password |
| Recovery settings | Append `restore_command = 'cp <archive>/%f %p'` (production archive, read-only use), `recovery_target = 'immediate'`, `recovery_target_action = 'pause'`, `hot_standby = on`, empty `primary_conninfo` / `primary_slot_name`; add `recovery.signal`; delete `postmaster.pid` and `standby.signal` | Pause at first consistency keeps the copy read-only |
| Start | `pg_ctl ... -o "-c data_directory=<copy> -p <port> -c listen_addresses=127.0.0.1 -c archive_mode=off"` | Command-line settings beat the config files, so the copied configuration cannot redirect anything |
| Isolation check | After start, `SELECT current_setting('data_directory') \|\| '\|' \|\| current_setting('archive_mode') \|\| '\|' \|\| pg_is_in_recovery()` must equal `<copy>\|off\|true` | Trust the running server, not the command line. Note `pg_is_in_recovery()` concatenated into a string is `true`, while psql prints `t` for the bare boolean |
| Content check | Wait (up to 180 x 5 s) until `select count(*) from pages` answers; compare with production count, accept within +/-10% | Backup is from 04:00; production can later gain pages or lose them (a de-duplication hard-deleted some). The check catches an empty or wrong database; it is not a content comparison |
| Finish | Stop with `pg_ctl -m fast`; if stop fails keep the directory and mark the run failed; delete the directory only if it was created by this run; write a JSON result with a JSON encoder (not string concatenation) | Failure to stop must never lead to deleting a live data directory |
| Environment | `LC_ALL` and `LANG` set | macOS postmaster otherwise aborts with "postmaster became multithreaded during startup" |

### Review of the script

Conditions: 2026-09-27 manual drill and a later manual re-run; the real throwaway-cluster reproduction date was not supplied. See [chapter details](restore-drill.md) for the surrounding procedure and evidence qualifiers.

| Severity | Finding | Fix |
|---|---|---|
| BLOCKER | A copied `postgresql.conf` with an absolute `data_directory` can make `pg_ctl -D` start the production directory; settings in `postgresql.auto.conf` are not guaranteed to win | Fix directory, port, and archive mode on the command line; verify with `SHOW` after start |
| BLOCKER | `rsync -a` preserves symlinks and `tablespace_map`; recovery could write production WAL or tablespaces | Refuse to run on any symlink, tablespace map or non-empty `pg_tblspc` |
| MAJOR | `standby.signal`, `primary_conninfo`, `primary_slot_name` in the backup: the drill could connect to the production primary and advance its replication slot | Delete the signal file and blank the settings; recover only from the local copy plus the read-only archive |
| MAJOR | Second-resolution directory names collide; cleanup removed directories the run had not created | `mktemp -d`; clean only what this run created |
| MAJOR | Stop timeout swallowed, then the data directory was deleted and success written | Failed stop keeps the directory and fails the run |
| MINOR | `lsof` not on the restricted PATH; port check silently skipped | Absolute path; treat "tool missing" as failure |
| MINOR | Hand-built JSON broke on quotes and backslashes | Encode with a JSON library and check the write |

### Results

Conditions: 2026-09-27 manual drill and a later manual re-run; the real throwaway-cluster reproduction date was not supplied. See [chapter details](restore-drill.md) for the surrounding procedure and evidence qualifiers.

| Run | Pages in drill | Pages in production | Note |
|---|---|---|---|
| First run, 2026-09-27 | 18,331 | 18,360 | Accepted by the +/-10% window |
| Later re-run | 18,331 | 18,363 | Self-audit re-run; production had gained pages since the first run |

### Pitfalls

Conditions: 2026-09-27 manual drill and a later manual re-run; the real throwaway-cluster reproduction date was not supplied. See [chapter details](restore-drill.md) for the surrounding procedure and evidence qualifiers.

| Symptom | Root cause | Fix |
|---|---|---|
| "postmaster became multithreaded during startup" | macOS locale not set | Export `LC_ALL`/`LANG` |
| Isolation check always fails | `pg_is_in_recovery()` string-concatenated is `true`, bare psql output is `t` | Compare in the same form you produced |
| Drill passes on an empty database | Count check with no lower bound | Require count > 0 and within a window of production |
| Drill directory left behind after a hung shutdown | Cleanup ran regardless | Keep directory, fail the run |

## Chapter 3: recovery uses

### Pitfalls

Conditions: Dated recovery uses from 2026-07-04 through 2026-10-02; these are distinct baselines. See [chapter details](recovery-uses.md) for the surrounding procedure and evidence qualifiers.

| Symptom | Root cause | Fix |
|---|---|---|
| Recovered page looks right but the confidential marker is gone | Frontmatter merge overwrote the database-only key | Compare frontmatter as dictionaries before and after, with an explicit allowed-differences list |
| "All pages restored" declared from the script's own output | Writer reported success | Re-derive the expected set independently and compare for equality |

## Chapter 4: fork verdicts

### Recorded results

Conditions: Rounds A through E retain their own dates, denominators and unfinished status. See [chapter details](fork-verdicts.md) for the surrounding procedure and evidence qualifiers.

| Round | Date | Fork | Local commits | Result of the triage |
|---|---|---|---|---|
| A | 2026-08-29 | Knowledge base, previous upgrade (695+ upstream commits, 4 minors, 22 migrations) | 17 | KEEP 3, DROP 9 (7 already implemented upstream and re-verified, 1 dormant switch, 1 replaced by an upstream mechanism), REWRITE 5 |
| B | 2026-09-28 | Knowledge base, this book's upgrade (12 minors, 21 migrations) | 14 | 1 skip (ancestor of target), 2 obsolete (a stale test, a one-character brace fix), 2 ledger/doc items to rewrite, 9 to keep or port (the last two being the file-mirror guard commits, which had to be re-implemented semantically on new paths); ported as three reviewable groups |
| C | 2026-07-27 | Agent runtime, against a July release | 43 commits + 1 uncommitted file | First pass: retire 3, keep 5, reimplement 30, uncertain 6. Second pass merged the 30 "reimplement" into 4 work items. Final: 3 retired whole plus ~6 split-out parts, 4 committed keeps plus 1 uncommitted, 4 decided at execution time |
| D | 2026-09-04 | Agent runtime, rebased onto a release tag dated 2026-08-31 | 39 (37 non-merge + 2 merges); 59 files, +4,713 / -172 | Upstream changed 8,638 files in the window, 40 overlapping ours. The "keep 5" figure from the July summary was stale: every fork-only symbol (anti-loop state, persona domain, silence filter, outbound gate hooks, audit hooks) had zero hits in the new tag, so none could be retired |
| E | 2026-09-18 | Agent runtime, next tag 10,722 commits later | 41 rows (40 rehearsed + 1 backport already in target) | Rehearsal cherry-pick: 5 clean, 35 conflicts, 1 n/a. Verdicts: 31 keep, 3 partial, 4 upstream-has-it, 3 re-evaluate. The pre-planning guess "most persona-routing and task-board patches can be dropped" was refuted: upstream had zero equivalents. Round E stopped after triage (not finished at time of writing) |

### Round B detail

| Verdict | Count | Examples (generic) |
|---|---|---|
| Skip, already an ancestor of the target | 1 | A takes-proposal exclusion |
| Keep, port with conflict resolution | 7 | A local confidential-attachment store (four commits: the store itself, audio extensions, WebP magic bytes, and source-id validation plus rollback of only the rows it created), an extract `--exclude` flag, a lint-exclude environment variable, a phantom-limit-zero fix |
| Keep, semantic port required | 2 | File-mirror guard with soft-delete audit and rename protection plus DB-only facts and a phase allowlist; the follow-up "re-read inside the lock, distinguish real empty result from fallback" |
| Obsolete / cleanup only | 2 | Delete a test whose subject is gone; a one-character missing brace from an earlier merge |
| Ledger or documentation | 2 | Ledger v2; review verdicts |

### Pitfalls

Conditions: Rounds A through E retain their own dates, denominators and unfinished status. See [chapter details](fork-verdicts.md) for the surrounding procedure and evidence qualifiers.

| Symptom | Root cause | Fix |
|---|---|---|
| Plan built on "keep 5" | A summary from an older rebase was reused | Re-measure the delta (commit count, fork-only symbols in the new tag) before planning |
| Verdict "absorbed" for an upstream guard with the same name | Same name, different semantics (upstream's guard checks revision identity, ours checks that existing content is not reduced) | Test behavior; an ancestor check is the only name-free proof |
| Rebase onto a "main tree" that runs production | Worktree created inside the live runtime tree or inside the wrong repository | `git worktree list` first; never switch branches in the tree production loads code from |

## Chapter 5: rebase proof

### Procedure as actually run (round D)

Conditions: Round D, 2026-09-04; serial integration/pristine comparison in the same environment. See [chapter details](rebase-proof.md) for the surrounding procedure and evidence qualifiers.

| Classification | Count |
|---|---|
| Upstream changed the same file in the window (overlap) | 67 |
| File new in our commits | 34 |
| Untouched upstream (clean) | 1 |
| Functions present in the target | 94 |
| Functions missing | 14: 10 are test functions, 3 are our own new symbols, 1 is a production function that really was removed upstream |
| Largest overlap per commit | four commits with 7, 6, 5 and 4 files that upstream also changed in the window |

### Targeted test run (our 27 touched or added test files)

Conditions: Round D, 2026-09-04; serial integration/pristine comparison in the same environment. See [chapter details](rebase-proof.md) for the surrounding procedure and evidence qualifiers.

| Root cause | Fix | Lesson |
|---|---|---|
| A telemetry hook judged "not needed" while resolving three conflicts (the upstream helper replaced the surrounding block) was locked in by two of our tests | Add the hook back into the upstream helper | Tests are the specification; check whether a test pins a behavior before judging it unnecessary |
| Upstream's new batch quality gate requires a goal of at least 10 characters; our test used a 9-character placeholder | Fix the test | Upstream tightened a precondition |
| Upstream passes a new keyword argument to a function our test stubbed with a fixed signature; the TypeError inside a thread was swallowed, yielding an empty result | Give the stub `**kwargs` | A swallowed exception looks like "no output" |

### Dual full test run results

Conditions: Round D, 2026-09-04; serial integration/pristine comparison in the same environment. See [chapter details](rebase-proof.md) for the surrounding procedure and evidence qualifiers.

| | Integration tree | Pristine tag |
|---|---|---|
| Test files | 3,510 | 3,493 |
| Passed | 42,281 | 42,144 |
| Failed | 52 (in 26 files) | 51 (in 25 files) |
| Skipped | 370 | 370 |
| Flaky (re-run once per file) | 0 | 0 |

### Adjudication of the integration-only files

Conditions: Round D, 2026-09-04; serial integration/pristine comparison in the same environment. See [chapter details](rebase-proof.md) for the surrounding procedure and evidence qualifiers.

| File | Verdict | Reason |
|---|---|---|
| A setup-login test | Accepted deviation | Our patch deliberately seals a direct login path for one provider; our own test covers the new behavior; the clean tree would of course pass the old test |
| A new upstream lint test forbidding raw config reads outside the owner module (it named our assignee-exit read) | Fixed | Added a small function in the owner module for cross-profile raw reads and called it. We refused the alternative of moving the file-name literal further away from the lint window, which is how another existing read passes by accident |

### Pitfalls

Conditions: Round D, 2026-09-04; serial integration/pristine comparison in the same environment. See [chapter details](rebase-proof.md) for the surrounding procedure and evidence qualifiers.

| Symptom | Root cause | Fix |
|---|---|---|
| Every function reported missing | `\s\b` unsupported in macOS git ERE | `[[:space:]]`, and self-test the checker |
| `pip install .[all]` gives a broken environment | The `all` extra in the new tag is a partial set | Reconstruct the explicit extras list from the freeze of the old environment |
| Worktree built in the wrong repository | Two repositories, only one shares the production worktrees | `git worktree list` before creating anything |
| Working tree always dirty by one file | Two upstream files differing only by case on a case-insensitive filesystem | Never `git add -A`; add named files |
| Command chain reports exit 1 on success | Last command in the chain was `grep -c` which returns 1 on zero matches | Do not end a chain with grep |

## Chapter 6: upgrade rehearsal

### Migration assessment

Conditions: 2026-09-29 rehearsal unless the previous 2026-08-29 round is named; elapsed migration rates omitted. See [chapter details](upgrade-rehearsal.md) for the surrounding procedure and evidence qualifiers.

| Class | Migrations | What they do (generic) |
|---|---|---|
| High risk | 3 | (1) job-queue table: exclusive lock, errors if any job is active, installs new protocol triggers; (2) pages get a non-null random revision identifier per row plus snapshot columns: full-table write; (3) one UPDATE over all pages clearing the text-projection revision and embedding signature, scheduling rebuild jobs and replacing the search-vector trigger: keyword retrieval empty until the rebuild |
| Medium | 9 | Widening a CHECK constraint (re-validate scan); rewriting client grants (revokes illegal agent scopes: a real permission change); building a withdrawal table from facts; a statistics object plus ANALYZE and a plain (non-concurrent) CREATE INDEX under statement_timeout 30 s and lock_timeout 2 s; scanning ~22k frontmatter blobs to backfill derived state; writer-guard triggers; effect-table rewrite; a concurrent index |
| Low to medium | 3 | Mostly new tables and indexes |
| Low | 6 | New empty tables or metadata |

### Rehearsal bed

Conditions: 2026-09-29 rehearsal unless the previous 2026-08-29 round is named; elapsed migration rates omitted. See [chapter details](upgrade-rehearsal.md) for the surrounding procedure and evidence qualifiers.

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

Conditions: 2026-09-29 rehearsal unless the previous 2026-08-29 round is named; elapsed migration rates omitted. See [chapter details](upgrade-rehearsal.md) for the surrounding procedure and evidence qualifiers.

| Run | Conditions | Result |
|---|---|---|
| run1 | Drill state directory had no completion records; PATH's CLI was the old version | 21 schema migrations applied (-> 165); then the first orchestration step's smoke test shelled out to the CLI on PATH (the old one) and the new queue protocol rejected it: a message of the form `<job queue> protocol 1 required: upgrade every producer and worker before restart`; rc 1 |
| run2 | PATH shim added, but completion records still empty | Because the ledger was empty, the migrator replayed every historical orchestration (from the oldest through the version before the new one), far longer than run3 and with side effects; one ended partial (7 pages whose files were missing); rc 1. In the previous upgrade round the same replay had installed the background daemon as a launchd service |
| run3 (faithful) | Reset copy, production-identical completion records (17 entries), PATH shim | rc 0; 21 schema migrations plus the new orchestration's mechanical check complete; 16 sources reported as needing an optional host action (left pending); per-page outer-join comparison over 21,766 live pages: 0 changes in body, timeline, frontmatter keys and values, privacy marker, facts, tags, soft-delete state |

### The four cut-over preconditions the rehearsal found

Conditions: 2026-09-29 rehearsal unless the previous 2026-08-29 round is named; elapsed migration rates omitted. See [chapter details](upgrade-rehearsal.md) for the surrounding procedure and evidence qualifiers.

| # | Precondition | Found in |
|---|---|---|
| 1 | The application CLI found on PATH must already be the new version before migrating (the orchestration smoke test calls it by name) | run1 |
| 2 | Run on a host that has the migration completion records (both hosts identical); an empty-record host replays all history | run2 |
| 3 | Active jobs must be 0 (the copy had 2; migration 149 errors otherwise) | run1 setup |
| 4 | Any new-version process that connects auto-migrates, so code replacement happens only inside the freeze window | assessment and run1 |

### The hard blocker found by the write-protocol drill

Conditions: 2026-09-29 rehearsal unless the previous 2026-08-29 round is named; elapsed migration rates omitted. See [chapter details](upgrade-rehearsal.md) for the surrounding procedure and evidence qualifiers.

| Operation | write-through on | write-through off |
|---|---|---|
| Create new page | rc 1 `owner_unavailable` | rc 0 |
| Update existing page, no revision | rc 1 `owner_unavailable` | rc 1 `revision_conflict` |
| Update existing page with `--force` | rc 1 `owner_unavailable` (database body unchanged) | rc 0 (database body updated) |
| `capture` existing page, no revision | rc 1 `owner_unavailable` | rc 1 `revision_conflict` |
| `capture` existing page with `--force` | rc 1 `owner_unavailable` | rc 0, receipt contains `revision` |
| `get --json` | not applicable (the page was never created) | revision present |

### The previous upgrade round (2026-08-29): three rehearsal traps

Conditions: 2026-09-29 rehearsal unless the previous 2026-08-29 round is named; elapsed migration rates omitted. See [chapter details](upgrade-rehearsal.md) for the surrounding procedure and evidence qualifiers.

| Trap | What happened | Lesson |
|---|---|---|
| Tilde in an environment variable | A tilde in `APP_HOME` set via `env`/`nohup` is not expanded; invalid home made a sub-step fall back to the real config file; 22 schema migrations leaked into production. Harmless in the end (additive, idempotent; final tables isomorphic to a clean rehearsal copy at 66 tables; count chain continuous) but a process incident | Assert the resolved paths inside the script before every step |
| Empty completion ledger | Full-history replay installed the background daemon as a launchd service in the rehearsal | Rehearse with the production ledger |
| `pg_basebackup` "complete" with 0 bytes | A timeout killed the process group | Report is not probe: verify bytes and a tar listing; macOS has no `setsid`, so use a managed background execution facility rather than assuming `nohup` survives a process-group timeout |

### Pitfalls

Conditions: 2026-09-29 rehearsal unless the previous 2026-08-29 round is named; elapsed migration rates omitted. See [chapter details](upgrade-rehearsal.md) for the surrounding procedure and evidence qualifiers.

| Symptom | Root cause | Fix |
|---|---|---|
| Migrations pass instantly in rehearsal | Empty or small copy | Rehearse on a restore of the real backup |
| Rehearsal run reads the real home directory | Application keeps some state in the OS home, not only in the configured home variable | Dedicated HOME plus `env -i` |
| Migration fails with "upgrade every producer and worker" | Smoke test called the old CLI on PATH | PATH shim |
| Rehearsal wants to message a cloud model | Credentials in the copied config | Delete credential-like rows from the copy; assert egress |
| New-page write fails everywhere | Write-through on, no file owner | Decide the write mode before cut-over (table above) |
| Full upstream test suite touches services | Upstream tests really manage launchd jobs | Skip flag, shims, separate run |

## Chapter 7: cutover

### What we built or changed

Conditions: 2026-09-29 cut-over and checks through 2026-10-02; baselines changed between stages. See [chapter details](cutover.md) for the surrounding procedure and evidence qualifiers.

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

### Results

Conditions: 2026-09-29 cut-over and checks through 2026-10-02; baselines changed between stages. See [chapter details](cutover.md) for the surrounding procedure and evidence qualifiers.

| Check | Result |
|---|---|
| Per-page comparison right after migration (21,810 live pages) | 0 changes |
| Production baseline after cut-over | 21,811 live pages = 21,810 + 1 new daily page; confidential 11,607; facts 93 |
| Gateway error log after cut-over | 0 application errors |
| Text retrieval | Fully restored; vector gaps closing, remaining under 0.2% a few days later |
| Compatibility patches | 13/13 offline checks |

### What the rehearsal did not cover

Conditions: 2026-09-29 cut-over and checks through 2026-10-02; baselines changed between stages. See [chapter details](cutover.md) for the surrounding procedure and evidence qualifiers.

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

### Pitfalls

Conditions: 2026-09-29 cut-over and checks through 2026-10-02; baselines changed between stages. See [chapter details](cutover.md) for the surrounding procedure and evidence qualifiers.

| Symptom | Root cause | Fix |
|---|---|---|
| Wrapper reports failure but the page is stored | Receipt semantics changed | Gate on revision plus independent read-back |
| Soft-deleted pages reappear after a re-import | Import path upserts without checking the soft-delete flag | Skip and audit; recheck inside the page lock; cover every entry point (markdown, image, code, managed) |
| A new release triggers a full re-import by itself | Derived-data version bump (chunker) | Treat any derived-data version bump as "a full reprocess will start": snapshot before, diff after the first cycle |
| Service-run script returns 127 | Minimal PATH under launchd | Absolute paths or explicit PATH in the job definition |


## Additional results recorded in prose

These tables collect result counts that the fact sheet recorded outside tables.
They are not new measurements or results from the public fixture suite.

### Recovery and file-mirror reconciliation

Conditions: 2026-09-27 incident and same-day read-only reconciliation; backup extraction used the 04:00 copy and version history.

| Measure | Recorded result |
|---|---|
| Overwritten bodies | 6 pages; largest 174,798 -> 433 characters |
| Lost confidential markers | 31 pages; zero pages deleted |
| Recovery set | 6 body + 31 marker - 2 overlapping = 35 unique; set equality true |
| Recovery content checks | No lost pre-sync body lines or fact blocks |
| Full mirror comparison | 21,765 pages |
| Identical | 9,835 |
| In database, absent from files | 11,644 |
| Database-only frontmatter keys | 283 pages; 230 with the privacy marker |
| Body text missing from file | 3 pages |

### Upgrade anchors and later comparison

Conditions: anchors on 2026-09-29; comparison against the post-cut-over baseline on 2026-10-02 after crash recovery.

| Measure | Recorded result |
|---|---|
| Rehearsal restore | 5.7 GB daily backup |
| Freeze anchor | 6.3 GB physical backup |
| Reset rehearsal | Re-restored twice, schema 144 each time |
| Later per-page comparison | +2,081 pages, 1 changed daily page, no resurrection, no loss |
| Remaining vector gaps | 161 on 2026-10-02 |

### Isolated patch-suite comparison

Conditions: knowledge-base port on the old host; isolation was corrected between the first and final runs. Headline failure totals and unique test-name totals are different measures.

| Measure | Clean upstream | Port |
|---|---|---|
| First run, environment noise | 603 vs 627 failures across the pair; ordering not specified in the sheet | Do not infer an ordering |
| Corrected headline totals | 113 | 110 |
| Corrected unique failing names | 112 | 109 |
| Exclusive failures | 3 clean-only | 0 port-only |

Conditions: separate service-boundary run after discovering that 4 upstream tests really managed services; no pre-run service snapshot existed for the earlier run.

| Passed | Skipped | Failed |
|---|---|---|
| 20 | 4 | 0 |

### Compatibility deployment checks

Conditions: historical cut-over preparation, 2026-09-29; offline checks with no real application, database or service calls. These are not counts from the newly shipped test suite.

| Measure | Recorded result |
|---|---|
| Checks | 13 of 13 passed |
| Patch files / full copies | 14 / 14 |
| Source hashes unchanged | 7 |
| Literal-secret hits | 0 |
| Real application, database or service calls | 0 |
