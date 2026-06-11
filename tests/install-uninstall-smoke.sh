#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/codex-coding-os-smoke.XXXXXX")"
skills_root="$test_root/skills"
codex_home="$test_root/codex-home"
install_script="$repo_root/scripts/install.sh"
uninstall_script="$repo_root/scripts/uninstall.sh"

cleanup() {
  rm -rf "$test_root"
}
trap cleanup EXIT

bash "$install_script" \
  --skills-root "$skills_root" \
  --codex-home "$codex_home" \
  --install-global-agents

manifest_path="$codex_home/coding-os-starter/install-manifest.txt"
master_skill="$skills_root/codex-coding-os-master/SKILL.md"
global_agents="$codex_home/AGENTS.md"

[[ -f "$manifest_path" ]] || { echo "Install manifest was not written." >&2; exit 1; }
[[ -f "$master_skill" ]] || { echo "Master skill was not installed." >&2; exit 1; }
[[ -f "$codex_home/coding-os-starter/docs/getting-started.md" ]] || { echo "Getting-started guide was not installed." >&2; exit 1; }
[[ -f "$codex_home/coding-os-starter/CHANGELOG.md" ]] || { echo "Changelog was not installed." >&2; exit 1; }
grep -q "# BEGIN CODEX CODING OS STARTER" "$global_agents" || { echo "Global AGENTS block was not installed." >&2; exit 1; }

stale_file="$skills_root/ai-coding-discipline/STALE.txt"
printf 'stale file from previous install\n' > "$stale_file"

bash "$install_script" \
  --skills-root "$skills_root" \
  --codex-home "$codex_home"

[[ ! -e "$stale_file" ]] || { echo "Reinstall left a stale file in an existing skill folder." >&2; exit 1; }

bash "$uninstall_script" \
  --skills-root "$skills_root" \
  --codex-home "$codex_home"

[[ ! -e "$master_skill" ]] || { echo "Uninstall did not remove installed skills." >&2; exit 1; }
[[ ! -e "$codex_home/coding-os-starter" ]] || { echo "Uninstall did not remove support files." >&2; exit 1; }

if [[ -f "$global_agents" ]] && grep -q "# BEGIN CODEX CODING OS STARTER" "$global_agents"; then
  echo "Uninstall did not remove the global AGENTS block." >&2
  exit 1
fi

echo "Bash install/uninstall smoke test passed."
