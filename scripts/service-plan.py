#!/usr/bin/env python3
"""Print a launchd cut-over/rollback pattern. Never execute service commands."""
import argparse
import json
import shlex
from pathlib import Path


def plan(config, rollback=False):
    loaded = config['loaded']
    if not loaded or not set(loaded) <= set(config['services']):
        raise ValueError('loaded set must be nonempty and declared')
    lines = ['# Save the actually loaded set and back up definitions before stopping anything.']
    if not rollback:
        for item in config['services'].values():
            lines.append(shlex.join(['cp', '-p', item['live'], item['backup']]))
    for label in loaded:
        live = config['services'][label]['live']
        lines.append(shlex.join(['launchctl', 'bootout', config['domain'], live]))
    for label, item in config['services'].items():
        lines.append(shlex.join(['cp', '-p', item['backup' if rollback else 'staged'], item['live']]))
    for label in loaded:
        lines.append(shlex.join(['launchctl', 'bootstrap', config['domain'], config['services'][label]['live']]))
    lines += ['# Verify the running count equals the saved loaded count.',
              '# Inspect every full process command line against the selected tree.',
              '# On rollback, verify no definition references the new tree.',
              '# Switch and verify the interactive wrapper and shell alias too.']
    return lines


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest')
    p.add_argument('--rollback', action='store_true')
    a = p.parse_args()
    print('\n'.join(plan(json.loads(Path(a.manifest).read_text()), a.rollback)))
