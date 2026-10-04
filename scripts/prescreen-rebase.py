#!/usr/bin/env python3
"""Rewritten mechanical pre-screen. A name hit is not behavior equivalence."""
import argparse
import csv
import re
import subprocess
import sys


class Git:
    def __init__(self, repository):
        self.repository = repository

    def __call__(self, *args, missing=False):
        result = subprocess.run(['git', '-C', self.repository, *args],
                                capture_output=True, text=True)
        if result.returncode and not (missing and result.returncode == 1):
            raise RuntimeError('git query failed: ' + args[0])
        return result.stdout.strip()


def symbols(patch):
    found = set()
    for line in patch.splitlines():
        if line.startswith('@@') or (line.startswith('+') and not line.startswith('+++')):
            found.update(re.findall(r'\b(?:def|function|class)\s+([A-Za-z_][A-Za-z_0-9]*)', line))
    return sorted(found)


def prescreen(git, base, local, target):
    merge = git('merge-base', base, target)
    date = git('show', '-s', '--format=%cI', merge)
    commits = git('rev-list', '--reverse', '--no-merges', base + '..' + local).splitlines()
    rows = []
    for commit in commits:
        subject = git('show', '-s', '--format=%s', commit)
        files = git('diff-tree', '--no-commit-id', '--name-only', '-r', commit).splitlines()
        for file in files:
            at_base = bool(git('ls-tree', '--name-only', base, '--', file))
            at_target = bool(git('ls-tree', '--name-only', target, '--', file))
            history = git('log', '--since=' + date, '--format=%H', target, '--', file)
            if not at_base:
                status = 'name collision' if at_target else 'new'
            elif not at_target:
                status = 'deleted upstream'
            else:
                status = 'overlap' if history else 'clean'
            patch = git('show', '--format=', '--unified=0', commit, '--', file)
            marks = []
            for name in symbols(patch):
                regex = ('(^|[[:space:]])(def|function|class)[[:space:]]+' +
                         name + '([^[:alnum:]_]|$)')
                hits = git('grep', '-n', '-E', regex, target, missing=True)
                marks.append(name + ':' + ('present' if hits else 'absent'))
            rows.append({'commit': commit, 'subject': subject, 'file': file,
                         'upstream_status': status, 'functions': ';'.join(marks),
                         'checked': '', 'verdict': ''})
    return rows


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('base')
    p.add_argument('local_branch')
    p.add_argument('target')
    p.add_argument('--repo', default='.')
    args = p.parse_args()
    rows = prescreen(Git(args.repo), args.base, args.local_branch, args.target)
    writer = csv.DictWriter(sys.stdout, fieldnames=['commit', 'subject', 'file',
                            'upstream_status', 'functions', 'checked', 'verdict'])
    writer.writeheader()
    writer.writerows(rows)
