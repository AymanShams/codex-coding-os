# Codex rules

Codex rules control which commands Codex can run outside the sandbox.

This pack includes an optional rules template at:

`.codex/rules/default.rules`

## Install options

### User-level managed rules

Install the universal campaign rules through the transactional installer. It
replaces only the marked Coding OS block and preserves unrelated rules bytes:

```powershell
$SourceCommit = git rev-parse HEAD
$BundleDigest = (Get-Content .\install-bundle.manifest.json | ConvertFrom-Json).aggregate_sha256
.\scripts\install.ps1 `
  -ExpectedSourceCommit $SourceCommit `
  -ExpectedBundleSha256 $BundleDigest `
  -InstallUniversalPolicy `
  -PolicyAuthoritySource explicit-user-approval `
  -PolicyAuthorityReference "approved-universal-policy-installation"
```

Do not overwrite the complete user rules file with `Copy-Item`. Restart Codex or
open a fresh task after the transaction so the installed rules are reloaded.

### Project-level rules

Project-local rules are not managed by the universal installer. Use them only
when the trusted project intentionally carries a separate command policy:

```powershell
$Target = ".\.codex\rules\default.rules"
if (Test-Path -LiteralPath $Target) {
  throw "Project rules already exist. Review and merge the intended rules instead of overwriting them."
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Target)
Copy-Item -LiteralPath "<path-to-this-pack>\.codex\rules\default.rules" -Destination $Target
```

Project-local rules load only when the project `.codex/` layer is trusted.

## Review before enabling

Check that the rules match the project and team workflow:

- deletion policy is strict enough for the repo
- dependency-manager prompts match the package managers in use
- deployment commands match the hosting stack
- migration commands match the database stack
- environment and `.env` reads require approval
- destructive git commands are blocked or prompted

## Test

When the Codex CLI is available, test representative commands:

```powershell
codex execpolicy check --pretty --rules "$HOME\.codex\rules\default.rules" -- git push origin main
codex execpolicy check --pretty --rules "$HOME\.codex\rules\default.rules" -- rm -rf .git
codex execpolicy check --pretty --rules "$HOME\.codex\rules\default.rules" -- npm install
```

## Notes

Rules are experimental and may change. Re-check the official Codex rules documentation before a public release or before adopting these rules in a team environment.
