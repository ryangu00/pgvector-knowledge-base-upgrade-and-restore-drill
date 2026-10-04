# 5. Proving the rebase

Round D, 2026-09-04; serial integration/pristine comparison in the same environment.

### Problem
"The rebase finished with no conflicts left" is not evidence that no hunk was silently dropped. Auto-merge on an upstream that changed thousands of files can lose local behavior without any marker.

### What we built or changed (the audit method)

**1. Mechanical pre-screen (before touching git).** For each local commit and each file it touches, record: status in the target tag (upstream changed it in the same window = overlap; untouched by upstream = clean; our new file = new; deleted upstream; name collision), and for each function or test the commit touched, whether the name still exists in the target tree. Implementation: about 30 lines of Python walking `git show --stat` plus a name search in the target tree (the original script was not kept; the book must ship a rewritten version, spec below).

Pre-screen spec for the writer: input = `<base>..<local-branch>` commit list and a target ref; for each commit list files; classify each file by `git log --since=<merge-base date> <target> -- <file>`; extract function names from the commit's hunk headers and added lines; `git grep -n -E "(^|[[:space:]])(def|function|class)[[:space:]]+<name>" <target>`; output one row per (commit, file) with columns commit, subject, file, upstream status, functions with present/absent marks, "checked" and "verdict" columns left blank for humans.

**2. Self-test the checker.** The first version used `\s\b` in an ERE grep; macOS git's regex does not support that, so it judged every function missing. After switching to `[[:space:]]` it worked. Rule: run the checker on symbols known to exist before trusting any "missing" count.

**3. Decisions ledger.** One row per commit in application order: result of applying (clean, or conflict with files and block count) and the verdict with the reasoning for each conflict. After every resolved conflict: AST parse plus symbol assertions; if the assertions fail, do not stage.

**4. Post-rebase survival check.** Re-run the pre-screen against the rebased tree for the commits' original functions. Every "missing" must map to a deliberate verdict in the ledger; unmapped missing = fail.

**5. Dual full test run.** Same virtual environment, serial, integration tree first and then the pristine upstream tag; compare the sets of failing files; only "failing on integration but not pristine" needs adjudication.

**6. Cut-over and rollback scripts written before cut-over; a rollback drilled right after.**

### Procedure as actually run (round D)

Pre-screen result (102 file rows over 37 non-merge commits):

| Classification | Count |
|---|---|
| Upstream changed the same file in the window (overlap) | 67 |
| File new in our commits | 34 |
| Untouched upstream (clean) | 1 |
| Functions present in the target | 94 |
| Functions missing | 14: 10 are test functions, 3 are our own new symbols, 1 is a production function that really was removed upstream |
| Largest overlap per commit | four commits with 7, 6, 5 and 4 files that upstream also changed in the window |

Rebase outcome: 36 applied, 1 skipped as absorbed, 2 merge commits skipped (zero increment). The one absorbed commit: ours patched one browser-open entry point in tests; upstream had since patched `open`, `open_new`, `open_new_tab` and `get` (more complete).

Conflict patterns that recurred:
1. Upstream refactored our inline block into a helper (4 occurrences): take upstream's version and patch the helper minimally to keep our semantics (for example adding a fence so a budget failure is attributed only to the right worker run).
2. Upstream deleted tests we had modified (3 occurrences): keep only our new tests, do not bring back deleted ones.
3. Adjacent insertions on both sides: keep both.
4. A hunk header misattributed a function (the pre-screen flagged a prompt-building function as ours; we had not touched it; upstream had removed it).

Survival check after the rebase: 314 functions present, 12 missing, all 12 mapped to a deliberate ledger verdict (7 upstream-deleted tests not brought back; 1 removed upstream plus hunk-header misattribution; 1 upstream-deleted test; 2 of our own tests that we deleted deliberately, both the same test listed under two commits; 1 more upstream-deleted test); unmapped 0.

### Targeted test run (our 27 touched or added test files)
First run: 9 failures, 3 root causes:

| Root cause | Fix | Lesson |
|---|---|---|
| A telemetry hook judged "not needed" while resolving three conflicts (the upstream helper replaced the surrounding block) was locked in by two of our tests | Add the hook back into the upstream helper | Tests are the specification; check whether a test pins a behavior before judging it unnecessary |
| Upstream's new batch quality gate requires a goal of at least 10 characters; our test used a 9-character placeholder | Fix the test | Upstream tightened a precondition |
| Upstream passes a new keyword argument to a function our test stubbed with a fixed signature; the TypeError inside a thread was swallowed, yielding an empty result | Give the stub `**kwargs` | A swallowed exception looks like "no output" |

Fourth red, found only at the last file of the run: a test file where 13 of 14 tests failed and a 15th hung (3.5 minutes wall clock for 2.7 seconds of CPU; the clean tree passed 22 tests in 4 seconds). Cause: our silence filter (a chat loop-prevention component) treats replies such as `ok`, `OK`, `okay`, a thumbs-up, and `[ACK]` as intentional silence; upstream's rewritten tests mocked the model reply as `ok`, so delivery never happened and the test waited forever. Fix: apply our renamed fixture reply consistently across the upstream-rewritten tests. Lesson: when a test seems stuck, compare elapsed time to CPU time: 3.5 min / 2.7 s means waiting, not computing.

### Dual full test run results

| | Integration tree | Pristine tag |
|---|---|---|
| Test files | 3,510 | 3,493 |
| Passed | 42,281 | 42,144 |
| Failed | 52 (in 26 files) | 51 (in 25 files) |
| Skipped | 370 | 370 |
| Flaky (re-run once per file) | 0 | 0 |

Set differences: failing only on integration: 2 files; failing only on pristine: 1 file; failing on both: 24 files (environment or upstream: audio devices, systemd, WSL, real launchctl, loopback auth, update-flow tests). The runner runs each test file in its own subprocess (our conftest isolates process-wide singletons per file; invoking pytest on many files directly violates that) with `--file-retries 1 --file-timeout 600`; the first run with 12 workers.

Adjudication of the 2 integration-only files:

| File | Verdict | Reason |
|---|---|---|
| A setup-login test | Accepted deviation | Our patch deliberately seals a direct login path for one provider; our own test covers the new behavior; the clean tree would of course pass the old test |
| A new upstream lint test forbidding raw config reads outside the owner module (it named our assignee-exit read) | Fixed | Added a small function in the owner module for cross-profile raw reads and called it. We refused the alternative of moving the file-name literal further away from the lint window, which is how another existing read passes by accident |

Final: regressions on the branch = 0; branch final state 39 commits (36 cherry-picked + 3 fixups).

### Cut-over and rollback drill

Cut-over script: back up service definitions with a timestamp, record the set of currently loaded services in a file, stop only that set, install new definitions, start the same set, verify process count and that each process's command line points into the new tree. Rollback script: restore the newest backup set and the recorded loaded set, verify counts and that no definition references the new tree.

A drill shortly after cut-over: code-only rollback (no data restore) to see whether the old version starts on the already-migrated state. It did (no schema errors, services reconnected), then forward again. Conclusion for that upgrade: the light rollback suffices; a data restore was not required.

Three false alarms in our own measurement scripts during the drill: a check grepping for a literal path missed a launcher-script service definition; `date -v-1M` argument order was wrong so the observation window was empty; `ps -eo` truncated the command so the "new-version process count" was 0. A later surprise: cut-over changed only the service definitions; the interactive entry point (a wrapper script on PATH and a shell alias) still pointed at the old environment until we added them to the checklist. Post-cut-over traceback comparison must be by line position, because traceback lines carry no timestamp.

### What did not work
- First pre-screen script (ERE regex).
- Running pytest directly over many files (violates per-file isolation).
- A hung wait loop: `pgrep -f '<pattern>'` inside a wrapper shell matches its own command line.

### Pitfalls

| Symptom | Root cause | Fix |
|---|---|---|
| Every function reported missing | `\s\b` unsupported in macOS git ERE | `[[:space:]]`, and self-test the checker |
| `pip install .[all]` gives a broken environment | The `all` extra in the new tag is a partial set | Reconstruct the explicit extras list from the freeze of the old environment |
| Worktree built in the wrong repository | Two repositories, only one shares the production worktrees | `git worktree list` before creating anything |
| Working tree always dirty by one file | Two upstream files differing only by case on a case-insensitive filesystem | Never `git add -A`; add named files |
| Command chain reports exit 1 on success | Last command in the chain was `grep -c` which returns 1 on zero matches | Do not end a chain with grep |

### The rule
A rebase is accepted only when every function the old commits touched exists in the new tree or maps to a deliberate verdict, and when the failing-test set of the integration tree minus that of the clean tree has been adjudicated line by line.

See [offline reproduction](offline-reproduction.md) for the public adaptations and validation boundary.
