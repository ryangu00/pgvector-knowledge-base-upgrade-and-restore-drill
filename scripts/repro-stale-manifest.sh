#!/usr/bin/env bash
# Throwaway-cluster reproduction adapted for this cookbook.
# Reproduces, on a throwaway local cluster, the "stale backup_manifest" false failure:
#   1. a physical copy made with pg_basebackup leaves a backup_manifest inside the data directory;
#   2. that copy becomes the live data directory;
#   3. a low-level online backup (pg_backup_start -> rsync -> pg_backup_stop in ONE psql session)
#      copies the old manifest into every backup;
#   4. pg_verifybackup then reports many errors on a backup that restores perfectly;
#   5. a restore drill (isolated dir, own port, restore_command from the archive) reaches
#      "consistent recovery state reached" and pauses.
# Needs: PostgreSQL 15+ binaries, rsync. Touches only $WORK. Ports $P1/$P2 on 127.0.0.1.
set -euo pipefail
PGBIN=${PGBIN:?set the PostgreSQL binary directory}
WORK=${WORK:-$PWD/manifest-repro}; P1=${P1:-55991}; P2=${P2:-55992}
export PATH="$PGBIN:$PATH" LC_ALL=${LC_ALL:-en_US.UTF-8} LANG=${LANG:-en_US.UTF-8}
[ ! -e "$WORK" ] || { echo "WORK already exists (refuse)"; exit 1; }
mkdir -p "$WORK/arch"
WORK=$(cd "$WORK" && pwd)
case "$WORK" in *[!a-zA-Z0-9_./-]*) echo "use a simple WORK path"; exit 1;; esac
export TMPDIR="$WORK"
LIVE=""; DRILL=""
cleanup() {
  local rc=$?
  if [ -n "$DRILL" ]; then pg_ctl -D "$DRILL" -m fast -w stop >/dev/null 2>&1 || rc=1; fi
  if [ -n "$LIVE" ]; then pg_ctl -D "$LIVE" -m fast -w stop >/dev/null 2>&1 || rc=1; fi
  return "$rc"
}
trap cleanup EXIT
Q() { psql -X -At -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "$1" -d postgres -c "$2"; }

initdb -D "$WORK/src" -A trust >/dev/null 2>&1 || { echo "initdb failed"; exit 1; }
cat >> "$WORK/src/postgresql.conf" <<C
port=$P1
listen_addresses='127.0.0.1'
unix_socket_directories=''
archive_mode=on
archive_command='cp %p $WORK/arch/%f'
wal_level=replica
max_wal_senders=3
C
LIVE="$WORK/src"
pg_ctl -D "$WORK/src" -l "$WORK/src.log" -w start >/dev/null 2>&1 || { echo "start failed"; exit 1; }
Q $P1 "create table t as select g, md5(g::text) from generate_series(1,200000) g" >/dev/null

# 1+2: physical copy carrying a backup_manifest, then use the copy as the live cluster
pg_basebackup -h 127.0.0.1 -p $P1 -D "$WORK/live" -Fp -X stream >/dev/null 2>&1 || { echo "pg_basebackup failed"; exit 1; }
test -f "$WORK/live/backup_manifest" && echo "live data dir contains backup_manifest (from the physical copy)"
pg_ctl -D "$WORK/src" -m fast -w stop >/dev/null 2>&1
LIVE="$WORK/live"
pg_ctl -D "$WORK/live" -l "$WORK/live.log" -w start >/dev/null 2>&1 || { echo "start live failed"; exit 1; }
Q $P1 "insert into t select g, md5(g::text) from generate_series(200001,260000) g" >/dev/null
Q $P1 "select pg_switch_wal()" >/dev/null

# 3: low-level online backup, one psql session; WAL comes from the archive, not from the copy
mkdir -p "$WORK/bk"
psql -X -At -v ON_ERROR_STOP=1 -h 127.0.0.1 -p $P1 -d postgres > "$WORK/stop.out" <<SQL
SELECT pg_backup_start('repro', true);
\! rsync -a --exclude='postmaster.pid' --exclude='postmaster.opts' --exclude='pg_wal/*' $WORK/live/ $WORK/bk/; printf '%s\\n' \$? > $WORK/copy.status
SELECT labelfile FROM pg_backup_stop(true);
SQL
[ "$(cat "$WORK/copy.status")" = 0 ] || { echo "rsync failed"; exit 1; }
tail -n +2 "$WORK/stop.out" | sed '/^$/d' > "$WORK/bk/backup_label"
grep -q 'START WAL LOCATION' "$WORK/bk/backup_label" || { echo "no label"; exit 1; }
Q $P1 "insert into t select g, md5(g::text) from generate_series(260001,270000) g" >/dev/null
Q $P1 "select pg_switch_wal()" >/dev/null; sleep 3

# 4: pg_verifybackup "fails" on a good backup
echo "--- pg_verifybackup (expected: many errors, none of them real corruption)"
if pg_verifybackup "$WORK/bk" > "$WORK/verify.log" 2>&1; then
  echo "expected stale-manifest failure was absent"; exit 1
fi
sed -E 's/"[^"]*"/"X"/; s/[0-9]+/N/g' "$WORK/verify.log" | sort | uniq -c | sort -rn
cmp -s "$WORK/bk/backup_manifest" "$WORK/live/backup_manifest" || { echo "manifest differs"; exit 1; }
echo "backup_manifest in the backup is byte-identical to the stale one in the live directory"

# 5: restore drill on an isolated copy
D="$WORK/drill"; cp -a "$WORK/bk" "$D"; chmod 700 "$D"; mkdir -p "$D/pg_wal"; rm -f "$D/postmaster.pid" "$D/backup_manifest"
cat >> "$D/postgresql.auto.conf" <<EOF
restore_command = 'cp $WORK/arch/%f %p'
recovery_target = 'immediate'
recovery_target_action = 'pause'
EOF
touch "$D/recovery.signal"
DRILL="$D"
pg_ctl -D "$D" -l "$WORK/drill.log" -w -t 120 -o "-c data_directory=$D -p $P2 -c listen_addresses=127.0.0.1 -c archive_mode=off" start >/dev/null 2>&1 || { echo "drill start failed"; tail -5 "$WORK/drill.log"; exit 1; }
ROWS=$(Q $P2 'select count(*) from t'); REC=$(Q $P2 'select pg_is_in_recovery()::text')
echo "--- drill: rows at consistency = $ROWS (live has 270000), in_recovery = $REC"
grep -E "consistent recovery state|pausing at the end of recovery" "$WORK/drill.log" | cut -c1-120
[ "$ROWS" = 260000 ] && [ "$REC" = true ] || { echo "recovery mismatch"; exit 1; }
pg_ctl -D "$D" -m fast -w stop >/dev/null 2>&1; DRILL=""
pg_ctl -D "$WORK/live" -m fast -w stop >/dev/null 2>&1; LIVE=""
echo "done; remove $WORK when finished"
