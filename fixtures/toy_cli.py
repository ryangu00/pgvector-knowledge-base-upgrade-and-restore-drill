#!/usr/bin/env python3
"""Offline revision protocol fixture. State is a caller-selected JSON file."""
import argparse
import json
from pathlib import Path
import sys

p = argparse.ArgumentParser()
p.add_argument('state')
p.add_argument('action', choices=['put', 'get'])
p.add_argument('--body', default='synthetic body')
p.add_argument('--force', action='store_true')
p.add_argument('--expected-revision')
p.add_argument('--write-through', action='store_true')
a = p.parse_args()
file = Path(a.state)
state = json.loads(file.read_text()) if file.exists() else {}
if a.action == 'get':
    print(json.dumps(state))
elif a.write_through:
    print(json.dumps({'error': 'owner_unavailable'})); sys.exit(1)
elif state and not a.force and a.expected_revision != state.get('revision'):
    print(json.dumps({'error': 'revision_conflict'})); sys.exit(1)
else:
    revision = str(int(state.get('revision', '0')) + 1)
    state = {'body': a.body, 'revision': revision, 'written': False}
    file.write_text(json.dumps(state))
    print(json.dumps(state))
