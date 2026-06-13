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

global_agents="$codex_home/AGENTS.md"
mkdir -p "$codex_home"
printf '# BEGIN CODEX CODING OS STARTER\nlegacy global block\n# END CODEX CODING OS STARTER\n' > "$global_agents"

bash "$install_script" \
  --skills-root "$skills_root" \
  --codex-home "$codex_home" \
  --install-global-agents

manifest_path="$codex_home/coding-os/install-manifest.txt"
master_skill="$skills_root/codex-coding-os-master/SKILL.md"

[[ -f "$manifest_path" ]] || { echo "Install manifest was not written." >&2; exit 1; }
grep -q '^ManifestVersion=2$' "$manifest_path" || { echo "Portable install manifest version was not written." >&2; exit 1; }
[[ -f "$master_skill" ]] || { echo "Master skill was not installed." >&2; exit 1; }
[[ -f "$codex_home/coding-os/docs/getting-started.md" ]] || { echo "Getting-started guide was not installed." >&2; exit 1; }
[[ -f "$codex_home/coding-os/CHANGELOG.md" ]] || { echo "Changelog was not installed." >&2; exit 1; }
grep -q "# BEGIN CODEX CODING OS" "$global_agents" || { echo "Global AGENTS block was not installed." >&2; exit 1; }
! grep -Eq "CODEX CODING OS STARTER|legacy global block" "$global_agents" || { echo "Legacy global AGENTS block was not fully replaced." >&2; exit 1; }

stale_file="$skills_root/ai-coding-discipline/STALE.txt"
printf 'stale file from previous install\n' > "$stale_file"
obsolete_skill="$skills_root/obsolete-pack-skill"
mkdir -p "$obsolete_skill"
printf 'obsolete skill from previous package version\n' > "$obsolete_skill/SKILL.md"
printf 'SkillPath=%s\n' "$obsolete_skill" >> "$manifest_path"

bash "$install_script" \
  --skills-root "$skills_root" \
  --codex-home "$codex_home"

[[ ! -e "$stale_file" ]] || { echo "Reinstall left a stale file in an existing skill folder." >&2; exit 1; }
[[ ! -e "$obsolete_skill" ]] || { echo "Upgrade left a skill recorded by the previous package version." >&2; exit 1; }

manifest_only_skill="$skills_root/manifest-only-skill"
mkdir -p "$manifest_only_skill"
printf 'manifest-only uninstall target\n' > "$manifest_only_skill/SKILL.md"
printf 'SkillPath=%s\n' "$manifest_only_skill" >> "$manifest_path"
printf '# BEGIN CODEX CODING OS STARTER\nlegacy global block before uninstall\n# END CODEX CODING OS STARTER\n' > "$global_agents"

bash "$uninstall_script" \
  --skills-root "$skills_root" \
  --codex-home "$codex_home"

[[ ! -e "$master_skill" ]] || { echo "Uninstall did not remove installed skills." >&2; exit 1; }
[[ ! -e "$manifest_only_skill" ]] || { echo "Uninstall did not consume the recorded installed-state manifest." >&2; exit 1; }
[[ ! -e "$codex_home/coding-os" ]] || { echo "Uninstall did not remove support files." >&2; exit 1; }

if [[ -f "$global_agents" ]] && grep -Eq "# BEGIN CODEX CODING OS|CODEX CODING OS STARTER|legacy global block" "$global_agents"; then
  echo "Uninstall did not remove the global AGENTS block." >&2
  exit 1
fi

echo "Bash install/uninstall smoke test passed."
