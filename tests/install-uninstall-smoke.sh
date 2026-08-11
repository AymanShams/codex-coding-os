#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/ccos-transaction-smoke.XXXXXX")"
profile_root="$test_root/profile"
codex_home="$profile_root/.codex"
skills_root="$codex_home/skills"
if [[ -n "${PYTHON:-}" ]]; then
  python_cmd="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
  python_cmd=python3
else
  python_cmd=python
fi
bundle_hash="$("$python_cmd" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["aggregate_sha256"])' "$repo_root/install-bundle.manifest.json")"
source_commit="$(git -C "$repo_root" rev-parse HEAD)"
repository_security_skills=(
  defensive-security-checklist
  postgres-security-best-practices
  security-best-practices
  security-ownership-map
  security-threat-model
)
codex_managed_plugin_skill_directories=(
  attack-path-analysis
  deep-security-scan
  define-security-policy
  finding-discovery
  fix-finding
  propose-security-hardening
  security-diff-scan
  security-scan
  threat-model
  track-findings
  triage-finding
  validation
  vulnerability-writeup
  supabase
  supabase-postgres-best-practices
  neon-postgres
  neon-postgres-egress-optimizer
)

cleanup() { rm -rf "$test_root"; }
trap cleanup EXIT

install_synthetic_runtime() {
  "$python_cmd" -B "$repo_root/scripts/install_transaction.py" --json install \
    --source-root "$repo_root" \
    --skills-root "$skills_root" \
    --codex-home "$codex_home" \
    --expected-bundle-sha256 "$bundle_hash" \
    --expected-source-commit "$source_commit" \
    --archive-mode
}

uninstall_synthetic_runtime() {
  "$python_cmd" -B "$repo_root/scripts/install_transaction.py" --json uninstall \
    --skills-root "$skills_root" \
    --codex-home "$codex_home"
}

mkdir -p "$skills_root/unmanaged" "$codex_home/case-state" "$codex_home/plugins"
printf 'preserved-config' > "$codex_home/config.toml"
printf 'preserved-case' > "$codex_home/case-state/case.json"
printf 'preserved-plugin' > "$codex_home/plugins/plugin.txt"
printf 'preserved-skill' > "$skills_root/unmanaged/SKILL.md"
preserved_before="$(sha256sum "$codex_home/config.toml" "$codex_home/case-state/case.json" "$codex_home/plugins/plugin.txt" "$skills_root/unmanaged/SKILL.md")"

rejection_output="$test_root/noncanonical-install.txt"
if HOME="$profile_root" CODEX_HOME= SKILLS_ROOT= bash "$repo_root/scripts/install.sh" \
  --expected-bundle-sha256 "$bundle_hash" \
  --expected-source-commit "$source_commit" \
  --archive-mode >"$rejection_output" 2>&1; then
  echo "Public Bash installer accepted a noncanonical CodexHome" >&2
  exit 1
fi
grep -F "canonical operating-system account-profile path" "$rejection_output" >/dev/null

skills_rejection_output="$test_root/noncanonical-skills-install.txt"
if CODEX_HOME= SKILLS_ROOT="$skills_root" bash "$repo_root/scripts/install.sh" \
  --expected-bundle-sha256 "$bundle_hash" \
  --expected-source-commit "$source_commit" \
  --archive-mode >"$skills_rejection_output" 2>&1; then
  echo "Public Bash installer accepted a noncanonical SkillsRoot" >&2
  exit 1
fi
grep -F "canonical Codex home skills path" "$skills_rejection_output" >/dev/null

install_synthetic_runtime
"$python_cmd" - "$codex_home/coding-os/install-manifest.json" "$codex_home/.coding-os-install/current.json" "$bundle_hash" "$source_commit" <<'PY'
import json, pathlib, sys
manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
current = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
assert manifest["manifest_version"] == 3
assert manifest["transaction_protocol"] == "ccos-install-transaction-v1"
assert manifest["package"]["bundle_sha256"] == sys.argv[3]
pin = manifest["runtime_pin"]
assert pin["source_commit"] == sys.argv[4]
assert pin["bundle_digest"] == sys.argv[3]
assert pin["install_transaction"] == manifest["transaction"]["id"]
assert pin["protocol_version"] == "ccos-campaign-v1"
assert pin["schema_compatibility"] == "campaign-store-v1"
assert pin["host_capability_probe_version"] == "native-bind-before-turn-scoped-tools-v3"
assert current["status"] == "committed"
assert pathlib.Path(manifest["targets"]["skills_root"]).resolve() == pathlib.Path(sys.argv[1]).parents[1].joinpath("skills").resolve()
assert "legacy_overlap_migration" not in manifest
PY
test -f "$skills_root/codex-coding-os-master/SKILL.md"
for skill_name in "${repository_security_skills[@]}"; do
  test -f "$skills_root/$skill_name/SKILL.md"
done
for skill_name in "${codex_managed_plugin_skill_directories[@]}"; do
  test ! -e "$skills_root/$skill_name"
  test ! -e "$skills_root/${skill_name^^}"
done
test ! -e "$codex_home/coding-os/capability-routing"
test ! -e "$codex_home/coding-os/capability-index"
test ! -e "$codex_home/coding-os/hooks/capability-router"
test ! -e "$codex_home/hooks/capability-router"
test ! -e "$codex_home/coding-os/Capability-Routing"
test ! -e "$codex_home/coding-os/Capability-Index"
test ! -e "$codex_home/coding-os/Hooks/Capability-Router"
test ! -e "$codex_home/Hooks/Capability-Router"
test -f "$codex_home/hooks/campaign-engine/campaign_hook.py"
test -f "$codex_home/coding-os-state/campaigns.sqlite3"

doctor_output="$("$python_cmd" -B - "$profile_root" <<'PY'
import pathlib, sys
profile = pathlib.Path(sys.argv[1]).resolve(strict=True)
support = profile / ".codex" / "coding-os"
sys.path.insert(0, str(support / "scripts" / "agent"))
from campaign_engine import cli
from campaign_engine.runtime_bootstrap import runtime_layout
raise SystemExit(cli.main(["--json", "doctor"], injected_runtime=runtime_layout(profile=profile)))
PY
)"
"$python_cmd" -c 'import json,sys; value=json.load(sys.stdin); assert value["ok"] and value["integrity"]["status"] == "ok"' <<<"$doctor_output"

pointer_before="$(sha256sum "$codex_home/.coding-os-install/current.json")"
install_synthetic_runtime
test "$(sha256sum "$codex_home/.coding-os-install/current.json")" = "$pointer_before"
test "$(sha256sum "$codex_home/config.toml" "$codex_home/case-state/case.json" "$codex_home/plugins/plugin.txt" "$skills_root/unmanaged/SKILL.md")" = "$preserved_before"

uninstall_synthetic_runtime
test ! -e "$codex_home/coding-os"
test ! -e "$skills_root/codex-coding-os-master"
for skill_name in "${repository_security_skills[@]}"; do
  test ! -e "$skills_root/$skill_name"
done
test ! -e "$codex_home/hooks/campaign-engine"
test -f "$codex_home/coding-os-state/campaigns.sqlite3"
test "$(sha256sum "$codex_home/config.toml" "$codex_home/case-state/case.json" "$codex_home/plugins/plugin.txt" "$skills_root/unmanaged/SKILL.md")" = "$preserved_before"
"$python_cmd" - "$codex_home/.coding-os-install/current.json" <<'PY'
import json, pathlib, sys
assert json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["status"] == "uninstalled"
PY

echo "Transactional Bash install/uninstall smoke test passed."
