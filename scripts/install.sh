#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
skills_root="${SKILLS_ROOT:-$HOME/.agents/skills}"
codex_home="${CODEX_HOME:-$HOME/.codex}"
expected_bundle=""
expected_commit=""
authority_case_id=""
authority_source=""
authority_reference=""
case_engine="$repo_root/scripts/agent/case_state.py"
case_state_root=""
install_policy=0
universal_bundle_id="automation-preserving-case-state-recovery-v1"
refresh=0
legacy_overlap_migration=0
archive_mode=0
dry_run=0

usage() {
  cat <<'USAGE'
Usage: ./scripts/install.sh --expected-bundle-sha256 HASH [options]

Options:
  --skills-root PATH
  --codex-home PATH
  --expected-source-commit COMMIT
  --install-universal-policy
  --universal-bundle-id IDENTIFIER
  --refresh-capability-index
  --authority-case-id UUID
  --authority-source preauthorized-run-envelope|explicit-user-approval
  --authority-reference TEXT
  --case-state-engine PATH
  --case-state-root PATH
  --legacy-overlap-migration
  --archive-mode
  --dry-run
  -h, --help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skills-root) skills_root="$2"; shift 2 ;;
    --codex-home) codex_home="$2"; shift 2 ;;
    --expected-bundle-sha256) expected_bundle="$2"; shift 2 ;;
    --expected-source-commit) expected_commit="$2"; shift 2 ;;
    --install-universal-policy|--install-global-agents) install_policy=1; shift ;;
    --universal-bundle-id) universal_bundle_id="$2"; shift 2 ;;
    --refresh-capability-index) refresh=1; shift ;;
    --authority-case-id) authority_case_id="$2"; shift 2 ;;
    --authority-source) authority_source="$2"; shift 2 ;;
    --authority-reference) authority_reference="$2"; shift 2 ;;
    --case-state-engine) case_engine="$2"; shift 2 ;;
    --case-state-root) case_state_root="$2"; shift 2 ;;
    --legacy-overlap-migration) legacy_overlap_migration=1; shift ;;
    --archive-mode) archive_mode=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$expected_bundle" ]] || { echo "--expected-bundle-sha256 is required" >&2; exit 2; }
if [[ -n "${PYTHON:-}" ]]; then
  python_cmd="$PYTHON"
else
  command -v python3 >/dev/null 2>&1 && python_cmd=python3 || python_cmd=python
fi
command -v "$python_cmd" >/dev/null 2>&1 || { echo "Python 3 is required" >&2; exit 2; }
args=(
  -B "$repo_root/scripts/install_transaction.py" --json install
  --source-root "$repo_root" --skills-root "$skills_root" --codex-home "$codex_home"
  --expected-bundle-sha256 "$expected_bundle" --universal-bundle-id "$universal_bundle_id"
)
[[ -z "$expected_commit" ]] || args+=(--expected-source-commit "$expected_commit")
[[ "$install_policy" -eq 0 ]] || args+=(--install-universal-policy)
[[ "$refresh" -eq 0 ]] || args+=(--refresh-capability-index)
[[ -z "$authority_case_id" ]] || args+=(--authority-case-id "$authority_case_id")
[[ -z "$authority_source" ]] || args+=(--authority-source "$authority_source")
[[ -z "$authority_reference" ]] || args+=(--authority-reference "$authority_reference")
if [[ "$install_policy" -eq 1 ]]; then
  [[ -n "$case_state_root" ]] || case_state_root="$codex_home/case-state"
  args+=(--case-state-engine "$case_engine" --case-state-root "$case_state_root")
fi
[[ "$archive_mode" -eq 0 ]] || args+=(--archive-mode)
[[ "$legacy_overlap_migration" -eq 0 ]] || args+=(--legacy-overlap-migration)
[[ "$dry_run" -eq 0 ]] || args+=(--dry-run)
exec "$python_cmd" "${args[@]}"
