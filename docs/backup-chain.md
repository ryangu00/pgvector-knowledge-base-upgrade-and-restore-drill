# 1. A backup nobody had restored

2026-07-04 incident; backup logs cover 2026-07-04 through 2026-09-22.

### Problem
On 2026-07-04 a disk audit found a WAL archive of 344 GB on the primary and the same 344 GB mirrored on the second machine. The database itself was 2.3 GB, a 150x write amplification. Worse, none of that WAL was usable: WAL replay has to start from a physical base backup, and the only backups were logical dumps. The 22 days of archive provided no point-in-time recovery at all.

### What we found (composition of the 344 GB)

| Component | Size / rate | Evidence quality |
|---|---|---|
| Write storm from the background maintenance daemon: after an upstream merge the daemon restarted with an anchor that had been stale for 13 days, so every ~2.4-minute cycle ran a full-table UPDATE of the pages table (~9,400 rows then) | 6.04 million row updates in total; 337 MB full-text GIN index rewritten each time; 93% of the WAL records in the storm segments were GIN records; 8.2 GB/h for 16 h (restart at 22:16, storm ended at 14:27 next day when the sync bug was fixed); about 130 GB | Incident write-up; the `pg_waldump` output was not kept |
| Idle noise: `archive_timeout = 300` forces a 16 MB segment every 5 minutes even with no writes | 288 segments/day = about 4.6 GB/day, ~5 GB/day x 22 days = ~100-110 GB | Arithmetic |
| Unattributed remainder | Roughly 100 GB after subtracting 130 + ~110 GB from 344 GB | Arithmetic remainder, not a measured allocation to real writes; the split is approximate |

Root cause in one line: a background job with a stale anchor rewrote an indexed table every cycle, and nothing watched archive growth.

### What we built or changed

**1. Low-level online backup (not `pg_basebackup`).** The WAL archive directory lives inside the data directory. `pg_basebackup` would have packed all 344 GB of it. A low-level backup lets you exclude the archive and `pg_wal`, needs no configuration change, and does not lock the database:

```bash
# one psql session; if the session disconnects the backup is aborted
psql <<EOF > "$STOPOUT"
SELECT pg_backup_start('daily-<date>', true);
\! rsync -a --exclude='archive/' --exclude='pg_wal/*' --exclude='postmaster.pid' --exclude='postmaster.opts' "$PGDATA"/ "$BK/data"/
SELECT labelfile FROM pg_backup_stop(true);
EOF
# everything after the first line of the output is the backup_label; it MUST be saved into the backup
tail -n +2 "$STOPOUT" | sed '/^$/d' > "$BK/data/backup_label"
WALFILE=$(grep 'START WAL LOCATION' "$STOPOUT" | grep -oE 'file [0-9A-F]+' | awk '{print $2}')
```

The historical script treated the backup as successful if psql exited 0 and `START WAL LOCATION` was present. That was insufficient: psql did not propagate the shell copy failure. The shipped script adds an independent rsync status marker, checks every command and file operation, and excludes `backup_manifest`. These checks were added while writing the book; this is not the version that produced the historical logs. The snippet above describes that historical mechanism, not a safe standalone implementation.

**2. Backup chain rules (daily at 04:00, plain shell, no model calls):**

| Step | Rule |
|---|---|
| 0 | Before backing up, measure the archive directory; alert if it exceeds 50 GB (storm symptom). |
| 1 | Backup as above into a dated directory; same-day rerun first removes the old directory (idempotent). |
| 2 | Copy the backup to the second machine; failure means alert and **no cleanup**. |
| 3 | Only after backup and copy both succeeded, `pg_archivecleanup` on both machines up to the backup's start WAL file (the file named in `backup_label`). |
| 4 | Keep the newest 2 backups (`ls -d ... \| sort -r \| tail -n +3 \| xargs rm -rf`, POSIX-safe; the first version used `head -n -2`, which BSD `head` rejects). |
| Fail-safe | Intended policy: any failed step alerts and skips subsequent cleanup. The historical rsync-status gap violated that intention; see the shipped correction above. |

**3. Configuration tuning, applied the same evening** (`ALTER SYSTEM` plus reload, no restart): `wal_compression = zstd`, `archive_timeout` 300 -> 1800, `checkpoint_timeout` 300 -> 1800. The decision record had estimated ~80% less idle noise; that estimate was never measured.

### Procedure as actually run (2026-07-04)
1. Run the low-level backup: 2.3 GB.
2. Start a side instance (port 5433) on a copy of the backup with `restore_command = 'cp <archive>/%f %p'`, `archive_mode = off`, and a `recovery.signal` file.
3. Wait for the log lines `consistent recovery state reached` and `archive recovery complete`.
4. Reconcile against the primary on four values: pages 8,693; content chunks 33,275; last-sync timestamp; max updated-at. All four identical (4/4).
5. Only then trim the archive to the backup's start segment: 344 GB -> 16 MB on the primary and 344 GB -> 17 MB on the mirror, 688 GB reclaimed. The running primary database was not modified by the restore; its archive was trimmed only after reconciliation.

### Results

| Measure | Value | Condition |
|---|---|---|
| Archive size before / after trimming | 344 GB / 16 MB (primary); 344 GB / 17 MB (mirror) | 2026-07-04, after the first proven restore |
| Daily backup log, 2026-07-04 to 2026-09-22 | 81 calendar days covered, 82 successful runs (79 at the scheduled 04:00 hour, 3 manual or late), 82 successful copies to the second machine, 1 failed run | Original host's own log |
| Failed run | 2026-07-05 04:00: `psql: command not found` (launchd's minimal PATH; the script called a bare `psql`); alert fired, no WAL trimmed; a manual rerun that afternoon succeeded | Fail-safe worked |
| Backup size over time | 2.3 GB (07-04) -> 2.4 GB (late July, August) -> 2.8 GB (08-28) -> 4.9 GB (09-05) -> 5.0 GB (09-21) | Log lines `backup OK ... size=` |
| Archive size after daily retention | 16 MB on 79 of 82 runs; 32 MB, 48 MB, 160 MB on the other three | Log lines `archive now` |
| `rsync: unexpected end of file` lines | Logged 7 times across the 82 runs; every one of those runs still reported `backup OK` | See pitfall below |

### What did not work
- `pg_basebackup` against this layout: it would have archived 344 GB with the data directory.
- Trusting process state: the sync job reported "completed" while the repository HEAD was frozen for 13 days, the WAL shipper faithfully shipped files that could never be replayed, and a health probe reported all-pass while the version had drifted. Three green signals, none about the result.

### Pitfalls (symptom -> root cause -> fix)

| Symptom | Root cause | Fix |
|---|---|---|
| Backup "succeeds" but cannot be restored | `pg_backup_start` and `pg_backup_stop` ran in different sessions, or `backup_label` was not saved | One psql session with the copy in `\!`; save the label output into the backup directory; check for `START WAL LOCATION` |
| Backup tool packs 344 GB | Archive directory is inside PGDATA | Do not keep large custom directories in PGDATA; use the low-level API with excludes |
| First scheduled run fails with `psql: command not found` | launchd gives a minimal PATH | Use absolute binary paths in the script |
| Alert storm every 30 minutes: "newest WAL is 30-33 minutes old, limit 20" | The monitoring check's threshold (20 min) was derived from the 5-minute shipping interval only, but `archive_timeout` is 30 minutes, so the physical floor is about 35 minutes | Threshold 40 minutes; derive any freshness threshold as producer interval plus transport interval. Quick discriminator: if the "newest file" named in successive alerts keeps advancing, nothing is stuck, there is just nothing to ship |
| `rsync` prints `unexpected end of file` yet the run is OK | The rsync runs inside psql's `\!`; its exit status is not checked | Check rsync's status (or compare file counts); do not rely on the log alone; the restore drill is what certifies each backup |

### The rule
Never trim or trust a backup chain until one restore from it has been reconciled against the source; and judge health by a result (can it be restored, how old is the newest usable file), not by whether a job ran.

See [offline reproduction](offline-reproduction.md) for the public adaptations and validation boundary.
