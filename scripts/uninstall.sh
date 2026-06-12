#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=0
SKILLS_ROOT="${SKILLS_ROOT:-$HOME/.agents/skills}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

usage() {
  cat <<'USAGE'
Usage: ./scripts/uninstall.sh [options]

Options:
  --skills-root PATH          Skill install path, default $HOME/.agents/skills
  --codex-home PATH           Codex home path, default $HOME/.codex
  --dry-run                   Print actions without changing files
  -h, --help                  Show help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
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
manifest_skills_root=""
manifest_agents_path=""
manifest_skill_paths=()

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf 'DRY RUN:'
    printf ' %q' "$@"
    printf '\n'
  else
    "$@"
  fi
}

abs_path() {
  local path="$1"
  if [[ -e "$path" ]]; then
    (cd "$(dirname "$path")" && printf '%s/%s\n' "$(pwd -P)" "$(basename "$path")")
  else
    (cd "$(dirname "$path")" 2>/dev/null && printf '%s/%s\n' "$(pwd -P)" "$(basename "$path")") || printf '%s\n' "$path"
  fi
}

ensure_under_root() {
  local target
  local root
  target="$(abs_path "$1")"
  root="$(abs_path "$2")"
  case "$target" in
    "$root"|"$root"/*) ;;
    *)
      echo "Refusing to remove path outside root. Target=$target Root=$root" >&2
      exit 1
      ;;
  esac
}

remove_if_present() {
  local target="$1"
  local label="$2"
  if [[ -e "$target" ]]; then
    run rm -rf "$target"
    echo "Removed $label: $target"
  fi
}

if [[ -f "$manifest_txt" ]]; then
  while IFS= read -r line; do
    case "$line" in
      SkillsRoot=*)
        manifest_skills_root="${line#SkillsRoot=}"
        ;;
      GlobalAgentsPath=*)
        manifest_agents_path="${line#GlobalAgentsPath=}"
        ;;
      SkillPath=*)
        manifest_skill_paths+=("${line#SkillPath=}")
        ;;
    esac
  done < "$manifest_txt"
fi

if [[ -n "$manifest_skills_root" ]]; then
  if [[ "$SKILLS_ROOT" != "$manifest_skills_root" ]]; then
    echo "Using SkillsRoot recorded by the install manifest: $manifest_skills_root"
  fi
  SKILLS_ROOT="$manifest_skills_root"
fi

if [[ "${#manifest_skill_paths[@]}" -gt 0 ]]; then
  for target in "${manifest_skill_paths[@]}"; do
    ensure_under_root "$target" "$SKILLS_ROOT"
    remove_if_present "$target" "skill"
  done
elif [[ -d "$source_skills" ]]; then
  for skill_dir in "$source_skills"/*; do
    [[ -d "$skill_dir" ]] || continue
    skill_name="$(basename "$skill_dir")"
    target="$SKILLS_ROOT/$skill_name"
    ensure_under_root "$target" "$SKILLS_ROOT"
    remove_if_present "$target" "skill $skill_name"
  done
fi

global_agents="${manifest_agents_path:-$CODEX_HOME/AGENTS.md}"
if [[ -e "$global_agents" ]] && grep -q "# BEGIN CODEX CODING OS STARTER" "$global_agents"; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY RUN: would remove global AGENTS block from $global_agents"
  else
    cp "$global_agents" "$global_agents.bak.$timestamp"
    perl -0pi -e 's/\n?# BEGIN CODEX CODING OS STARTER.*?# END CODEX CODING OS STARTER\n?//s' "$global_agents"
    echo "Removed global AGENTS block. Backup: $global_agents.bak.$timestamp"
  fi
fi

ensure_under_root "$support_root" "$CODEX_HOME"
remove_if_present "$support_root" "installed support files"

echo "Uninstall complete."
