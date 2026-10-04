# 2. The weekly restore drill, and why the verifier lies

2026-09-27 manual drill and a later manual re-run; the real throwaway-cluster reproduction date was not supplied.

### Problem
On 2026-09-27 a fleet self-audit found: physical backup produced daily; WAL copied 809 times with 0 non-zero exits; but never restored automatically since the July manual proof. A natural next check, `pg_verifybackup`, reported about 110 checksum errors on each of the two most recent backups.

### The false failure: mechanism and reproduction
Mechanism: an earlier physical copy of the database to a new host (made with `pg_basebackup`) left a `backup_manifest` file in the data directory. The low-level backup's rsync copies the entire directory, so every backup carries the old manifest. `pg_verifybackup` compares the backup's files with that stale manifest, so it reports mismatches on a backup that restores perfectly. The source established this from the two backups showing the same ~110 count.

The fact sheet records a first-hand reproduction on a real throwaway cluster. The public adaptation is `scripts/repro-stale-manifest.sh`; its validation here uses command doubles only. The recorded real-cluster sequence was: physical copy with `pg_basebackup` -> use the copy as the live cluster -> low-level backup -> `pg_verifybackup` -> restore drill.

| `pg_verifybackup` output on a good low-level backup | Count |
|---|---|
| `checksum mismatch for file ...` | 21 |
| `... is present on disk but not in the manifest` | 4 |
| `... has size N on disk but size M in the manifest` | 2 |
| `pg_waldump: could not find any WAL file` and `WAL parsing failed for timeline` | 1 + 1 (these appear because the backup excludes `pg_wal`; the WAL lives in the archive) |
| Backup's `backup_manifest` byte-identical to the stale one in the live directory | yes |
| Restore drill on the same backup | `consistent recovery state reached`, then `pausing at the end of recovery`; 260,000 of 270,000 rows visible (rows committed after the backup ended are expected to be absent) |

Fix we applied: stop using manifest verification; use a restore drill; move the stale manifest out of the data directory (moved, not deleted). The drill's copy step also excludes `backup_manifest`. The shipped backup also adds `--exclude=backup_manifest` to its rsync step.

### What we built: the drill script
The design specifies Sunday 05:30. Schedule registration was not confirmed. Steps and guards:

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

The result file contains: timestamp, ok flag, backup path, pages in the drill, pages in production, seconds, detail.

### Review of the script
First review: FAIL, with 3 BLOCKER, 9 MAJOR, 3 MINOR across the whole change set (the same change set also touched an auto-push hook, a self-check script and a polling script). The drill-specific findings:

| Severity | Finding | Fix |
|---|---|---|
| BLOCKER | A copied `postgresql.conf` with an absolute `data_directory` can make `pg_ctl -D` start the production directory; settings in `postgresql.auto.conf` are not guaranteed to win | Fix directory, port, and archive mode on the command line; verify with `SHOW` after start |
| BLOCKER | `rsync -a` preserves symlinks and `tablespace_map`; recovery could write production WAL or tablespaces | Refuse to run on any symlink, tablespace map or non-empty `pg_tblspc` |
| MAJOR | `standby.signal`, `primary_conninfo`, `primary_slot_name` in the backup: the drill could connect to the production primary and advance its replication slot | Delete the signal file and blank the settings; recover only from the local copy plus the read-only archive |
| MAJOR | Second-resolution directory names collide; cleanup removed directories the run had not created | `mktemp -d`; clean only what this run created |
| MAJOR | Stop timeout swallowed, then the data directory was deleted and success written | Failed stop keeps the directory and fails the run |
| MINOR | `lsof` not on the restricted PATH; port check silently skipped | Absolute path; treat "tool missing" as failure |
| MINOR | Hand-built JSON broke on quotes and backslashes | Encode with a JSON library and check the write |

Rounds 2 to 5 added four more MAJOR findings in the same change set (all fixed; two concern other scripts): the refusal diagnostic must not echo a value that may contain a password (setting name only); tree-hash checks with `pipefail`; recursive-delete detection; result-write failure must give a non-zero exit. Round 5 returned PASS. Total: 14 fixes, 1 rejection (the rejected item concerned a patch checker where "warn only, exit 0" was by design).

### Results

| Run | Pages in drill | Pages in production | Note |
|---|---|---|---|
| First run, 2026-09-27 | 18,331 | 18,360 | Accepted by the +/-10% window |
| Later re-run | 18,331 | 18,363 | Self-audit re-run; production had gained pages since the first run |

Per-run duration is omitted (host-specific). A scheduled run result was NOT retrieved and, at the time of writing, we could not confirm that the weekly schedule is registered. Only the two manual runs are evidence.

### What did not work
- `pg_verifybackup` as a recoverability check on a low-level backup (above).
- The first version of the drill script: it was refused by review for seven concrete reasons, three of which could have damaged production state in a conditional scenario (none were present in the live configuration at the time: no tablespaces, symlinks, absolute directories or replication settings, but all were guarded anyway).

### Pitfalls

| Symptom | Root cause | Fix |
|---|---|---|
| "postmaster became multithreaded during startup" | macOS locale not set | Export `LC_ALL`/`LANG` |
| Isolation check always fails | `pg_is_in_recovery()` string-concatenated is `true`, bare psql output is `t` | Compare in the same form you produced |
| Drill passes on an empty database | Count check with no lower bound | Require count > 0 and within a window of production |
| Drill directory left behind after a hung shutdown | Cleanup ran regardless | Keep directory, fail the run |

### The rule
Replace manifest checking with a restore drill that cannot touch production: fix directory, port and archive mode on the command line, refuse to run on anything that could redirect writes, and read the answer back from the running server.

See [offline reproduction](offline-reproduction.md) for the public adaptations and validation boundary.
