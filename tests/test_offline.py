import contextlib
import copy
import difflib
import importlib.util
import io
import itertools
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'fixtures'))
import pg_lab
import rehearsal
from toy_git import ToyGit


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.offline-', dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / 'bin'; self.bin.mkdir()
        tool = ROOT / 'fixtures/mock_tools.py'
        tool.chmod(0o755)
        for name in ('psql', 'pg_ctl', 'pg_archivecleanup', 'rsync', 'lsof',
                     'initdb', 'pg_basebackup', 'pg_verifybackup'):
            (self.bin / name).symlink_to(tool)
        self.source = self.root / 'source'; self.source.mkdir()
        (self.source / 'postgresql.conf').write_text('# synthetic\n')
        (self.source / 'postgresql.auto.conf').write_text('')
        (self.source / 'pg_tblspc').mkdir()
        (self.source / 'pg_wal').mkdir()
        (self.source / 'backup_manifest').write_text('stale fixture')
        shutil.copyfile(ROOT / 'fixtures/rows.json', self.source / 'rows.json')
        self.archive = self.root / 'archive'; self.archive.mkdir()
        (self.archive / 'retained-wal').write_text('synthetic WAL placeholder')
        self.state = self.root / 'state.json'
        self.state.write_text(json.dumps({'active_jobs': 2, 'old_paths': 2, 'credential_keys': 1}))
        self.env = dict(os.environ, PGBIN=str(self.bin), PGDATA=str(self.source),
                        ARCHIVE_DIR=str(self.archive), BACKUP_ROOT=str(self.root / 'backups'),
                        DRILL_ROOT=str(self.root / 'drills'), RESULT=str(self.root / 'result.json'),
                        DBNAME='postgres', PROD_PORT='55991', PORT='55992', DRILL_TABLE='pages',
                        RSYNC=str(self.bin / 'rsync'), LSOF=str(self.bin / 'lsof'),
                        MOCK_ROOT=str(self.root), TMPDIR=str(self.root), PYTHONDONTWRITEBYTECODE='1',
                        CLEANUP_PROVEN='yes', FAIL_STEP='')
        self.env.pop('MIRROR', None)
        self.env.pop('ALERT_CMD', None)

    def command(self, name, env=None, args=()):
        return subprocess.run(['bash', str(ROOT / 'scripts' / name), *args],
                              env=env or self.env, cwd=ROOT, text=True, capture_output=True)

    def backup(self):
        result = self.command('backup-low-level.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        return next((self.root / 'backups').glob('basebackup-*'))

    def result(self):
        return json.loads((self.root / 'result.json').read_text())

    def change(self, **values):
        state = json.loads(self.state.read_text()); state.update(values)
        self.state.write_text(json.dumps(state))

    def rehearsal_env(self):
        database = self.root / 'copy.sqlite'
        if not database.exists():
            with contextlib.closing(sqlite3.connect(database)) as connection:
                connection.executescript("CREATE TABLE config(key text, value text); INSERT INTO config VALUES('version','144');")
            shutil.copy2(database, self.root / 'source.sqlite')
        config = self.root / 'config.json'
        config.write_text(json.dumps({'database_url': 'postgresql://127.0.0.1:55992/postgres',
                                     'fixture_state': str(self.state),
                                     'fixture_database': str(database),
                                     'fixture_migrations': str(ROOT / 'fixtures/migrations')}))
        ledger = self.root / 'completed.jsonl'; ledger.write_text('{"migration":"fixture"}\n')
        self.env.update(APP_CONFIG=str(config), DRILL_HOME=str(self.root / 'home'),
                        APP_STATE=str(self.root / 'app-state'), APP_PATH=os.environ['PATH'],
                        COMPLETION_LEDGER=str(ledger), VERSION_SQL=str(ROOT / 'configs/version.sql'),
                        ACTIVE_JOBS_SQL=str(ROOT / 'configs/active-jobs.sql'),
                        CANCEL_JOBS_SQL=str(ROOT / 'configs/cancel-jobs.sql'), EXPECTED_PROD_VERSION='144',
                        MIGRATE_LOG=str(self.root / 'migrate.log'),
                        APP_CMD=json.dumps([sys.executable, str(ROOT / 'fixtures/toy_migrate.py')]),
                        APP_MIGRATE_ARGS='[]', INHERITED_SECRET_FIXTURE='must-not-propagate',
                        PATH_UPDATES=str(ROOT / 'configs/path-updates.sql'),
                        PATH_CHECK=str(ROOT / 'configs/path-check.sql'))


class BackupRestoreTests(Workspace):
    def test_stale_manifest_reproduction_control_flow_with_command_doubles(self):
        work = self.root / 'manifest-repro'
        result = self.command('repro-stale-manifest.sh', dict(self.env, WORK=str(work)))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('rows at consistency = 260000', result.stdout)
        self.assertTrue((work / 'bk/backup_manifest').exists())
        self.assertFalse((work / 'live/postmaster.pid').exists())

    def test_backup_excludes_manifest_and_reconciles_mock_restore(self):
        backup = self.backup()
        self.assertFalse((backup / 'data/backup_manifest').exists())
        self.assertIn('START WAL LOCATION', (backup / 'data/backup_label').read_text())
        result = self.command('restore-drill.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.result()['ok'])
        self.assertFalse(Path(self.result()['workspace']).exists())

    def test_rsync_error_is_not_backup_ok_and_never_cleans(self):
        result = self.command('backup-low-level.sh', dict(self.env, FAIL_STEP='copy'))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('backup OK', result.stdout)
        self.assertFalse((self.root / 'cleaned').exists())
        self.assertTrue((self.archive / 'retained-wal').exists())

    def test_psql_failure_or_missing_label_never_cleans(self):
        for failure in ('psql', 'label'):
            with self.subTest(failure=failure):
                result = self.command('backup-low-level.sh', dict(self.env, FAIL_STEP=failure))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / 'cleaned').exists())

    def test_missing_psql_refuses_without_removing_wal(self):
        (self.bin / 'psql').unlink()
        self.assertNotEqual(self.command('backup-low-level.sh').returncode, 0)
        self.assertTrue((self.archive / 'retained-wal').exists())

    def test_cleanup_failure_is_not_success(self):
        result = self.command('backup-low-level.sh', dict(self.env, FAIL_STEP='pg_archivecleanup'))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('backup OK', result.stdout)

    def test_mirror_failure_prevents_cleanup(self):
        for failure in ('ship', ''):
            result = self.command('backup-low-level.sh', dict(self.env, MIRROR=str(self.root / 'mirror'),
                                  FAIL_STEP=failure, MIRROR_CLEANUP_CMD='/usr/bin/false'))
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.root / 'cleaned').exists())

    def test_retention_keeps_two_newest(self):
        root = self.root / 'retention'; root.mkdir()
        for date in ('2026-07-04', '2026-09-27', '2026-09-29'):
            (root / ('basebackup-' + date)).mkdir()
        pg_lab.retain(root)
        self.assertEqual([p.name for p in sorted(root.iterdir())],
                         ['basebackup-2026-09-27', 'basebackup-2026-09-29'])

    def test_each_redirection_guard_refuses_without_start(self):
        backup = self.backup(); data = backup / 'data'
        for setting in ('data_directory', 'hba_file', 'ident_file', 'external_pid_file',
                        'include', 'include_dir', 'include_if_exists', 'primary_conninfo', 'primary_slot_name'):
            with self.subTest(setting=setting):
                (data / 'postgresql.conf').write_text(setting + " = 'sensitive-fixture-value'\n")
                result = self.command('restore-drill.sh')
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('sensitive-fixture-value', result.stdout + result.stderr)
                self.assertIn(setting, self.result()['detail'])
        (data / 'postgresql.conf').write_text('')
        for kind in ('symlink', 'tablespace_map', 'tablespace'):
            item = data / ('pg_tblspc/item' if kind == 'tablespace' else kind)
            if kind == 'symlink': item.symlink_to(self.source)
            else: item.touch()
            result = self.command('restore-drill.sh')
            self.assertNotEqual(result.returncode, 0)
            item.unlink()
        calls = (self.root / 'calls.jsonl').read_text()
        self.assertNotIn('"tool": "pg_ctl"', calls)

    def test_busy_port_and_missing_lsof_refuse(self):
        self.backup()
        for failure in ('busy', 'lsof'):
            result = self.command('restore-drill.sh', dict(self.env, FAIL_STEP=failure))
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.result()['ok'])
        result = self.command('restore-drill.sh', dict(self.env, LSOF=str(self.bin / 'missing-lsof')))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('lsof unavailable', self.result()['detail'])

    def test_lsof_version_status_is_not_a_precondition(self):
        self.backup()
        version = subprocess.run([self.env['LSOF'], '-v'], env=self.env, capture_output=True)
        self.assertEqual(version.returncode, 1)
        (self.root / 'calls.jsonl').unlink()
        result = self.command('restore-drill.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = [json.loads(line) for line in (self.root / 'calls.jsonl').read_text().splitlines()]
        probes = [call['args'] for call in calls if call['tool'] == 'lsof']
        self.assertEqual(probes, [['-nP', '-iTCP:55992', '-sTCP:LISTEN']])

    def test_all_layout_paths_reject_equality_and_containment(self):
        names = ('PGDATA', 'ARCHIVE_DIR', 'BACKUP_ROOT', 'DRILL_ROOT', 'RESULT')
        for a, b in itertools.combinations(names, 2):
            for relation in ('equal', 'child', 'parent'):
                env = self.env.copy()
                if relation == 'equal':
                    env[b] = env[a]
                elif relation == 'child':
                    env[b] = str(Path(env[a]) / 'nested')
                else:
                    env[a] = str(Path(env[b]) / 'nested')
                with self.subTest(a=a, b=b, relation=relation), patch.dict(os.environ, env):
                    with self.assertRaisesRegex(ValueError, 'must not overlap'):
                        pg_lab.guard_paths()

    def test_unsafe_layout_refuses_before_backup_restore_or_reset_writes(self):
        self.backup()
        protected = self.source / 'postgresql.conf'
        original = protected.read_bytes()
        alias = self.root / 'result-alias'
        alias.symlink_to(protected)
        for values in ({'DRILL_ROOT': str(self.source)},
                       {'BACKUP_ROOT': str(self.source.parent)},
                       {'ARCHIVE_DIR': str(self.source.parent)},
                       {'RESULT': str(protected)}, {'RESULT': str(alias)}):
            for script in ('backup-low-level.sh', 'restore-drill.sh', 'rehearsal-reset.sh'):
                with self.subTest(values=values, script=script):
                    (self.root / 'calls.jsonl').unlink(missing_ok=True)
                    result = self.command(script, dict(self.env, **values))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((self.root / 'calls.jsonl').exists())
                    self.assertEqual(protected.read_bytes(), original)
                    self.assertFalse((self.root / 'result.json').exists())
                    self.assertFalse((self.root / 'drills').exists())

    def test_archive_quote_or_newline_refused_before_restore(self):
        self.backup()
        for suffix in ("'quote", '\nnewline', '\rcarriage-return'):
            result = self.command('restore-drill.sh',
                                  dict(self.env, ARCHIVE_DIR=str(self.root / ('archive' + suffix))))
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.root / 'drills').exists())
            self.assertFalse((self.root / 'result.json').exists())

    def test_stop_failure_keeps_workspace(self):
        self.backup()
        result = self.command('restore-drill.sh', dict(self.env, FAIL_STEP='stop'))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(Path(self.result()['workspace']).is_dir())
        self.assertIn('stop failed', self.result()['detail'])

    def test_empty_or_out_of_window_count_fails(self):
        self.backup()
        for count in (0, 10):
            self.change(drill_count=count)
            self.assertNotEqual(self.command('restore-drill.sh').returncode, 0)

    def test_result_write_failure_fails(self):
        self.backup()
        target = self.root / 'result-directory'
        target.mkdir()
        self.assertNotEqual(self.command('restore-drill.sh',
                            dict(self.env, RESULT=str(target))).returncode, 0)
        calls = [json.loads(line) for line in (self.root / 'calls.jsonl').read_text().splitlines()]
        self.assertTrue(any(call['tool'] == 'pg_ctl' and call['args'][-1] == 'stop' for call in calls))

    def test_row_extraction_uses_backup_rows_and_private_mode(self):
        self.backup()
        changed = json.loads((self.source / 'rows.json').read_text())
        changed[0]['body'] = 'changed after backup'
        (self.source / 'rows.json').write_text(json.dumps(changed))
        slugs = self.root / 'slugs.txt'
        slugs.write_text('fixture/alpha\nfixture/beta\nfixture/gamma\n')
        dump = self.root / 'dump.jsonl'
        env = dict(self.env, SLUG_FILE=str(slugs), DUMP=str(dump), ROW_COLUMNS='slug,body')
        result = self.command('extract-rows-from-backup.sh', env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = [json.loads(line) for line in dump.read_text().splitlines()]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['body'], 'original alpha')
        self.assertEqual(dump.stat().st_mode & 0o777, 0o600)

    def test_writable_copy_rewrites_paths_and_reset_restores(self):
        self.backup(); self.rehearsal_env()
        result = self.command('restore-writable-copy.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.state.read_text())['old_paths'], 0)
        workspace = Path(self.result()['workspace'])
        self.assertTrue((workspace / '.rehearsal-owned').exists())
        result = self.command('rehearsal-reset.sh', dict(self.env, WORKSPACE=str(workspace)))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((workspace / 'data').exists())
        self.assertTrue((workspace / 'drill.log').is_file())
        replacement = Path(self.result()['workspace'])
        self.assertNotEqual(replacement, workspace)
        self.assertTrue((replacement / 'data').is_dir())

    def test_wrong_port_refused_before_credential_delete_and_reset(self):
        self.rehearsal_env()
        for name in ('strip-credentials.sh', 'rehearsal-reset.sh'):
            result = self.command(name, dict(self.env, PORT=self.env['PROD_PORT']))
            self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.state.read_text())['credential_keys'], 1)

    def test_remaining_credential_value_is_a_failure(self):
        self.change(credential_values=1)
        result = self.command('strip-credentials.sh')
        self.assertNotEqual(result.returncode, 0)

    def test_migration_isolated_and_production_version_unchanged(self):
        self.rehearsal_env()
        result = self.command('rehearsal-migrate.sh')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('production_version_unchanged=true', result.stdout)
        self.assertEqual(json.loads(self.state.read_text())['version'], '165')
        with contextlib.closing(sqlite3.connect(self.root / 'source.sqlite')) as database:
            self.assertEqual(database.execute('SELECT value FROM config').fetchone()[0], '144')
        with contextlib.closing(sqlite3.connect(self.root / 'copy.sqlite')) as database:
            self.assertEqual(database.execute('SELECT value FROM config').fetchone()[0], '165')

    def test_migration_rejects_wrong_config_and_empty_ledger(self):
        self.rehearsal_env()
        Path(self.env['APP_CONFIG']).write_text('{"database_url":"postgresql://127.0.0.1:5432/postgres"}')
        self.assertNotEqual(self.command('rehearsal-migrate.sh').returncode, 0)
        self.assertFalse((self.root / 'calls.jsonl').exists())
        self.rehearsal_env()
        Path(self.env['COMPLETION_LEDGER']).write_text('')
        self.assertNotEqual(self.command('rehearsal-migrate.sh').returncode, 0)
        calls = [json.loads(line) for line in (self.root / 'calls.jsonl').read_text().splitlines()]
        self.assertTrue(all(call['args'][call['args'].index('-p') + 1] != self.env['PROD_PORT']
                            for call in calls if call['tool'] == 'psql'))

    def test_migration_bad_config_precedes_unavailable_database(self):
        self.rehearsal_env()
        Path(self.env['APP_CONFIG']).write_text('{"database_url":"postgresql://127.0.0.1:5432/postgres"}')
        with patch.dict(os.environ, self.env), patch.object(pg_lab, 'sql') as drill_sql, \
                patch.object(rehearsal, 'sql', side_effect=RuntimeError('database unavailable')) as source_sql:
            with self.assertRaisesRegex(ValueError, 'database_url not drill'):
                rehearsal.migrate()
        drill_sql.assert_not_called()
        source_sql.assert_not_called()


class DataAndForkTests(Workspace):
    def test_snapshot_connection_keeps_credentials_out_of_argv(self):
        m = module('snapshot-pages')
        dsn = 'postgresql://fixture_user:fixture%2Dpassword@127.0.0.1:55992/postgres'
        output = self.root / 'snapshot.json'
        result = subprocess.CompletedProcess([], 0, stdout='{"id":"a","slug":"fixture/alpha"}\n')
        env = dict(self.env, SNAPSHOT_DSN=dsn, PGHOST='localhost', PGSERVICE='unused-fixture',
                   PGPASSWORD='stale-fixture-password')
        with patch.dict(os.environ, env), patch.object(sys, 'argv',
                ['snapshot-pages.py', 'snap', str(output), '--dsn-env', 'SNAPSHOT_DSN']), \
                patch.object(subprocess, 'run', return_value=result) as query, patch('builtins.print'):
            m.main()
        args, kwargs = query.call_args
        self.assertNotIn(dsn, args[0])
        self.assertNotIn('fixture-password', args[0])
        child = kwargs['env']
        self.assertNotIn('SNAPSHOT_DSN', child)
        self.assertNotIn('PGSERVICE', child)
        self.assertEqual({k: v for k, v in child.items() if k.startswith('PG')},
                         dict(PGHOST='127.0.0.1', PGPORT='55992', PGUSER='fixture_user',
                              PGDATABASE='postgres', PGPASSWORD='fixture-password'))
        self.assertTrue(kwargs['capture_output'])
        self.assertEqual(set(json.loads(output.read_text())), {'a'})

    def test_snapshot_query_failure_does_not_echo_connection_details(self):
        command = self.root / 'snapshot-psql'
        command.write_text('#!/bin/sh\nprintf "%s\\n" "$PGPASSWORD" >&2\nexit 1\n')
        command.chmod(0o755)
        dsn = 'postgresql://fixture_user:fixture-secret@127.0.0.1:55992/postgres'
        output = self.root / 'snapshot.json'
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/snapshot-pages.py'),
                                 'snap', str(output), '--psql', str(command)],
                                env=dict(self.env, DATABASE_URL=dsn), text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(dsn, result.stdout + result.stderr)
        self.assertNotIn('fixture-secret', result.stdout + result.stderr)
        self.assertIn('snapshot failed', result.stderr)
        self.assertFalse(output.exists())

    def test_snapshot_reports_all_event_kinds_without_body(self):
        m = module('snapshot-pages')
        before = json.loads((ROOT / 'fixtures/rows.json').read_text())
        after = copy.deepcopy(before)
        after[0].update(body='replacement-body-never-print', timeline='changed', frontmatter={},
                        fact_count=0, tag_count=0, deleted_at='2026-09-29')
        after[1]['deleted_at'] = None
        after.pop(); after.append(dict(before[2], id='new', slug='fixture/new'))
        a, b = m.snapshot(before), m.snapshot(after)
        # A separate value mutation from the key-removal case.
        b['b']['fm'] = {'category': 'different'}
        a['b']['fm'] = {'category': 'original'}
        events = m.difference(a, b)
        self.assertEqual({e[0] for e in events}, {'GONE','SOFTDELETED','RESTORED','BODY_CHANGED',
                         'TIMELINE_CHANGED','FM_KEY_LOST','FM_VALUE_CHANGED','PRIVACY_CHANGED',
                         'FACTS_DECREASED','TAGS_DECREASED','NEW_PAGES'})
        self.assertNotIn('replacement-body-never-print', json.dumps(b) + json.dumps(events))
        self.assertTrue(all(len(e) == 2 for e in events))

    def test_compat_dry_run_apply_skip_and_permissions(self):
        m = module('deploy-compat-patches'); entries = []
        for name in ('alpha', 'beta', 'gamma'):
            live, staged, diff = [self.root / (name + suffix) for suffix in ('.live','.staged','.diff')]
            live.write_text('old\n'); live.chmod(0o755); staged.write_text('new\n')
            diff.write_text(''.join(difflib.unified_diff(['old\n'], ['new\n'], fromfile='old', tofile='new')))
            entries.append(dict(live=live.name, staged=staged.name, diff=diff.name))
        (self.root / 'gamma.live').write_text('changed since staging\n')
        manifest = self.root / 'manifest.json'; manifest.write_text(json.dumps(entries))
        result = m.deploy(manifest)
        self.assertEqual([r['status'] for r in result], ['ready','ready','live_changed_since_stage(skip)'])
        self.assertEqual((self.root / 'alpha.live').read_text(), 'old\n')
        result = m.deploy(manifest, True)
        self.assertEqual(result[0]['status'], 'applied')
        self.assertEqual((self.root / 'alpha.live').stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.root / 'alpha.live.bak-compat').read_text(), 'old\n')

    def test_prescreen_five_commit_loose_object_repository(self):
        m = module('prescreen-rebase'); toy = ToyGit(self.root / 'toy')
        with patch.object(subprocess, 'run', side_effect=AssertionError('no git commands allowed')):
            rows = m.prescreen(toy, 'base', 'local', 'target')
        self.assertEqual(len(rows), 5)
        self.assertEqual({r['upstream_status'] for r in rows},
                         {'overlap','new','clean','deleted upstream','name collision'})
        self.assertTrue(all(not r['checked'] and not r['verdict'] for r in rows))
        self.assertIn('LocalOnly:absent', ';'.join(r['functions'] for r in rows))
        self.assertTrue(toy.git.joinpath('HEAD').is_file())

    def test_prescreen_known_present_symbol_and_portable_expression(self):
        m = module('prescreen-rebase'); toy = ToyGit(self.root / 'toy')
        self.assertEqual(m.symbols('@@ -1 +1 @@ def stable():\n+class LocalOnly:'), ['LocalOnly','stable'])
        calls = []
        def query(*args, **kwargs):
            calls.append(args)
            value = toy(*args, **kwargs)
            if args[0] == 'show' and '--unified=0' in args and args[-1] == 'overlap.py':
                value = '@@ -1 +1 @@ def stable():\n' + value
            return value
        rows = m.prescreen(query, 'base', 'local', 'target')
        self.assertIn('stable:present', ';'.join(r['functions'] for r in rows))
        expressions = [c[3] for c in calls if c[0] == 'grep']
        self.assertTrue(all('[[:space:]]' in x and '\\s' not in x for x in expressions))

    def test_dual_run_compares_failure_sets(self):
        integ, clean = self.root / 'integration', self.root / 'pristine'
        integ.mkdir(); clean.mkdir()
        for tree in (integ, clean):
            (tree / 'test_shared.py').write_text('raise SystemExit(1)\n')
            (tree / 'test_pass.py').write_text('pass\n')
        (integ / 'test_local.py').write_text('raise SystemExit(1)\n')
        (clean / 'test_upstream.py').write_text('raise SystemExit(1)\n')
        result = self.command('dual-test-run.sh', dict(self.env, TEST_CMD=sys.executable), (str(integ),str(clean)))
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertEqual(report['integration_only'], ['test_local.py'])
        self.assertEqual(report['both'], ['test_shared.py'])
        result = self.command('triage-one-file.sh', dict(self.env, TEST_CMD=sys.executable,
                              INTEGRATION_TREE=str(integ), PRISTINE_TREE=str(clean)), ('test_shared.py',))
        self.assertEqual(json.loads(result.stdout)['both'], ['test_shared.py'])

    def test_service_plans_are_print_only_and_loaded_set_exact(self):
        for name in ('cutover.sh', 'rollback.sh'):
            result = self.command(name, args=(str(ROOT / 'configs/service-plan.json'),))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count('launchctl bootstrap'), 1)
            self.assertEqual(result.stdout.count('launchctl bootout'), 1)
        self.assertFalse((self.root / 'calls.jsonl').exists())

    def test_cli_write_protocol_rejects_and_reads_back(self):
        base = [sys.executable, str(ROOT / 'fixtures/toy_cli.py'), str(self.root / 'writer.json')]
        env = dict(self.env, WRITE_NEW=json.dumps(base + ['put','--body','initial']),
                   WRITE_UPDATE=json.dumps(base + ['put','--body','bad']),
                   WRITE_FORCE=json.dumps(base + ['put','--force','--body','final']),
                   WRITE_READ=json.dumps(base + ['get']), EXPECTED_BODY='final')
        result = self.command('write-protocol-drill.sh', env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        denied = subprocess.run(base + ['put','--write-through'], capture_output=True, text=True)
        self.assertEqual(json.loads(denied.stdout)['error'], 'owner_unavailable')

    def test_sampling_uses_isolated_worker_and_supplied_query(self):
        self.rehearsal_env(); self.change(pending=3)
        env = dict(self.env, PENDING_SQL=str(ROOT / 'configs/pending.sql'),
                   WORKER_CMD=json.dumps([sys.executable, str(ROOT / 'fixtures/counter_worker.py'), str(self.state)]),
                   SAMPLE_SECONDS='1', SAMPLE_INTERVAL='1')
        result = self.command('sample-pending.sh', env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"completed": 3', result.stdout)


class SyntaxTests(unittest.TestCase):
    def test_every_python_file_parses_and_shell_script_has_valid_syntax(self):
        import ast
        for folder in ('scripts', 'fixtures', 'tests', 'docs'):
            for file in (ROOT / folder).glob('*.py'):
                ast.parse(file.read_text(), filename=str(file))
        for file in (ROOT / 'scripts').glob('*.sh'):
            result = subprocess.run(['bash', '-n', str(file)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
