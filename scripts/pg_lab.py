#!/usr/bin/env python3
"""Shared PostgreSQL mechanics, adapted from the supplied operational scripts.

No application schema is assumed except the explicitly selected example queries.
External commands are argument lists; errors never print connection credentials.
"""
import datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit


def required(name):
    value = os.environ.get(name)
    if not value:
        raise ValueError('set ' + name)
    return value


def path(name):
    return Path(required(name)).expanduser().resolve()


def guard_paths():
    names = ('PGDATA', 'ARCHIVE_DIR', 'BACKUP_ROOT', 'DRILL_ROOT', 'RESULT')
    paths = {name: path(name) for name in names}
    for index, name in enumerate(names):
        for other in names[index + 1:]:
            a, b = paths[name], paths[other]
            if a == b or a in b.parents or b in a.parents:
                raise ValueError(name + ' and ' + other + ' must not overlap')
    if any(char in str(paths['ARCHIVE_DIR']) for char in ("'", '\n', '\r')):
        raise ValueError('ARCHIVE_DIR must not contain single quotes or newlines')
    return paths


def run(args, **kwargs):
    result = subprocess.run([str(x) for x in args], text=True,
                            capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(Path(str(args[0])).name + ' failed')
    return result.stdout.strip()


def binary(name):
    candidate = path('PGBIN') / name
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ValueError(name + ' unavailable')
    return str(candidate)


def ident(value):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*(\.[A-Za-z_][A-Za-z_0-9]*)*', value):
        raise ValueError('invalid SQL identifier')
    return '.'.join('"' + part + '"' for part in value.split('.'))


def sql(query, port=None, variables=None):
    args = [binary('psql'), '-X', '-At', '-v', 'ON_ERROR_STOP=1',
            '-h', '127.0.0.1', '-p', str(port or os.environ.get('PORT', '55432')),
            '-d', required('DBNAME')]
    for key, value in (variables or {}).items():
        args += ['-v', key + '=' + value]
    return run(args + ['-f', '-'], input=query + '\n')


def json_write(target, value, mode=0o600):
    target = Path(target)
    with target.open('w') as f:
        os.chmod(target, mode)
        json.dump(value, f, indent=2)
        f.write('\n')


def guard_data(data):
    for root, dirs, files in os.walk(data, followlinks=False):
        if any((Path(root) / name).is_symlink() for name in dirs + files):
            raise ValueError('symlinks in backup (refuse)')
    if (data / 'tablespace_map').exists():
        raise ValueError('tablespace_map present (refuse)')
    if (data / 'pg_tblspc').exists() and any((data / 'pg_tblspc').iterdir()):
        raise ValueError('pg_tblspc not empty (refuse)')
    names = ('data_directory|hba_file|ident_file|external_pid_file|include|'
             'include_dir|include_if_exists|primary_conninfo|primary_slot_name')
    for name in ('postgresql.conf', 'postgresql.auto.conf'):
        config = data / name
        if config.exists():
            match = re.search(r'^\s*(' + names + r')(?:\s|=)',
                              config.read_text(), re.M | re.I)
            if match:
                raise ValueError('config setting refused: ' + match.group(1))


def port_guard():
    port = required('PORT')
    if not port.isdigit() or port == os.environ.get('PROD_PORT', '5432'):
        raise ValueError('not a drill port')
    if sql("select current_setting('port')") != port:
        raise ValueError('server port mismatch')


def stop(data):
    run([binary('pg_ctl'), '-D', data, '-m', 'fast', '-t', '120', 'stop'])


def copy_backup(source, target, status):
    status.unlink(missing_ok=True)
    run([required('RSYNC'), '-a', '--exclude=archive/', '--exclude=pg_wal/*',
         '--exclude=postmaster.pid', '--exclude=postmaster.opts',
         '--exclude=backup_manifest', str(source) + '/', str(target) + '/'])
    status.write_text('0\n')


def retain(root):
    # The original POSIX-safe ordering: sort -r | tail -n +3.
    candidates = [p for p in root.glob('basebackup-*')
                  if p.is_dir() and not p.is_symlink()]
    ordered = run(['sort', '-r'], input=''.join(str(p) + '\n' for p in candidates))
    old = run(['tail', '-n', '+3'], input=ordered + '\n')
    for name in old.splitlines():
        shutil.rmtree(name)


def backup():
    paths = guard_paths()
    source, archive, root = (paths[name] for name in ('PGDATA', 'ARCHIVE_DIR', 'BACKUP_ROOT'))
    binary('psql')
    root.mkdir(parents=True, exist_ok=True)
    size = int(run(['du', '-sk', archive]).split()[0])
    if size > 50 * 1024 * 1024:
        alert('WAL archive exceeds 50 GB')
    name = 'basebackup-' + datetime.date.today().isoformat()
    dest = root / name
    if dest.is_symlink():
        raise ValueError('backup destination is a symlink')
    if dest.exists():
        shutil.rmtree(dest)
    (dest / 'data').mkdir(parents=True)
    status = dest / 'copy.status'
    helper = [sys.executable, str(Path(__file__).resolve()), 'copy',
              str(source), str(dest / 'data'), str(status)]
    query = ("SELECT pg_backup_start('daily-backup', true);\n\\! " +
             shlex.join(helper) + '\nSELECT labelfile FROM pg_backup_stop(true);\n')
    output = sql(query, os.environ.get('PROD_PORT', '5432'))
    if not status.exists() or status.read_text().strip() != '0':
        raise RuntimeError('rsync failed; no cleanup')
    label = '\n'.join(output.splitlines()[1:]).strip() + '\n'
    match = re.search(r'START WAL LOCATION:.*\(file ([0-9A-F]{24})\)', label)
    if not match:
        raise RuntimeError('missing START WAL LOCATION; no cleanup')
    (dest / 'data' / 'backup_label').write_text(label)
    mirror = os.environ.get('MIRROR')
    if mirror:
        # MIRROR is an rsync destination; no name is embedded in this cookbook.
        run([required('RSYNC'), '-a', '--timeout=300', str(dest), mirror])
    # Cleanup is an explicit opt-in after an independently reconciled restore.
    if os.environ.get('CLEANUP_PROVEN') == 'yes':
        if mirror:
            # Hook performs remote archive cleanup and retention, and must fail loud.
            run(shlex.split(required('MIRROR_CLEANUP_CMD')) + [match.group(1)])
        run([binary('pg_archivecleanup'), archive, match.group(1)])
        retain(root)
    print('backup OK')


def alert(message):
    print('ALERT: ' + message, file=sys.stderr)
    if os.environ.get('ALERT_CMD'):
        run(shlex.split(os.environ['ALERT_CMD']) + [message])


def restore(mode='drill'):
    # Validate before the try/finally: an unsafe RESULT must never be written.
    paths = guard_paths()
    started = False
    workspace = None
    ok = False
    detail = ''
    result = {'ok': False, 'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'pages_drill': None, 'pages_prod': None}
    t0 = time.monotonic()
    try:
        os.environ.setdefault('LC_ALL', 'en_US.UTF-8')
        os.environ.setdefault('LANG', 'en_US.UTF-8')
        port = required('PORT')
        if not port.isdigit() or port == os.environ.get('PROD_PORT', '5432'):
            raise ValueError('not a drill port')
        backups = sorted(p for p in paths['BACKUP_ROOT'].glob('basebackup-*')
                         if (p / 'data/backup_label').is_file() and not p.is_symlink())
        if not backups:
            raise ValueError('no basebackup with backup_label')
        chosen = backups[-1]
        result['backup'] = str(chosen)
        lsof = shutil.which(required('LSOF'))
        if lsof is None:
            raise ValueError('lsof unavailable')
        check = subprocess.run([lsof, '-nP', '-iTCP:' + port, '-sTCP:LISTEN'],
                               capture_output=True)
        if check.returncode == 0:
            raise ValueError('port busy')
        if check.returncode != 1:
            raise ValueError('port probe failed')
        root = paths['DRILL_ROOT']
        root.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix='drill-', dir=root))
        os.chmod(workspace, 0o700)
        data = workspace / 'data'
        run([required('RSYNC'), '-a', '--exclude=backup_manifest',
             str(chosen / 'data') + '/', str(data) + '/'])
        guard_data(data)
        os.chmod(data, 0o700)
        (data / 'pg_wal').mkdir(exist_ok=True)
        for name in ('postmaster.pid', 'standby.signal'):
            (data / name).unlink(missing_ok=True)
        archive = paths['ARCHIVE_DIR']
        command = 'cp ' + shlex.quote(str(archive) + '/%f') + ' %p'
        action = 'promote' if mode == 'writable' else 'pause'
        with (data / 'postgresql.auto.conf').open('a') as f:
            f.write("\nrestore_command = '" + command.replace("'", "''") + "'\n" +
                    "recovery_target = 'immediate'\nrecovery_target_action = '" + action +
                    "'\nhot_standby = on\nprimary_conninfo = ''\nprimary_slot_name = ''\n")
        (data / 'recovery.signal').touch()
        # Mark before start: a failed start can still leave a running postmaster.
        started = True
        options = shlex.join(['-c', 'data_directory=' + str(data), '-p', port,
                             '-c', 'listen_addresses=127.0.0.1', '-c', 'archive_mode=off',
                             '-c', 'unix_socket_directories=' + str(workspace)])
        run([binary('pg_ctl'), '-D', data, '-l', workspace / 'drill.log', '-w',
             '-t', '900', '-o', options, 'start'])
        query = ('select count(*) from ' + ident(os.environ.get('DRILL_TABLE', 'pages')))
        expected = 'false' if mode == 'writable' else 'true'
        for attempt in range(180):
            try:
                result['pages_drill'] = int(sql(query))
                if mode == 'writable' and sql('select pg_is_in_recovery()::text') != 'false':
                    result['pages_drill'] = None
                    time.sleep(5)
                    continue
                break
            except RuntimeError:
                time.sleep(5)
        if result['pages_drill'] is None:
            raise RuntimeError('drill db never became queryable')
        actual = sql("select current_setting('data_directory')||'|'||"
                     "current_setting('archive_mode')||'|'||pg_is_in_recovery()")
        if actual != str(data) + '|off|' + expected:
            raise ValueError('drill instance not isolated as expected')
        if mode == 'extract':
            slugs = [s.strip() for s in path('SLUG_FILE').read_text().splitlines() if s.strip()]
            if not slugs:
                raise ValueError('no slug list')
            columns = ','.join(ident(x) for x in required('ROW_COLUMNS').split(','))
            table = ident(required('DRILL_TABLE'))
            slug_column = ident(os.environ.get('SLUG_COLUMN', 'slug'))
            query = (f'select row_to_json(t) from (select {columns} from {table} where '
                     f'{slug_column} in (select jsonb_array_elements_text(:\'slugs\'::jsonb))) t;')
            dump = sql(query, variables={'slugs': json.dumps(slugs)})
            with path('DUMP').open('w') as f:
                os.chmod(f.name, 0o600)
                f.write(dump + '\n')
        elif mode == 'writable':
            sql(path('PATH_UPDATES').read_text())
            if sql(path('PATH_CHECK').read_text()) != '0':
                raise ValueError('production paths remain')
        else:
            result['pages_prod'] = int(sql(query, os.environ.get('PROD_PORT', '5432')))
            a, b = result['pages_drill'], result['pages_prod']
            if not (a > 0 and b * 9 <= a * 10 <= b * 11):
                raise ValueError('page count out of range')
        ok, detail = True, 'recovered; isolation verified'
    except (ValueError, OSError, RuntimeError) as exc:
        detail = str(exc)
    finally:
        keep = mode == 'writable'
        if started and not (ok and keep):
            try:
                stop(workspace / 'data')
            except (RuntimeError, OSError, ValueError):
                ok, keep = False, True
                detail += '; stop failed, directory kept'
        if workspace:
            result['workspace'] = str(workspace)
            if keep:
                if ok:
                    (workspace / '.rehearsal-owned').write_text(required('PORT') + '\n')
            else:
                try:
                    shutil.rmtree(workspace)
                except OSError:
                    ok = False
                    detail += '; cleanup failed'
        result.update(ok=ok, detail=detail, seconds=int(time.monotonic() - t0))
        json_write(paths['RESULT'], result)
        print(json.dumps(result))
    return 0 if ok else 1


def main():
    operation = sys.argv[1]
    if operation == 'copy':
        copy_backup(*(Path(x) for x in sys.argv[2:5]))
    elif operation == 'backup':
        try:
            backup()
        except Exception:
            alert('backup chain failed; subsequent cleanup skipped')
            raise
    else:
        return restore(operation)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError):
        print('operation failed; inspect local configuration and retained workspace', file=sys.stderr)
        sys.exit(1)
