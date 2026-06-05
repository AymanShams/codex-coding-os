# Publishing checklist

## Before sharing

- Run `scripts/validate-pack.ps1`.
- Open `templates/first-codex-prompt.md` and confirm it is beginner-friendly.
- Confirm `LICENSE.md` and `COMMERCIAL.md` match the intended sharing model.
- Rebuild the ZIP with `scripts/package.ps1`.
- Share the ZIP or repo link.

## Before a public release

- Review the license with counsel.
- Verify every third-party link and license.
- Decide whether the repo is source-available or OSI-approved open source.
- Run the release-safety scan.
- Run the secret-pattern scan.
- Test install on a clean Windows profile.
- Confirm examples and templates are generic.

## Release notes template

```text
Version:
Date:

Added:
- 

Changed:
- 

Fixed:
- 

Known limitations:
- 
```
