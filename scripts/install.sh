#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
codex_home="${CODEX_HOME:-$HOME/.codex}"
skills_root="${SKILLS_ROOT:-$codex_home/skills}"
expected_bundle=""
expected_commit=""
policy_authority_source=""
policy_authority_reference=""
publication_campaign_id=""
publication_node_id=""
publication_authority_epoch=""
publication_cancellation_epoch=""
archive_legacy_state=0
legacy_state_root=""
install_policy=0
universal_bundle_id="campaign-engine-policy-v1"
refresh=0
legacy_overlap_migration=0
archive_mode=0
dry_run=0

usage() {
  cat <<'USAGE'
Usage: ./scripts/install.sh --expected-bundle-sha256 HASH --expected-source-commit COMMIT [options]

Options:
  --skills-root PATH
  --codex-home PATH
  --install-universal-policy
  --universal-bundle-id IDENTIFIER
  --refresh-capability-index
  --policy-authority-source explicit-user-approval|campaign-publication-authority
  --policy-authority-reference TEXT
  --publication-campaign-id IDENTIFIER
  --publication-node-id IDENTIFIER
  --publication-authority-epoch INTEGER
  --publication-cancellation-epoch INTEGER
  --archive-legacy-state
  --legacy-state-root PATH
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
    --install-universal-policy) install_policy=1; shift ;;
    --universal-bundle-id) universal_bundle_id="$2"; shift 2 ;;
    --refresh-capability-index) refresh=1; shift ;;
    --policy-authority-source) policy_authority_source="$2"; shift 2 ;;
    --policy-authority-reference) policy_authority_reference="$2"; shift 2 ;;
    --publication-campaign-id) publication_campaign_id="$2"; shift 2 ;;
    --publication-node-id) publication_node_id="$2"; shift 2 ;;
    --publication-authority-epoch) publication_authority_epoch="$2"; shift 2 ;;
    --publication-cancellation-epoch) publication_cancellation_epoch="$2"; shift 2 ;;
    --archive-legacy-state) archive_legacy_state=1; shift ;;
    --legacy-state-root) legacy_state_root="$2"; shift 2 ;;
    --legacy-overlap-migration) legacy_overlap_migration=1; shift ;;
    --archive-mode) archive_mode=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$expected_bundle" ]] || { echo "--expected-bundle-sha256 is required" >&2; exit 2; }
[[ -n "$expected_commit" ]] || { echo "--expected-source-commit is required" >&2; exit 2; }
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
args+=(--expected-source-commit "$expected_commit")
[[ "$install_policy" -eq 0 ]] || args+=(--install-universal-policy)
[[ "$refresh" -eq 0 ]] || args+=(--refresh-capability-index)
[[ -z "$policy_authority_source" ]] || args+=(--policy-authority-source "$policy_authority_source")
[[ -z "$policy_authority_reference" ]] || args+=(--policy-authority-reference "$policy_authority_reference")
[[ -z "$publication_campaign_id" ]] || args+=(--publication-campaign-id "$publication_campaign_id")
[[ -z "$publication_node_id" ]] || args+=(--publication-node-id "$publication_node_id")
[[ -z "$publication_authority_epoch" ]] || args+=(--publication-authority-epoch "$publication_authority_epoch")
[[ -z "$publication_cancellation_epoch" ]] || args+=(--publication-cancellation-epoch "$publication_cancellation_epoch")
[[ "$archive_legacy_state" -eq 0 ]] || args+=(--archive-legacy-state)
[[ -z "$legacy_state_root" ]] || args+=(--legacy-state-root "$legacy_state_root")
[[ "$archive_mode" -eq 0 ]] || args+=(--archive-mode)
[[ "$legacy_overlap_migration" -eq 0 ]] || args+=(--legacy-overlap-migration)
[[ "$dry_run" -eq 0 ]] || args+=(--dry-run)
exec "$python_cmd" "${args[@]}"
