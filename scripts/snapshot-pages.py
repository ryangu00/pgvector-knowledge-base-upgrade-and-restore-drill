#!/usr/bin/env python3
"""Example schema snapshot and outer-join diff; never emit page bodies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlsplit
from pg_lab import ident, json_write


def connection_env(name):
    """Expand a connection URI into separate libpq environment fields."""
    url = urlsplit(os.environ[name])
    if (url.scheme not in ('postgres', 'postgresql') or not url.hostname or
            not url.username or not url.path.startswith('/') or len(url.path) < 2 or
            url.query or url.fragment):
        raise ValueError('expected a PostgreSQL URI with host, user and database, without query or fragment')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('PG') and key != name}
    env.update(PGHOST=url.hostname, PGPORT=str(url.port or 5432),
               PGUSER=unquote(url.username), PGDATABASE=unquote(url.path[1:]))
    if url.password is not None:
        env['PGPASSWORD'] = unquote(url.password)
    return env


def digest(value):
    return hashlib.md5(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def snapshot(rows):
    data = {}
    for row in rows:
        key = str(row['id'])
        if key in data:
            raise ValueError('duplicate page id')
        fm = row.get('frontmatter') or {}
        data[key] = {'s': row['slug'], 'c': digest(row.get('body') or ''),
                     't': digest(row.get('timeline') or ''),
                     'fm': {k: digest(v) for k, v in fm.items()},
                     'pv': fm.get('privacy'), 'd': row.get('deleted_at') is not None,
                     'u': row.get('updated_at'), 'f': row.get('fact_count', 0),
                     'g': row.get('tag_count', 0)}
    return data


def difference(before, after):
    events = []
    for key, x in before.items():
        y = after.get(key)
        if y is None:
            events.append(('GONE', x['s']))
            continue
        flags = [('SOFTDELETED', not x['d'] and y['d']), ('RESTORED', x['d'] and not y['d']),
                 ('BODY_CHANGED', x['c'] != y['c']), ('TIMELINE_CHANGED', x['t'] != y['t']),
                 ('PRIVACY_CHANGED', x['pv'] != y['pv']),
                 ('FACTS_DECREASED', y['f'] < x['f']), ('TAGS_DECREASED', y['g'] < x['g'])]
        for flag, changed in flags:
            if changed:
                events.append((flag, x['s']))
        for name, value in x['fm'].items():
            if name not in y['fm']:
                events.append(('FM_KEY_LOST', x['s']))
            elif value != y['fm'][name]:
                events.append(('FM_VALUE_CHANGED', x['s']))
    events.extend(('NEW_PAGES', after[k]['s']) for k in sorted(set(after) - set(before)))
    return events


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='op', required=True)
    s = sub.add_parser('snap')
    s.add_argument('output')
    s.add_argument('--rows', help='offline synthetic JSON array')
    s.add_argument('--dsn-env', default='DATABASE_URL')
    s.add_argument('--psql', default='psql')
    s.add_argument('--schema', default='configs/snapshot-schema.json')
    d = sub.add_parser('diff')
    d.add_argument('before')
    d.add_argument('after')
    args = p.parse_args()
    if args.op == 'diff':
        events = difference(json.loads(Path(args.before).read_text()),
                            json.loads(Path(args.after).read_text()))
        print(json.dumps(events))
        return
    if args.rows:
        rows = json.loads(Path(args.rows).read_text())
    else:
        schema = json.loads(Path(args.schema).read_text())
        columns = ','.join(ident(v) + ' as ' + ident(k) for k, v in schema['columns'].items())
        query = 'select row_to_json(t) from (select ' + columns + ' from ' + ident(schema['table']) + ') t'
        result = subprocess.run([args.psql, '-X', '-At', '-v', 'ON_ERROR_STOP=1',
                                 '-c', query], env=connection_env(args.dsn_env),
                                text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError('snapshot query failed')
        rows = [json.loads(line) for line in result.stdout.splitlines()]
    data = snapshot(rows)
    json_write(args.output, data)
    print(json.dumps({'pages': len(data), 'live': sum(not r['d'] for r in data.values())}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, KeyError):
        print('snapshot failed; inspect local inputs and connection settings', file=sys.stderr)
        sys.exit(1)
