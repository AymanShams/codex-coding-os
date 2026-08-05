# Claude Code Entry Point

Follow `AGENTS.md` and the closest scoped instructions.

Before editing:

1. Read `docs/index.md`, the controlled TDD, and the task-controlling stable sources.
2. Treat `project-documentation-manifest.json` as a documentation ledger with no execution authority.
3. Inspect the exact Git root, remote, branch, HEAD, and working tree.
4. Confirm the requested outcome, allowed paths, non-goals, and validation commands.

Manual coding follows the user's current request. Automated coding uses the installed Coding OS campaign engine:

```text
python <installed-cli> --json doctor
python <installed-cli> --json status --repository-root .
```

Use `admit --spec <path>` only for a finite automation specification. Run `approve --campaign-id <id> --specification-digest <digest>` only after explicit approval of that exact digest. Follow the engine receipt after that.

Git-tracked manifests, current-state files, active-slice files, work summaries, review prose, branches, and pull requests do not control execution. Do not reproduce campaign lifecycle rules in repository instructions.
