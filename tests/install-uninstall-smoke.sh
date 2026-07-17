#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/ccos-transaction-smoke.XXXXXX")"
skills_root="$test_root/skills"
codex_home="$test_root/codex-home"
python_cmd="${PYTHON:-python}"
bundle_hash="$("$python_cmd" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["aggregate_sha256"])' "$repo_root/install-bundle.manifest.json")"

cleanup() { rm -rf "$test_root"; }
trap cleanup EXIT

mkdir -p "$skills_root/unmanaged" "$codex_home/case-state" "$codex_home/plugins"
printf 'preserved-config' > "$codex_home/config.toml"
printf 'preserved-case' > "$codex_home/case-state/case.json"
printf 'preserved-plugin' > "$codex_home/plugins/plugin.txt"
printf 'preserved-skill' > "$skills_root/unmanaged/SKILL.md"
preserved_before="$(sha256sum "$codex_home/config.toml" "$codex_home/case-state/case.json" "$codex_home/plugins/plugin.txt" "$skills_root/unmanaged/SKILL.md")"

bash "$repo_root/scripts/install.sh" --skills-root "$skills_root" --codex-home "$codex_home" --expected-bundle-sha256 "$bundle_hash" --archive-mode
"$python_cmd" - "$codex_home/coding-os/install-manifest.json" "$codex_home/.coding-os-install/current.json" "$bundle_hash" <<'PY'
import json, pathlib, sys
manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
current = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
assert manifest["manifest_version"] == 3
assert manifest["transaction_protocol"] == "ccos-install-transaction-v1"
assert manifest["package"]["bundle_sha256"] == sys.argv[3]
assert current["status"] == "committed"
PY
test -f "$skills_root/codex-coding-os-master/SKILL.md"

pointer_before="$(sha256sum "$codex_home/.coding-os-install/current.json")"
bash "$repo_root/scripts/install.sh" --skills-root "$skills_root" --codex-home "$codex_home" --expected-bundle-sha256 "$bundle_hash" --archive-mode
test "$(sha256sum "$codex_home/.coding-os-install/current.json")" = "$pointer_before"
test "$(sha256sum "$codex_home/config.toml" "$codex_home/case-state/case.json" "$codex_home/plugins/plugin.txt" "$skills_root/unmanaged/SKILL.md")" = "$preserved_before"

bash "$repo_root/scripts/uninstall.sh" --skills-root "$skills_root" --codex-home "$codex_home"
test ! -e "$codex_home/coding-os"
test ! -e "$skills_root/codex-coding-os-master"
test "$(sha256sum "$codex_home/config.toml" "$codex_home/case-state/case.json" "$codex_home/plugins/plugin.txt" "$skills_root/unmanaged/SKILL.md")" = "$preserved_before"
"$python_cmd" - "$codex_home/.coding-os-install/current.json" <<'PY'
import json, pathlib, sys
assert json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["status"] == "uninstalled"
PY

echo "Transactional Bash install/uninstall smoke test passed."
