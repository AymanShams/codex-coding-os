#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
skills_root="${SKILLS_ROOT:-$HOME/.agents/skills}"
codex_home="${CODEX_HOME:-$HOME/.codex}"
dry_run=0

usage() {
  cat <<'USAGE'
Usage: ./scripts/uninstall.sh [--skills-root PATH] [--codex-home PATH] [--dry-run]
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skills-root) skills_root="$2"; shift 2 ;;
    --codex-home) codex_home="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -n "${PYTHON:-}" ]]; then
  python_cmd="$PYTHON"
else
  command -v python3 >/dev/null 2>&1 && python_cmd=python3 || python_cmd=python
fi
command -v "$python_cmd" >/dev/null 2>&1 || { echo "Python 3 is required" >&2; exit 2; }
args=(-B "$repo_root/scripts/install_transaction.py" --json uninstall --skills-root "$skills_root" --codex-home "$codex_home")
[[ "$dry_run" -eq 0 ]] || args+=(--dry-run)
exec "$python_cmd" "${args[@]}"
