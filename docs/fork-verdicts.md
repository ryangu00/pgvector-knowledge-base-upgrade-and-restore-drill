# 4. Which patches survive

Rounds A through E retain their own dates, denominators and unfinished status.

### Problem
A fork with local commits meets a new upstream 12 minor versions (knowledge base) or 10,722 commits (agent runtime) away. The question "which of our patches still matter?" cannot be answered by reading commit titles.

### What we built or changed
A verdict table, one row per local commit, with evidence columns: commit, topic, files touched, rehearsal result (clean / conflict, files), "does upstream have an equivalent?", "does the deployment still depend on it?", verdict. Verdict vocabulary: **keep**, **keep (rewrite)**, **partial**, **skip (absorbed upstream)**, **retire**, **re-evaluate later**.

Evidence rules we ended up with:
- "Absorbed" must be proven by either `git merge-base --is-ancestor <commit> <target>` (exit 0), or a behavior test; never by a similar name, title, or the existence of a function with a similar purpose.
- A ledger of local patches must be reconciled entry by entry against the target, including entries previously marked dropped. In one ledger the baseline hash was wrong (it labelled one commit as the release; `git rev-parse <tag>` showed a different commit), so the range included an upstream commit.
- A patch that is "dead because upstream rewrote the area" must still be checked: when upstream restructured, three of the knowledge-base guard functions had to be redone on new code paths rather than rebased.

### Results: recorded rounds of data

| Round | Date | Fork | Local commits | Result of the triage |
|---|---|---|---|---|
| A | 2026-08-29 | Knowledge base, previous upgrade (695+ upstream commits, 4 minors, 22 migrations) | 17 | KEEP 3, DROP 9 (7 already implemented upstream and re-verified, 1 dormant switch, 1 replaced by an upstream mechanism), REWRITE 5 |
| B | 2026-09-28 | Knowledge base, this book's upgrade (12 minors, 21 migrations) | 14 | 1 skip (ancestor of target), 2 obsolete (a stale test, a one-character brace fix), 2 ledger/doc items to rewrite, 9 to keep or port (the last two being the file-mirror guard commits, which had to be re-implemented semantically on new paths); ported as three reviewable groups |
| C | 2026-07-27 | Agent runtime, against a July release | 43 commits + 1 uncommitted file | First pass: retire 3, keep 5, reimplement 30, uncertain 6. Second pass merged the 30 "reimplement" into 4 work items. Final: 3 retired whole plus ~6 split-out parts, 4 committed keeps plus 1 uncommitted, 4 decided at execution time |
| D | 2026-09-04 | Agent runtime, rebased onto a release tag dated 2026-08-31 | 39 (37 non-merge + 2 merges); 59 files, +4,713 / -172 | Upstream changed 8,638 files in the window, 40 overlapping ours. The "keep 5" figure from the July summary was stale: every fork-only symbol (anti-loop state, persona domain, silence filter, outbound gate hooks, audit hooks) had zero hits in the new tag, so none could be retired |
| E | 2026-09-18 | Agent runtime, next tag 10,722 commits later | 41 rows (40 rehearsed + 1 backport already in target) | Rehearsal cherry-pick: 5 clean, 35 conflicts, 1 n/a. Verdicts: 31 keep, 3 partial, 4 upstream-has-it, 3 re-evaluate. The pre-planning guess "most persona-routing and task-board patches can be dropped" was refuted: upstream had zero equivalents. Round E stopped after triage (not finished at time of writing) |

Round B in detail (knowledge base, 14 commits):

| Verdict | Count | Examples (generic) |
|---|---|---|
| Skip, already an ancestor of the target | 1 | A takes-proposal exclusion |
| Keep, port with conflict resolution | 7 | A local confidential-attachment store (four commits: the store itself, audio extensions, WebP magic bytes, and source-id validation plus rollback of only the rows it created), an extract `--exclude` flag, a lint-exclude environment variable, a phantom-limit-zero fix |
| Keep, semantic port required | 2 | File-mirror guard with soft-delete audit and rename protection plus DB-only facts and a phase allowlist; the follow-up "re-read inside the lock, distinguish real empty result from fallback" |
| Obsolete / cleanup only | 2 | Delete a test whose subject is gone; a one-character missing brace from an earlier merge |
| Ledger or documentation | 2 | Ledger v2; review verdicts |

### What did not work (numbers)
- Pre-planning estimates: "keep 5" (reality: 39 commits, none retirable); "most persona and task-board patches droppable" (reality: 31 of 41 keep, 3 partial); "about 22k pages, 50k+ chunks" for the database (reality: 23,698 pages and 135,607 chunks, 2.7x on chunks).
- Two "upstream has it" conclusions in round A were wrong at behavior level: a federation commit judged "upstream is more complete" would have cut a reader's visibility from 15,224 pages to 21 (a 99.86% loss) because upstream's tier explicitly does not federate; a keyword-role switch was equated with an upstream fallback that behaves differently. The same error occurred once more: a commit kept "by title" turned out, on reading the diff, to be auto-appended topic links, not code.
- A cost estimate for the "do it properly" path was overestimated because nobody measured it (round A, federation): the supposed blocker did not exist.

### Pitfalls

| Symptom | Root cause | Fix |
|---|---|---|
| Plan built on "keep 5" | A summary from an older rebase was reused | Re-measure the delta (commit count, fork-only symbols in the new tag) before planning |
| Verdict "absorbed" for an upstream guard with the same name | Same name, different semantics (upstream's guard checks revision identity, ours checks that existing content is not reduced) | Test behavior; an ancestor check is the only name-free proof |
| Rebase onto a "main tree" that runs production | Worktree created inside the live runtime tree or inside the wrong repository | `git worktree list` first; never switch branches in the tree production loads code from |

### The rule
Decide each fork patch from evidence of behavior in the target (ancestor check or test), measure the delta before planning, and never carry a plan's estimate across rounds.

See [offline reproduction](offline-reproduction.md) for the public adaptations and validation boundary.
