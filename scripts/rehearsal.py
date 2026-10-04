#!/usr/bin/env python3
"""Application-neutral rehearsal templates; SQL and argv come from local files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit
from pg_lab import path, required, port_guard, guard_paths, sql, stop, run, json_write, ident


def guard():
    config = json.loads(path('APP_CONFIG').read_text())
    url = urlsplit(config['database_url'])
    if url.scheme not in ('postgres', 'postgresql') or url.hostname != '127.0.0.1' or str(url.port) != required('PORT'):
        raise ValueError('database_url not drill')
    if url.path != '/' + required('DBNAME'):
        raise ValueError('database name mismatch')
    port_guard()
    return config


def isolated_env():
    home, state = path('DRILL_HOME'), path('APP_STATE')
    if home == Path.home() or state == Path.home():
        raise ValueError('dedicated home and state required')
    home.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    env = {'HOME': str(home), 'PATH': required('APP_PATH'),
           'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8',
           'TMPDIR': str(home / 'tmp'),
           'GIT_CEILING_DIRECTORIES': str(home)}
    (home / 'tmp').mkdir(exist_ok=True)
    env[os.environ.get('APP_HOME_VAR', 'APP_HOME')] = str(home)
    env[os.environ.get('APP_STATE_VAR', 'APP_STATE')] = str(state)
    env[os.environ.get('APP_CONFIG_VAR', 'APP_CONFIG')] = str(path('APP_CONFIG'))
    env[os.environ.get('APP_NO_INSTALL_VAR', 'APP_NO_INSTALL')] = '1'
    env[os.environ.get('APP_PHASES_VAR', 'APP_PHASES')] = 'none'
    return env


def strip():
    port_guard()
    table = ident(os.environ.get('CONFIG_TABLE', 'config'))
    key = ident(os.environ.get('CONFIG_KEY', 'key'))
    value = ident(os.environ.get('CONFIG_VALUE', 'value'))
    pattern = '(api_key|apikey|token|secret|password|credential)'
    sql(f"delete from {table} where {key} ~* '{pattern}'")
    left = sql(f"select count(*) from {table} where {key} ~* '{pattern}'")
    values = sql(f"select count(*) from {table} where {value}::text ~ '(sk-[A-Za-z0-9]|Bearer )'")
    print('credential_keys_remaining=' + left)
    print('credential_values_remaining=' + values)
    if left != '0' or values != '0':
        raise ValueError('credential check incomplete; review copy before migrating')


def migrate():
    guard()
    env = isolated_env()
    ledger = path('COMPLETION_LEDGER')
    if not ledger.is_file() or not ledger.read_text().strip():
        raise ValueError('completion ledger missing')
    target = path('APP_STATE') / 'completed.jsonl'
    shutil.copy2(ledger, target)
    version_query = path('VERSION_SQL').read_text()
    before = sql(version_query, required('PROD_PORT'))
    if before != required('EXPECTED_PROD_VERSION'):
        raise ValueError('unexpected production version')
    rc = 1
    try:
        guard()
        sql(path('CANCEL_JOBS_SQL').read_text())
        if sql(path('ACTIVE_JOBS_SQL').read_text()) != '0':
            raise ValueError('active jobs remain')
        guard()
        argv = json.loads(required('APP_CMD')) + json.loads(required('APP_MIGRATE_ARGS'))
        with path('MIGRATE_LOG').open('w') as log:
            os.chmod(log.name, 0o600)
            rc = subprocess.run(argv, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
    finally:
        after = sql(version_query, required('PROD_PORT'))
        print('production_version_unchanged=' + str(before == after).lower())
        if before != after:
            raise ValueError('production version changed')
    if rc:
        raise RuntimeError('migration failed')
    print('migration OK')


def reset():
    guard_paths()
    port_guard()
    root, workspace = path('DRILL_ROOT'), path('WORKSPACE')
    if workspace.parent != root or workspace == root:
        raise ValueError('workspace outside drill root')
    if (workspace / '.rehearsal-owned').read_text().strip() != required('PORT'):
        raise ValueError('workspace ownership mismatch')
    stop(workspace / 'data')
    shutil.rmtree(workspace / 'data')
    state = path('APP_STATE')
    state.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path('COMPLETION_LEDGER'), state / 'completed.jsonl')
    # A fresh unique workspace is allocated; previous logs stay available.
    from pg_lab import restore
    if restore('writable'):
        raise RuntimeError('reset restore failed')
    strip()


def protocol():
    env = os.environ.copy()
    expectations = [('WRITE_NEW', 0), ('WRITE_UPDATE', 1), ('WRITE_FORCE', 0)]
    for name, expected in expectations:
        result = subprocess.run(json.loads(required(name)), capture_output=True, text=True, env=env)
        receipt = json.loads(result.stdout)
        if result.returncode != expected:
            raise ValueError(name + ' unexpected status')
        if expected:
            if receipt.get('error') != 'revision_conflict':
                raise ValueError('expected revision_conflict')
        elif not receipt.get('revision'):
            raise ValueError('missing committed revision')
        print(name + ': expected outcome')
    # Read-back is a separate command and assertion, never the receipt alone.
    result = run(json.loads(required('WRITE_READ')))
    if json.loads(result).get('body') != required('EXPECTED_BODY'):
        raise ValueError('read-back mismatch')


def sample():
    guard()
    env = isolated_env()
    embed = os.environ.get('SAMPLE_MODE') == 'embed'
    query = path('PENDING_SQL').read_text()
    duration = int(os.environ.get('SAMPLE_SECONDS', '180' if embed else '300'))
    interval = int(os.environ.get('SAMPLE_INTERVAL', '20'))
    if duration <= 0 or interval <= 0:
        raise ValueError('positive sample duration and interval required')
    before = int(sql(query))
    process = subprocess.Popen(json.loads(required('WORKER_CMD')), env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    start = time.monotonic()
    try:
        while time.monotonic() - start < duration:
            if process.poll() is not None:
                raise RuntimeError('worker exited during sample')
            time.sleep(min(interval, max(0, duration - (time.monotonic() - start))))
            print(json.dumps({'pending': int(sql(query))}))
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait()
    print(json.dumps({'completed': before - int(sql(query))}))


if __name__ == '__main__':
    try:
        {'strip': strip, 'migrate': migrate, 'reset': reset,
         'protocol': protocol, 'sample': sample}[sys.argv[1]]()
    except (ValueError, OSError, RuntimeError, KeyError):
        print('rehearsal failed; inspect local configuration', file=sys.stderr)
        sys.exit(1)
