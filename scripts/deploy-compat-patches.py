#!/usr/bin/env python3
"""Reverse-patch staged results; replace only byte-identical bases. Dry-run default."""
import argparse
import json
from pathlib import Path
import os
import shutil
import subprocess
import tempfile


def deploy(manifest, apply=False):
    manifest = Path(manifest).resolve()
    outcomes = []
    for entry in json.loads(manifest.read_text()):
        live, staged, diff = [manifest.parent / entry[k] for k in ('live', 'staged', 'diff')]
        status = 'live_missing'
        if live.is_file():
            with tempfile.TemporaryDirectory(dir=manifest.parent) as work:
                base = Path(work) / 'base'
                shutil.copy2(staged, base)
                process = subprocess.run(['patch', '-s', '-R', '-f', str(base), str(diff)],
                                         capture_output=True)
                if process.returncode:
                    status = 'reverse_patch_failed'
                elif base.read_bytes() != live.read_bytes():
                    status = 'live_changed_since_stage(skip)'
                else:
                    status = 'ready'
                    if apply:
                        backup = live.with_name(live.name + '.bak-compat')
                        if backup.exists():
                            status = 'backup_exists(skip)'
                        else:
                            mode = live.stat().st_mode & 0o7777
                            shutil.copy2(live, backup)
                            shutil.copyfile(staged, live)
                            os.chmod(live, mode)
                            status = ('applied' if live.read_bytes() == staged.read_bytes()
                                      else 'apply_verify_failed')
        outcomes.append({'file': entry['live'], 'status': status})
    return outcomes


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    result = deploy(args.manifest, args.apply)
    print(json.dumps(result, indent=2))
    raise SystemExit(any(r['status'] not in ('ready', 'applied') for r in result))
