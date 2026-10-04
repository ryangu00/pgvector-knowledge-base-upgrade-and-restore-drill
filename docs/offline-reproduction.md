# Reproduction contracts and public adaptations

The historical source scripts were read and adapted, not shipped as private operational copies.
The application and runtime are intentionally unnamed. SQL tables, command vectors and state-variable names are explicit integration points.
No production logs, page dumps, credentials or service definitions are included.

The standard-library test suite uses command doubles, synthetic JSON rows, an in-memory HTTP handler and a loose-object toy Git repository.
It does not start PostgreSQL, open sockets, execute git commands or manage services.
The public stale-manifest script has not been exercised against a real PostgreSQL cluster during writing; only its test-suite mock run validates the control flow.
The fact sheet separately records the earlier real-cluster reproduction reported in chapter 2.

## Start with the tests

From the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

Temporary directories are allocated under the repository and removed after each test.
Python is the only language runtime dependency; shell entry points need bash.
The compatibility-deployment test also needs the already installed `patch` command.
No dependency download is performed.

The fixture counts, paths, versions and command outcomes are test inputs, not additional historical results.
`fixtures/mock_tools.py` implements only the PostgreSQL command contracts exercised here.
Its SQL matching is deliberately narrow; passing it is not SQL-engine validation.
The test for service plans asserts printed actions only.

## First real proof: stale manifest

With PostgreSQL 15+ and rsync already installed, choose unused loopback ports and a new work directory:

```bash
PGBIN=<absolute-postgresql-bin-directory> \
WORK="$PWD/work-manifest" P1=55991 P2=55992 \
bash scripts/repro-stale-manifest.sh
```

The placeholders must be replaced; the correct installation path is unknown here.
The script refuses an existing work directory instead of deleting it.
It sets a locale for macOS and starts only the throwaway data directories it creates.

Sequence:

1. Initialize a cluster with local archiving and create the synthetic table.
2. Take a `pg_basebackup`, stop the first cluster, and use the physical copy as the live cluster.
3. Insert more rows, then perform a low-level online backup in one psql session.
4. Retain the old manifest deliberately in this reproduction, unlike the normal backup script.
5. Check the copy's exit status and save `backup_label`.
6. Expect `pg_verifybackup` to fail, verify that the manifest is byte-identical, and restore a separate copy.
7. Require 260,000 rows at first consistency, with recovery paused, while the live fixture has 270,000.
8. Stop both clusters. The work directory and local diagnostic files remain for inspection.

The fact sheet records the reproduction outcome, but does not supply its date or a portable checksum-error count guarantee.
This exercise is not a general claim that verifier failures are harmless.
The cause here is specifically the inherited stale manifest, demonstrated by byte comparison and restore.

## Reusable backup and paused restore

`configs/example.env` names the required environment without embedding installation paths.
Set absolute `PGBIN`, `RSYNC` and `LSOF` paths before sourcing it.
Use your own throwaway cluster; exact installation and cluster-provisioning commands depend on your platform and are not established by the fact sheet.
Its required settings are archiving enabled and an archive command that copies `%p` to the chosen archive directory's `%f`.

```bash
# After a throwaway cluster with archiving is running:
. configs/example.env
"$PGBIN/psql" -X -v ON_ERROR_STOP=1 -h 127.0.0.1 -p "$PROD_PORT" \
  -d "$DBNAME" -f fixtures/schema.sql
bash scripts/backup-low-level.sh
bash scripts/restore-drill.sh
```

Common inputs:

| Variable | Contract |
|---|---|
| `PGBIN` | Absolute directory containing compatible PostgreSQL binaries |
| `PGDATA` | Source cluster directory; never a drill destination |
| `ARCHIVE_DIR` | Existing WAL archive, read-only to the restore procedure; must not contain single quotes or newlines |
| `BACKUP_ROOT` | Separate backup directory; same-day backup replaces its dated directory |
| `DRILL_ROOT` | Parent for unique private workspaces |
| `RESULT` | JSON result path; parent must exist and be writable |
| `DBNAME` | Database name; no embedded user identity |
| `PROD_PORT`, `PORT` | Source and drill ports, distinct; connections explicitly use loopback |
| `DRILL_TABLE` | Count table, optionally schema-qualified; identifier validation applies |
| `LC_ALL`, `LANG` | Explicit locale appropriate to the host |
| `RSYNC`, `LSOF` | Explicit executable paths, including under a restricted service PATH |

The public adapters require `PGDATA`, `ARCHIVE_DIR`, `BACKUP_ROOT`, `DRILL_ROOT` and `RESULT` to be distinct resolved paths; none may contain another.
Supply all five for backup, restore and reset. The historical archive-inside-PGDATA layout is described in chapter 1; the public example uses a separate archive directory.
Path rejection happens before filesystem changes, including result-file writes, so an unsafe layout produces a nonzero exit without a JSON result.

The backup script uses one psql session for start/copy/stop.
A separate copy helper writes its success marker only after rsync succeeds.
A missing marker fails even when psql returns success and prints a label.
Label saving, mirror copy, cleanup, retention and result-file operations are also checked.
The marker check and `backup_manifest` exclusion are publication-time corrections, not the historical implementation.

Optional controls:

| Variable | Contract |
|---|---|
| `ALERT_CMD` | Command plus fixed arguments parsed as an argument vector; receives the alert message as its last argument |
| `MIRROR` | Optional rsync destination; absent means local-only backup |
| `MIRROR_CLEANUP_CMD` | Required with mirror cleanup; receives the start WAL filename; implements remote cleanup and newest-2 retention |
| `CLEANUP_PROVEN` | Cleanup disabled unless exactly `yes`; set only after independently reconciling this chain |

The mirror hook is intentionally generic: no SSH destination or private remote directory is published.
It must report failure, not swallow it. Mirror cleanup runs before local cleanup; any failure prevents subsequent cleanup.
No cleanup procedure is transactional across machines.
Local retention preserves the source rule `sort -r | tail -n +3`.

The restore rejects symlinks, tablespaces, redirection settings, include directives and replication settings before starting a server.
It requires a working port probe; missing `lsof` is a failure.
Command-line settings pin storage, port, loopback, archive mode and a workspace-local socket directory.
The live query must confirm the selected directory, `off`, and `true` for paused recovery.
A positive count within +/-10% of the source is only a plausibility check.
Stop failure preserves the directory, returns nonzero and sets `ok: false`.
Failure to write the result is also nonzero.

## Extract original rows

Back up the three synthetic rows first, then change a row in the source.
Create a slug file containing the synthetic slugs to recover.

```bash
export SLUG_FILE="$PWD/work/slugs.txt"
export ROW_COLUMNS=slug,body,timeline,frontmatter
export SLUG_COLUMN=slug
export DUMP="$PWD/work/recovered.jsonl"
bash scripts/extract-rows-from-backup.sh
```

The same restore guards apply. SQL identifiers are validated and slug values are bound as a JSON array.
The dump is created with mode 600 before row contents are written.
Compare it to the original fixture rows, not to the now-modified source.
Unlike the routine drill, extraction does not require source-count agreement after an incident.
No historical page slugs or row data are included.

## Writable rehearsal, reset and migration

Set `PATH_UPDATES` to a SQL file that rewrites every application path in the copy.
Set `PATH_CHECK` to a query returning a single `0` when no production paths remain.
The examples operate on `sources` and `config`; replace them for your schema.

```bash
export PATH_UPDATES="$PWD/configs/path-updates.sql"
export PATH_CHECK="$PWD/configs/path-check.sql"
bash scripts/restore-writable-copy.sh
```

Read the workspace path from `RESULT`.
A successful writable restore stays running and has a `.rehearsal-owned` marker containing its port.
The marker does not replace the running server's directory, archive-mode and recovery-state checks.
Failed writable restores retain their workspace for inspection and attempt a stop.

Migration adapter contract:

| Input | Meaning |
|---|---|
| `APP_CONFIG` | JSON file with `database_url` matching the loopback drill port and database exactly |
| `DRILL_HOME`, `APP_STATE` | Resolved dedicated directories, distinct from the real home |
| `APP_HOME_VAR`, `APP_STATE_VAR`, `APP_CONFIG_VAR` | Application-specific environment-variable names; defaults are generic |
| `APP_NO_INSTALL_VAR`, `APP_PHASES_VAR` | Names for disable-install and phase-allowlist controls; verify your application implements them |
| `APP_PATH` | PATH beginning with your new-version CLI shim and required binaries |
| `COMPLETION_LEDGER` | Nonempty production-equivalent completion record; copied to `APP_STATE/completed.jsonl` |
| `VERSION_SQL`, `EXPECTED_PROD_VERSION` | Read-only version query and expected source value |
| `CANCEL_JOBS_SQL`, `ACTIVE_JOBS_SQL` | Copy-only cancellation statement and a zero-active-jobs check |
| `APP_CMD`, `APP_MIGRATE_ARGS` | JSON argv arrays, not shell command strings |
| `MIGRATE_LOG` | Private local output path for the application command |

```bash
export VERSION_SQL="$PWD/configs/version.sql"
export CANCEL_JOBS_SQL="$PWD/configs/cancel-jobs.sql"
export ACTIVE_JOBS_SQL="$PWD/configs/active-jobs.sql"
# Fill every remaining adapter input before running these commands.
bash scripts/strip-credentials.sh
bash scripts/rehearsal-migrate.sh
```

Credential stripping requires the server to report the drill port before deleting anything.
`CONFIG_TABLE`, `CONFIG_KEY` and `CONFIG_VALUE` default to the example schema.
The script prints remaining counts, never credential values, and fails when its key/value checks remain nonzero.
Pattern checks are not an operating-system egress boundary; supply and verify that separately where required.
The historical remaining credential-like row was unresolved, not silently approved.

Migration runs with a newly constructed environment equivalent to `env -i`.
It validates the local database configuration before any database query, then verifies the drill port, installs the ledger, checks the source version, cancels copy jobs, checks zero active jobs, rechecks the config, runs the argv and checks the source version again even if migration fails.
Actual application state locations and no-install flags are unknown until you supply and test the adapter.
The synthetic application in `fixtures/toy_migrate.py` consumes those contracts and executes the SQL files in `fixtures/migrations/` against a SQLite copy using the standard library.
The test verifies its config version changes while a separate source database's version stays unchanged; the PostgreSQL command layer remains mocked.
This is not an implementation of the historical SQL migrations.

For reset, set `WORKSPACE` from the previous writable result and retain all restore/credential variables.
`rehearsal-reset.sh` requires that workspace to be directly under `DRILL_ROOT`, checks its ownership marker and drill port, stops it before deleting data, copies the completion ledger, restores into a fresh workspace and strips credentials.
A failed stop must prevent deletion.
Reset creates a fresh drill workspace and writes its path to `RESULT`; use that new path for the next reset. The prior workspace remains for inspection, including its logs and ownership marker, but its stopped data directory has been removed.

## Write protocol and receipts

The CLI template takes JSON argument arrays in `WRITE_NEW`, `WRITE_UPDATE`, `WRITE_FORCE` and `WRITE_READ`.
It expects create success, `revision_conflict` without a revision, forced success with a revision, and a separate read-back matching `EXPECTED_BODY`.
The fixture can also simulate write-through with no owner, which returns `owner_unavailable`.
It never treats `written: false` alone as failure.
The fixture command shapes are demonstrated in `tests/test_offline.py`.

The HTTP smoke takes `--endpoint`, `--slug`, `--put-tool` and `--get-tool`; `SMOKE_TOKEN` is optional and read only from the environment.
It initializes JSON-RPC, creates, expects a revision conflict, reads the revision, updates with it and reads back the new body.
Both plain JSON and SSE are covered by `fixtures/http_protocol.py` over an in-memory HTTP transport.
No token files, token-generation steps or network services are needed for those tests.
Application receipt payload shapes beyond the example need their own adapter.

## Snapshot and diff

```bash
mkdir -p work
python3 scripts/snapshot-pages.py snap work/before.json --rows fixtures/rows.json
# Write a separately mutated synthetic row file, then:
python3 scripts/snapshot-pages.py snap work/after.json --rows work/changed-rows.json
python3 scripts/snapshot-pages.py diff work/before.json work/after.json
```

Without `--rows`, `--dsn-env` selects an environment variable containing a `postgres://` or `postgresql://` URI with a host, user and database; password and port are optional. Query parameters, fragments and keyword/value DSNs are not supported.
The script parses it into separate `PGHOST`, `PGPORT`, `PGUSER`, `PGDATABASE` and optional `PGPASSWORD` values in the child environment. The original DSN and inherited `PG*` settings are removed from that environment; neither the DSN nor password is passed in command arguments. Query failures suppress psql output and report a generic error.
`configs/snapshot-schema.json` maps each public example field to a table column.
For application-specific facts/tags stored in other tables, provide a view with the required counts and point the mapping at that view.
The example is not a claim about the historical application's schema.

Snapshots include body/timeline hashes, per-frontmatter-value hashes, privacy marker, fact/tag counts, soft-delete state and updated-at.
Diffs outer-join page IDs and emit only slugs and flags: gone, new, soft-deleted, restored, changed body/timeline/frontmatter/privacy, and decreased fact/tag counts.
The original detector is asymmetric for frontmatter: it reports lost or changed old keys, not added keys.
It records updated-at without treating its change alone as content loss.
These metadata artifacts remain private operational evidence even though bodies are excluded.

## Fork pre-screen and test-set comparison

`configs/writer-inventory.csv`, `configs/excluded-writers.csv` and `configs/decisions-ledger.csv` are intentionally empty, header-only templates for the reader to fill in.
See [chapter 6's writer inventory](upgrade-rehearsal.md#writer-inventory-template) for writers and exclusions, and [chapter 5's audit method](rebase-proof.md#what-we-built-or-changed-the-audit-method) for the decisions ledger. They are manual records, not populated datasets consumed by the scripts.

The original pre-screen was not retained. `prescreen-rebase.py` implements the chapter's specification as a new script.
On your own repository, supply the base, local branch and target ref:

```bash
python3 scripts/prescreen-rebase.py <base> <local-branch> <target> --repo <repository> > work/prescreen.csv
TEST_CMD='<interpreter> <per-file-runner>' bash scripts/dual-test-run.sh <integration-tree> <pristine-tree>
INTEGRATION_TREE=<integration-tree> PRISTINE_TREE=<pristine-tree> \
TEST_CMD='<interpreter> <per-file-runner>' bash scripts/triage-one-file.sh <test-file>
```

These are reader commands, not commands executed during authorship.
The pre-screen uses commit history, target paths, patch hunks and portable ERE symbol queries.
The toy fixture builds genuine compressed blob/tree/commit objects and a five-commit local branch directly with the standard library.
A query adapter reads that repository in tests; it does not validate invocation against an installed Git executable.
Packfiles, complex renames and full language parsing are outside that fixture.
Name presence never establishes semantic equivalence, and hunk headers can over-attribute symbols.

The full-test template launches one subprocess per file, integration first then pristine, using the same command.
`--retry 1` and `--timeout 600` are defaults; `--retry 0` disables retries.
It reports integration-only, pristine-only and shared failure sets, plus flaky file sets.
A nonempty integration-only set returns nonzero pending adjudication.
Application-wide isolation and real service-test exclusion still need to be configured in the runner you supply.

## Patch deployment and service patterns

The compatibility manifest is a JSON array with `live`, `staged` and `diff` paths, relative to the manifest directory.
Create each diff from the original file to the staged result.

```bash
python3 scripts/deploy-compat-patches.py work/patches.json
python3 scripts/deploy-compat-patches.py work/patches.json --apply
bash scripts/cutover.sh configs/service-plan.json
bash scripts/rollback.sh configs/service-plan.json
```

The first command is dry-run. A changed live file is skipped and produces nonzero status.
Applying preserves permission bits and keeps an undated `.bak-compat` backup without overwriting an existing backup.
Service scripts only print the launchd pattern from a synthetic manifest.
Replace `<uid>` in `configs/service-plan.json` with the result of `id -u` for the user whose services the plan describes.
Populate its `loaded` set from a real service inventory before adapting it; the cookbook does not detect or register services.
Record the loaded set and back up definitions before stopping anything; restore that saved set for rollback.
Full process-count/path verification and interactive entry-point switching are explicit operator steps in this pattern.

## Sampling

`sample-pending.sh` takes `PENDING_SQL` and a JSON `WORKER_CMD`, plus the rehearsal config and isolated environment inputs.
Default sampling is every 20 seconds for 5 minutes; `SAMPLE_MODE=embed` selects a 180-second window.
Use a pending-embedding count query for embedding mode.
`SAMPLE_SECONDS` and `SAMPLE_INTERVAL` allow short synthetic tests; they are not published performance measurements.
The fixture worker changes a synthetic pending counter in a separate process; the sampler verifies the decrease.
The worker is terminated and reaped on completion or failure.
No model endpoint or rate from the historical host is shipped.
