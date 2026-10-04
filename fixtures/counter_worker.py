#!/usr/bin/env python3
"""Synthetic rebuild worker; atomically decreases the mock pending counter."""
import json
from pathlib import Path
import sys
import time

path = Path(sys.argv[1])
while True:
    state = json.loads(path.read_text())
    if state.get('pending', 0) > 0:
        state['pending'] -= 1
        temporary = path.with_suffix('.counter-tmp')
        temporary.write_text(json.dumps(state))
        temporary.replace(path)
    time.sleep(0.1)
