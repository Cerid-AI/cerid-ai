# Git Workflow — Feature Branches, Rebasing, Conflict Resolution

A clean Git history reflects how a team thinks, not just what it built. The practices below trade slightly more discipline at commit time for sharply easier code review, bisecting, and rollback.

## Feature branches

The fundamental unit of work is a short-lived branch, named for its intent (`feature/customer-export`, `bugfix/login-redirect`, `chore/upgrade-django-5`). Branches stay open hours to days, not weeks. Long-lived branches accumulate merge debt — the longer they live, the more painful the eventual reintegration.

The trunk (`main` or `master`) stays releasable at all times. Never push directly; everything lands via pull request. Branch protection rules in GitHub/GitLab enforce this: required reviews, passing CI, no force-push to main.

## Rebasing vs merging

Two valid patterns exist:

**Merge commits** preserve the historical fact that two branches existed and were combined. Useful for tracking when a feature shipped. The downside is non-linear history, which can be hard to read with `git log --oneline`.

**Rebase before merge** rewrites your branch's commits to sit atop the current main, producing linear history. Run `git fetch origin && git rebase origin/main` from your branch. Resolve any conflicts as they appear, `git add` the resolutions, and `git rebase --continue`. Force-push (`--force-with-lease`, never plain `--force`) updates your remote branch.

Most teams converge on "rebase your feature branch onto main, then merge it as a fast-forward (or squash)". Squash merges produce one commit per PR — clean main history, but loses the granular history within the feature branch.

Important: never rebase a branch that someone else is working on. Rewriting shared history forces collaborators to do dangerous recovery.

## Conflict resolution

When `git merge` or `git rebase` reports a conflict, the affected files contain markers:

```
<<<<<<< HEAD
your version
=======
their version
>>>>>>> main
```

Resolve by editing the file to its correct final state and removing all markers. Tools like `git mergetool` or your editor's diff view help with non-trivial conflicts.

Strategies for harder cases:
- **`git rerere`** ("reuse recorded resolution") records how you resolved a conflict and replays it on subsequent rebases. Lifesaver for long rebase chains.
- **`git diff --base`** shows the original common ancestor — sometimes the conflict is obvious only when you see what both sides changed *from*.
- **Cherry-pick a single commit** to test a resolution in isolation: `git cherry-pick <sha>`.

When conflicts overwhelm a rebase, abort (`git rebase --abort`), break the work into smaller PRs against current main, and never let yourself rebase a stale branch over a moved-target main again.

## Commit hygiene

Each commit should represent one logical change with a meaningful message. The conventional-commit style (`feat:`, `fix:`, `chore:`, `docs:`) helps tools generate changelogs and helps humans skim history. The first line is a 50-character summary; an optional body explains the why, not the what.

Atomic commits make `git bisect` work — when a regression appears, bisect can identify the exact commit that introduced it in O(log N) tests, but only if each commit compiles and the tests run.
