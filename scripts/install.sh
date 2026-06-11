#!/usr/bin/env bash
set -euo pipefail

INSTALL_GLOBAL_AGENTS=0
DRY_RUN=0
SKILLS_ROOT="${SKILLS_ROOT:-$HOME/.agents/skills}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

usage() {
  cat <<'USAGE'
Usage: ./scripts/install.sh [options]

Options:
  --install-global-agents     Add the Coding OS block to $CODEX_HOME/AGENTS.md
  --skills-root PATH          Skill install path, default $HOME/.agents/skills
  --codex-home PATH           Codex home path, default $HOME/.codex
  --dry-run                   Print actions without changing files
  -h, --help                  Show help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-global-agents)
      INSTALL_GLOBAL_AGENTS=1
      shift
      ;;
    --skills-root)
      SKILLS_ROOT="$2"
      shift 2
      ;;
    --codex-home)
      CODEX_HOME="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_skills="$repo_root/.agents/skills"
support_root="$CODEX_HOME/coding-os-starter"
manifest_txt="$support_root/install-manifest.txt"
timestamp="$(date +%Y%m%d-%H%M%S)"

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf 'DRY RUN:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

copy_dir_clean() {
  local source="$1"
  local target="$2"
  local label="$3"

  if [[ -e "$target" ]]; then
    run cp -R "$target" "$target.backup-$timestamp"
    run rm -rf "$target"
  fi
  run mkdir -p "$(dirname "$target")"
  run cp -R "$source" "$target"
  echo "Installed $label: $target"
}

copy_file_clean() {
  local source="$1"
  local target="$2"
  local label="$3"

  if [[ -e "$target" ]]; then
    run cp "$target" "$target.backup-$timestamp"
    run rm -f "$target"
  fi
  run mkdir -p "$(dirname "$target")"
  run cp "$source" "$target"
  echo "Installed $label: $target"
}

if [[ ! -d "$source_skills" ]]; then
  echo "Cannot find source skills folder: $source_skills" >&2
  exit 1
fi

run mkdir -p "$SKILLS_ROOT" "$CODEX_HOME" "$support_root"

installed_skills=()
for skill_dir in "$source_skills"/*; do
  [[ -d "$skill_dir" ]] || continue
  skill_name="$(basename "$skill_dir")"
  target="$SKILLS_ROOT/$skill_name"
  copy_dir_clean "$skill_dir" "$target" "skill $skill_name"
  installed_skills+=("$target")
done

support_items=(
  templates
  docs
  codex-capabilities
  external-skills
  hooks
  patches
  scripts
  .codex
  .github
  tests
  pack.manifest.json
  THIRD_PARTY_SKILLS.md
  README.md
  CHANGELOG.md
  LICENSE.md
  COMMERCIAL.md
  NOTICE.md
)

for item in "${support_items[@]}"; do
  source="$repo_root/$item"
  target="$support_root/$item"
  [[ -e "$source" ]] || continue
  if [[ -d "$source" ]]; then
    copy_dir_clean "$source" "$target" "support item $item"
  else
    copy_file_clean "$source" "$target" "support item $item"
  fi
done

if [[ "$INSTALL_GLOBAL_AGENTS" -eq 1 ]]; then
  global_agents="$CODEX_HOME/AGENTS.md"
  start_marker="# BEGIN CODEX CODING OS STARTER"
  end_marker="# END CODEX CODING OS STARTER"

  echo "Global AGENTS.md update requested."
  echo "Target: $global_agents"

  if [[ "$DRY_RUN" -eq 0 ]]; then
    mkdir -p "$(dirname "$global_agents")"
    if [[ -e "$global_agents" ]]; then
      cp "$global_agents" "$global_agents.backup-$timestamp"
      perl -0pi -e 's/\n?# BEGIN CODEX CODING OS STARTER.*?# END CODEX CODING OS STARTER\n?//s' "$global_agents"
    fi
    {
      printf '\n%s\n' "$start_marker"
      cat "$repo_root/AGENTS.md"
      printf '\n%s\n' "$end_marker"
    } >> "$global_agents"
  else
    echo "DRY RUN: would update $global_agents"
  fi
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  {
    echo "Codex Coding OS Starter installed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "RepoRoot=$repo_root"
    echo "SkillsRoot=$SKILLS_ROOT"
    echo "CodexHome=$CODEX_HOME"
    echo "SupportRoot=$support_root"
    echo "InstalledGlobalAgents=$INSTALL_GLOBAL_AGENTS"
    echo "Skills:"
    printf '%s\n' "${installed_skills[@]}"
  } > "$manifest_txt"
fi

echo "Support files copied to: $support_root"
echo "Restart Codex, then use templates/first-codex-prompt.md from this repo or $support_root."
