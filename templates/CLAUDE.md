# Coding OS Campaign Client

Read `AGENTS.md` and the repository's stable product sources.

For automated work, use only the installed campaign executable:

```powershell
python -B "$env:USERPROFILE\.codex\coding-os\scripts\agent\campaign_engine\cli.py" --json status --repository-root .
```

The external SQLite campaign store is the sole lifecycle authority. Bind every
write to the exact campaign, node, actor lease, fencing epoch, authority epoch,
cancellation epoch, repository identity, worktree, base SHA, and allowed paths.

Current-state files, active-slice manifests, handoffs, comments, review markers,
caller-declared roles, branches, and pull requests are informational only. They
cannot approve, stop, review, repair, or publish work.

Manual coding follows the current user request, stable product sources, exact Git
evidence, and repository validation. Do not invoke retired case commands.
