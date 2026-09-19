#!/usr/bin/env bash
set -euo pipefail

if [[ "${GITHUB_ACTIONS:-}" != "true" ]]; then
  echo "This prerequisite setup is limited to disposable GitHub Actions runners." >&2
  exit 1
fi

if [[ "$(command -v bwrap)" != "/usr/bin/bwrap" ]]; then
  echo "The Ubuntu prerequisite requires the packaged /usr/bin/bwrap executable." >&2
  exit 1
fi

probe_error="$(mktemp "$RUNNER_TEMP/coding-os-bwrap-probe.XXXXXX")"
trap 'rm -f -- "$probe_error"' EXIT

probe_boundary() {
  # Match the Linux READ_ONLY command in campaign_engine/evidence.py.
  /usr/bin/bwrap --ro-bind / / --proc /proc --dev /dev --unshare-pid \
    --die-with-parent --chdir "$PWD" -- /usr/bin/true
}

if probe_boundary 2>"$probe_error"; then
  echo "Linux read-only validation boundary is available."
  exit 0
fi
cat "$probe_error" >&2

restriction="$(sudo sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || true)"
if [[ "$restriction" != "1" ]] || ! grep -Eq \
  '^bwrap: (setting up uid map: Permission denied|Creating new namespace failed: Operation not permitted|No permissions to create new namespace([,. ].*)?)$' \
  "$probe_error"; then
  echo "The failed probe did not establish the Ubuntu namespace prerequisite." >&2
  exit 1
fi

profile=/etc/apparmor.d/coding-os-bwrap
if sudo test -e "$profile"; then
  echo "An existing Coding OS bubblewrap profile requires inspection." >&2
  exit 1
fi

# Ubuntu's documented executable-specific user-namespace permission.
# https://documentation.ubuntu.com/release-notes/24.04/
sudo tee "$profile" >/dev/null <<'PROFILE'
abi <abi/4.0>,
include <tunables/global>

profile coding_os_bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
}
PROFILE
sudo apparmor_parser -r "$profile"
probe_boundary
echo "Linux read-only validation boundary is available with its namespace prerequisite."
