# Pitfalls: symptom, root cause, fix

These failures come from the recorded runs. A passing process is evidence only of that process.

## Chapter 1

| Symptom | Root cause | Fix |
|---|---|---|
| Backup "succeeds" but cannot be restored | `pg_backup_start` and `pg_backup_stop` ran in different sessions, or `backup_label` was not saved | One psql session with the copy in `\!`; save the label output into the backup directory; check for `START WAL LOCATION` |
| Backup tool packs 344 GB | Archive directory is inside PGDATA | Do not keep large custom directories in PGDATA; use the low-level API with excludes |
| First scheduled run fails with `psql: command not found` | launchd gives a minimal PATH | Use absolute binary paths in the script |
| Alert storm every 30 minutes: "newest WAL is 30-33 minutes old, limit 20" | The monitoring check's threshold (20 min) was derived from the 5-minute shipping interval only, but `archive_timeout` is 30 minutes, so the physical floor is about 35 minutes | Threshold 40 minutes; derive any freshness threshold as producer interval plus transport interval. Quick discriminator: if the "newest file" named in successive alerts keeps advancing, nothing is stuck, there is just nothing to ship |
| `rsync` prints `unexpected end of file` yet the run is OK | The rsync runs inside psql's `\!`; its exit status is not checked | Check rsync's status (or compare file counts); do not rely on the log alone; the restore drill is what certifies each backup |

Context and procedure: [backup chain](backup-chain.md).

## Chapter 2

| Symptom | Root cause | Fix |
|---|---|---|
| "postmaster became multithreaded during startup" | macOS locale not set | Export `LC_ALL`/`LANG` |
| Isolation check always fails | `pg_is_in_recovery()` string-concatenated is `true`, bare psql output is `t` | Compare in the same form you produced |
| Drill passes on an empty database | Count check with no lower bound | Require count > 0 and within a window of production |
| Drill directory left behind after a hung shutdown | Cleanup ran regardless | Keep directory, fail the run |

Context and procedure: [restore drill](restore-drill.md).

## Chapter 3

| Symptom | Root cause | Fix |
|---|---|---|
| Recovered page looks right but the confidential marker is gone | Frontmatter merge overwrote the database-only key | Compare frontmatter as dictionaries before and after, with an explicit allowed-differences list |
| "All pages restored" declared from the script's own output | Writer reported success | Re-derive the expected set independently and compare for equality |

Context and procedure: [recovery uses](recovery-uses.md).

## Chapter 4

| Symptom | Root cause | Fix |
|---|---|---|
| Plan built on "keep 5" | A summary from an older rebase was reused | Re-measure the delta (commit count, fork-only symbols in the new tag) before planning |
| Verdict "absorbed" for an upstream guard with the same name | Same name, different semantics (upstream's guard checks revision identity, ours checks that existing content is not reduced) | Test behavior; an ancestor check is the only name-free proof |
| Rebase onto a "main tree" that runs production | Worktree created inside the live runtime tree or inside the wrong repository | `git worktree list` first; never switch branches in the tree production loads code from |

Context and procedure: [fork verdicts](fork-verdicts.md).

## Chapter 5

| Symptom | Root cause | Fix |
|---|---|---|
| Every function reported missing | `\s\b` unsupported in macOS git ERE | `[[:space:]]`, and self-test the checker |
| `pip install .[all]` gives a broken environment | The `all` extra in the new tag is a partial set | Reconstruct the explicit extras list from the freeze of the old environment |
| Worktree built in the wrong repository | Two repositories, only one shares the production worktrees | `git worktree list` before creating anything |
| Working tree always dirty by one file | Two upstream files differing only by case on a case-insensitive filesystem | Never `git add -A`; add named files |
| Command chain reports exit 1 on success | Last command in the chain was `grep -c` which returns 1 on zero matches | Do not end a chain with grep |

Context and procedure: [rebase proof](rebase-proof.md).

## Chapter 6

| Symptom | Root cause | Fix |
|---|---|---|
| Migrations pass instantly in rehearsal | Empty or small copy | Rehearse on a restore of the real backup |
| Rehearsal run reads the real home directory | Application keeps some state in the OS home, not only in the configured home variable | Dedicated HOME plus `env -i` |
| Migration fails with "upgrade every producer and worker" | Smoke test called the old CLI on PATH | PATH shim |
| Rehearsal wants to message a cloud model | Credentials in the copied config | Delete credential-like rows from the copy; assert egress |
| New-page write fails everywhere | Write-through on, no file owner | Decide the write mode before cut-over (table above) |
| Full upstream test suite touches services | Upstream tests really manage launchd jobs | Skip flag, shims, separate run |

Context and procedure: [upgrade rehearsal](upgrade-rehearsal.md).

## Chapter 7

| Symptom | Root cause | Fix |
|---|---|---|
| Wrapper reports failure but the page is stored | Receipt semantics changed | Gate on revision plus independent read-back |
| Soft-deleted pages reappear after a re-import | Import path upserts without checking the soft-delete flag | Skip and audit; recheck inside the page lock; cover every entry point (markdown, image, code, managed) |
| A new release triggers a full re-import by itself | Derived-data version bump (chunker) | Treat any derived-data version bump as "a full reprocess will start": snapshot before, diff after the first cycle |
| Service-run script returns 127 | Minimal PATH under launchd | Absolute paths or explicit PATH in the job definition |

Context and procedure: [cutover](cutover.md).


## Diagnose the result, not the process

A backup job running on schedule, a shipper returning success, and a green health probe described different processes.
None established that a physical base existed or that WAL could be replayed.
The decisive evidence was the isolated restore followed by page count, chunk count, last-sync and max-updated-at reconciliation.
The historical copy-status bug is why the published backup checks rsync separately from psql.

A verifier mismatch has a narrower interpretation than an unsuccessful restore.
For the stale-manifest case, compare the manifest bytes with the inherited source manifest, then restore using the archive.
Move the obsolete manifest aside; do not pretend a low-level backup created a current manifest.
This diagnosis applies to the recorded inherited-manifest case, not arbitrary checksum errors.

## Refusal is part of the drill

Copied configuration can redirect a server even when `pg_ctl -D` names a different directory.
Inspect both PostgreSQL configuration files, refuse settings by name without echoing their values, and pin the directory and archive mode on the command line.
The running server's read-back is a separate check.
Symlinks, tablespace maps and nonempty tablespace directories are refused because recovery can follow them outside the copy.

A failed stop must keep the directory.
Removing files after swallowing a shutdown timeout can delete a live cluster's storage.
The JSON result therefore depends on shutdown and result-write success, not just the earlier count query.

## Rehearsal fidelity is more than row count

A restored database with an empty completion ledger replays old orchestration, including side effects outside SQL.
A correct ledger with the old CLI on PATH can still fail when a smoke test calls that CLI by name.
Use the new-version shim, the real completion records and a dedicated HOME and application state directory together.
Read production's schema version before and after each run because the first new-version connection can migrate automatically.

An environment variable containing an unexpanded tilde is not an isolated home.
The previous upgrade's fallback to the real configuration crossed the intended boundary.
Assert resolved paths inside the script and supply application-specific variables whose consumers you have verified.

## Publication checks do not erase historical uncertainty

The public fixtures test refusal paths, receipt semantics and set comparisons.
They do not establish that a historical rsync error was harmless or that a weekly scheduler is registered.
The service-domain inventory error remains inferred, and the unresolved credential-value match remains unresolved.
Keep those limits beside the procedure rather than converting a later script improvement into historical evidence.
