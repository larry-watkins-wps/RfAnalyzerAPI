# Repo-tracked Claude memory

This directory mirrors selected project-scoped Claude memory files so they travel with the repo and reach collaborators' Claude sessions, not just the original author's account.

## How Claude actually loads memory

Claude Code loads memory automatically from a per-user, per-project location, **not** from this directory. On the original author's machine the auto-loaded path is:

```
~/.claude/projects/<repo-id>/memory/
```

Files here are mirrors. They are read by humans browsing the repo, and a collaborator who wants the same auto-load behavior can copy them into their own per-user memory location.

## What lives here

| File | Purpose |
|---|---|
| [`feedback_spec_sync.md`](feedback_spec_sync.md) | Cross-artifact sync rule for spec changes — same rule duplicated in [`/CLAUDE.md`](../../CLAUDE.md) and [`/README.md`](../../README.md). |

Personal memory (e.g., user role, preferences) is intentionally **not** mirrored — it stays in the per-user account location.

## Keeping the mirror current

When the project-scoped feedback or reference memory changes in the per-user location, copy the updated file here in the same commit. Drift between the two copies defeats the purpose. The cross-artifact-sync rule in [`feedback_spec_sync.md`](feedback_spec_sync.md) applies to memory sync as well.
