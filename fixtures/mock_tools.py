#!/usr/bin/env python3
"""Deterministic command doubles. They model control flow, not PostgreSQL recovery."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

root = Path(os.environ['MOCK_ROOT'])
name = Path(sys.argv[0]).name
args = sys.argv[1:]
failure = os.environ.get('FAIL_STEP', '')
with (root / 'calls.jsonl').open('a') as f:
    f.write(json.dumps({'tool': name, 'args': args}) + '\n')


def option(flag, default=None):
    return args[args.index(flag) + 1] if flag in args else default


def state():
    return json.loads((root / 'state.json').read_text())


def save(value):
    (root / 'state.json').write_text(json.dumps(value))


if failure == name:
    sys.exit(2 if name == 'lsof' else 1)
if name == 'lsof':
    sys.exit(0 if failure == 'busy' else 1)
elif name == 'initdb':
    data = Path(option('-D')); data.mkdir()
    (data / 'postgresql.conf').write_text('')
    (data / 'pg_wal').mkdir()
elif name == 'pg_basebackup':
    s = state(); dest = Path(option('-D'))
    shutil.copytree(s['data'], dest)
    (dest / 'backup_manifest').write_text('synthetic stale manifest')
elif name == 'pg_verifybackup':
    print('checksum mismatch for file "synthetic"')
    sys.exit(1)
elif name == 'rsync':
    source, dest = Path(args[-2]), Path(args[-1])
    if failure == 'copy' or (failure == 'ship' and '--timeout=300' in args):
        print('rsync: unexpected end of file', file=sys.stderr); sys.exit(1)
    excluded = [a.split('=', 1)[1] for a in args if a.startswith('--exclude=')]
    if source.is_dir():
        def ignore(directory, names):
            return [n for n in names if n in excluded or n + '/' in excluded or
                    (Path(directory).name == 'pg_wal' and 'pg_wal/*' in excluded)]
        shutil.copytree(source, dest, dirs_exist_ok=True, symlinks=True, ignore=ignore)
elif name == 'pg_archivecleanup':
    (root / 'cleaned').touch()
elif name == 'pg_ctl':
    data = Path(option('-D'))
    if args[-1] == 'stop':
        if failure == 'stop':
            sys.exit(1)
        (data / 'postmaster.pid').unlink(missing_ok=True)
    elif args[-1] == 'start':
        if failure == 'start':
            sys.exit(1)
        config = (data / 'postgresql.auto.conf').read_text() if (data / 'postgresql.auto.conf').exists() else ''
        s = state(); s['data'] = str(data); s['recovery'] = 'false' if "'promote'" in config else 'true'
        save(s); (data / 'postmaster.pid').write_text('synthetic')
        if option('-l'):
            Path(option('-l')).write_text('consistent recovery state reached\npausing at the end of recovery\n')
elif name == 'psql':
    query = option('-c') or sys.stdin.read()
    s = state()
    port = option('-p')
    if 'pg_backup_start' in query:
        for line in query.splitlines():
            if line.startswith('\\! '):
                # psql ignores shell exit status; the caller must check its marker.
                subprocess.run(line[3:], shell=True)
        print('0/START')
        if failure != 'label':
            print('START WAL LOCATION: 0/START (file ' + '0' * 24 + ')')
    elif 'generate_series' in query:
        count = int(re.search(r'generate_series\(\d+,(\d+)\)', query).group(1))
        (Path(s['data']) / 'fixture-count').write_text(str(count))
    elif 'pg_switch_wal' in query:
        print('0/START')
    elif "current_setting('port')" in query:
        print(s.get('reported_port', port))
    elif "current_setting('data_directory')" in query:
        print(s['data'] + '|off|' + s['recovery'])
    elif 'pg_is_in_recovery()::text' in query:
        print(s['recovery'])
    elif 'jsonb_array_elements_text' in query:
        variables = [args[i+1] for i, a in enumerate(args) if a == '-v']
        slugs = json.loads(next(x.split('=',1)[1] for x in variables if x.startswith('slugs=')))
        rows = json.loads((Path(s['data']) / 'rows.json').read_text())
        for row in rows:
            if row['slug'] in slugs: print(json.dumps(row))
    elif "where key = 'version'" in query.lower() or "where key='version'" in query.lower():
        print(s.get('production_version', '144') if port == os.environ.get('PROD_PORT') else s.get('version', '144'))
    elif query.lower().startswith('delete from'):
        s['credential_keys'] = 0; save(s)
    elif '~*' in query:
        print(s.get('credential_keys', 0))
    elif 'Bearer ' in query:
        print(s.get('credential_values', 0))
    elif query.startswith('UPDATE jobs'):
        s['active_jobs'] = 0; save(s)
    elif 'count(*) FROM jobs' in query:
        print(s.get('active_jobs', 0))
    elif 'UPDATE sources' in query:
        s['old_paths'] = 0; save(s)
    elif 'source-content/' in query:
        print(s.get('old_paths', 0))
    elif 'count(*)' in query:
        if 'from t' in query and 'fixture-count' in [p.name for p in Path(s['data']).iterdir()]:
            print((Path(s['data']) / 'fixture-count').read_text())
        elif 'text_projection_revision' in query:
            print(s.get('pending', 0))
        elif port == os.environ.get('PROD_PORT'):
            print(s.get('production_count', 3))
        else:
            print(s.get('drill_count', 3))
    else:
        print('unhandled mock SQL', file=sys.stderr); sys.exit(1)
else:
    print('unknown mock tool', file=sys.stderr); sys.exit(1)
