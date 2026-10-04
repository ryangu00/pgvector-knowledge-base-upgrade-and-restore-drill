#!/usr/bin/env python3
"""A toy migration reads its isolated config, cancels no jobs, changes only its copy."""
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3

config = json.loads(Path(os.environ['APP_CONFIG']).read_text())
statefile = Path(config['fixture_state'])
state = json.loads(statefile.read_text())
assert state['active_jobs'] == 0
assert Path(os.environ['APP_STATE'], 'completed.jsonl').is_file()
assert Path(os.environ['HOME']).is_dir()
assert os.environ['APP_PHASES'] == 'none'
assert os.environ['APP_NO_INSTALL'] == '1'
assert 'INHERITED_SECRET_FIXTURE' not in os.environ
with closing(sqlite3.connect(config['fixture_database'])) as database:
    for migration in sorted(Path(config['fixture_migrations']).glob('*.sql')):
        database.executescript(migration.read_text())
    database.commit()
    state['version'] = database.execute("SELECT value FROM config WHERE key = 'version'").fetchone()[0]
statefile.write_text(json.dumps(state))
print('toy migration applied')
