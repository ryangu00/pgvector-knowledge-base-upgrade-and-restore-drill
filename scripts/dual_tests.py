#!/usr/bin/env python3
"""Serial tree comparison, one subprocess per test file, optional one retry."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess


def run_tree(tree, files, command, retries, timeout):
    failures, flaky = set(), set()
    for name in files:
        failed_once = False
        for attempt in range(retries + 1):
            try:
                result = subprocess.run(command + [name], cwd=tree, timeout=timeout,
                                        capture_output=True, text=True)
                passed = result.returncode == 0
            except subprocess.TimeoutExpired:
                passed = False
            if passed:
                if failed_once:
                    flaky.add(name)
                break
            failed_once = True
        else:
            failures.add(name)
    return failures, flaky


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('integration')
    p.add_argument('pristine')
    p.add_argument('--file')
    p.add_argument('--pattern', default='test_*.py')
    p.add_argument('--retry', type=int, choices=[0, 1], default=1)
    p.add_argument('--timeout', type=int, default=600)
    args = p.parse_args()
    command = shlex.split(os.environ.get('TEST_CMD', 'python3'))
    results = []
    for tree in (args.integration, args.pristine):
        files = [args.file] if args.file else sorted(str(f.relative_to(tree))
                                                    for f in Path(tree).rglob(args.pattern))
        if not files:
            raise ValueError('no test files')
        results.append(run_tree(tree, files, command, args.retry, args.timeout))
    (a, af), (b, bf) = results
    print(json.dumps({'integration_only': sorted(a - b), 'pristine_only': sorted(b - a),
                      'both': sorted(a & b), 'integration_flaky': sorted(af),
                      'pristine_flaky': sorted(bf)}, indent=2))
    return bool(a - b)


if __name__ == '__main__':
    raise SystemExit(main())
