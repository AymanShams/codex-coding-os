# Hooks

`campaign-engine/campaign_hook.py` is the only lifecycle-aware hook. It is a
thin client of the installed campaign engine and contains no transition table,
budget reset, actor-role inference, Git-state mirror gate, or fallback path.

The installer owns one narrow `PreToolUse` entry matching only
`campaign_apply_patch` and `campaign_commit`. It transactionally removes the
retired anti-loop runtime entries and preserves every unrelated hook. Uninstall
removes the owned campaign entry without restoring the retired engine.

Manual work and incomplete inherited environments are unaffected. Delegation
requires `CCOS_CAMPAIGN_ID`, `CCOS_ACTOR_ID`, `CCOS_LEASE_ID`, all three exact
epochs, `CCOS_REPOSITORY_ROOT`, and `CCOS_HOOK_ACTION`. If any value is absent,
the hook returns success without consulting state. With the complete tuple, it
delegates the exact decision to `campaign_engine/cli.py authorize-action`.

Pack validation and capability-routing hooks remain ordinary advisory or build
tools. They do not own campaign state.
