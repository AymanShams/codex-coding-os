#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
codex_home="${CODEX_HOME:-$HOME/.codex}"
skills_root="${SKILLS_ROOT:-}"
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
remove_policy=0
universal_bundle_id="campaign-engine-policy-v1"
refresh=0
legacy_overlap_migration=0
archive_mode=0
dry_run=0

usage() {
  cat <<'USAGE'
Usage: ./scripts/install.sh --expected-bundle-sha256 HASH --expected-source-commit COMMIT [options]

Options:
  --skills-root PATH  Must equal the canonical Codex home skills directory
  --codex-home PATH  Must equal the OS account profile's .codex directory
  --install-universal-policy
  --remove-universal-policy
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
    --remove-universal-policy) remove_policy=1; shift ;;
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

[[ -n "$skills_root" ]] || skills_root="$codex_home/skills"
[[ -n "$expected_bundle" ]] || { echo "--expected-bundle-sha256 is required" >&2; exit 2; }
[[ -n "$expected_commit" ]] || { echo "--expected-source-commit is required" >&2; exit 2; }
if [[ "$install_policy" -eq 1 && "$remove_policy" -eq 1 ]]; then
  echo "--install-universal-policy and --remove-universal-policy are mutually exclusive" >&2
  exit 2
fi
if [[ -n "${PYTHON:-}" ]]; then
  python_cmd="$PYTHON"
else
  command -v python3 >/dev/null 2>&1 && python_cmd=python3 || python_cmd=python
fi
command -v "$python_cmd" >/dev/null 2>&1 || { echo "Python 3 is required" >&2; exit 2; }
if ! canonical_codex_home="$("$python_cmd" -B -c '
import os
import pwd

try:
    profile = pwd.getpwuid(os.getuid()).pw_dir
except (KeyError, OSError, AttributeError):
    raise SystemExit(1)
if not profile:
    raise SystemExit(1)
print(os.path.normpath(os.path.abspath(os.path.join(profile, ".codex"))))
')"; then
  echo "The operating-system account profile is unavailable" >&2
  exit 2
fi
requested_codex_home="$("$python_cmd" -B -c '
import os
import sys

print(os.path.normpath(os.path.abspath(os.path.expanduser(sys.argv[1]))))
' "$codex_home")"
if [[ "$requested_codex_home" != "$canonical_codex_home" ]]; then
  echo "--codex-home must equal the canonical operating-system account-profile path: $canonical_codex_home" >&2
  exit 2
fi
canonical_skills_root="$canonical_codex_home/skills"
requested_skills_root="$("$python_cmd" -B -c '
import os
import sys

print(os.path.normpath(os.path.abspath(os.path.expanduser(sys.argv[1]))))
' "$skills_root")"
if [[ "$requested_skills_root" != "$canonical_skills_root" ]]; then
  echo "--skills-root must equal the canonical Codex home skills path: $canonical_skills_root" >&2
  exit 2
fi
args=(
  -B "$repo_root/scripts/install_transaction.py" --json install
  --source-root "$repo_root" --skills-root "$skills_root" --codex-home "$codex_home"
  --expected-bundle-sha256 "$expected_bundle" --universal-bundle-id "$universal_bundle_id"
)
args+=(--expected-source-commit "$expected_commit")
[[ "$install_policy" -eq 0 ]] || args+=(--install-universal-policy)
[[ "$remove_policy" -eq 0 ]] || args+=(--remove-universal-policy)
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
