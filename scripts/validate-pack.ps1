$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Required = @(
  "README.md",
  "AGENTS.md",
  "LICENSE.md",
  "COMMERCIAL.md",
  "NOTICE.md",
  "THIRD_PARTY_SKILLS.md",
  "codex-capabilities\default-skills-reference.md",
  "codex-capabilities\plugins.manifest.json",
  "external-skills\manifest.json",
  "docs\codex-plugins-mcps-hooks.md",
  "docs\external-skills-installation.md",
  "docs\full-skill-inventory.md",
  "docs\pack-design.md",
  "docs\component-classification.md",
  "docs\publishing-checklist.md",
  "templates\first-codex-prompt.md",
  "templates\project-brief.md",
  "templates\prd.md",
  "templates\app-flow-doc.md",
  "templates\tech-stack-doc.md",
  "templates\frontend-guidelines.md",
  "templates\backend-structure.md",
  "templates\security-guidelines.md",
  "templates\implementation-plan.md",
  "templates\tdd.md",
  "templates\repo-docs-template.md",
  "templates\repo-AGENTS.md",
  "templates\scoped-AGENTS.md",
  "templates\handoff-note.md",
  "templates\review-checklist.md",
  "templates\validation-report.md",
  "scripts\install.ps1",
  "scripts\install-external-skills.ps1",
  "scripts\uninstall.ps1",
  "scripts\apply-external-skill-overlays.ps1",
  "scripts\package.ps1",
  "hooks\README.md"
)

$Errors = @()

foreach ($Path in $Required) {
  $Full = Join-Path $RepoRoot $Path
  if (-not (Test-Path $Full)) {
    $Errors += "Missing required file: $Path"
  }
}

$RequiredSkills = @(
  "codex-coding-os-master",
  "catalogue-router",
  "ai-coding-discipline",
  "new-project-documentation-system",
  "technical-docs-pack",
  "create-prd",
  "product-strategy",
  "customer-journey-map",
  "working-backwards",
  "wbs-artifact-planner",
  "artifact-system-designer",
  "artifact-validation-workflow",
  "ssot-drafter",
  "ssot-auditor",
  "process-docs",
  "support-docs",
  "doc",
  "pdf",
  "evidence-checker",
  "deep-critic",
  "pre-mortem",
  "improve-codebase-architecture",
  "react-best-practices",
  "react-native-skills",
  "composition-patterns",
  "cli-creator",
  "playwright",
  "security-best-practices",
  "security-threat-model",
  "security-ownership-map",
  "vercel-optimize",
  "code-review-graph",
  "vexor-cli",
  "external-skill-overlay-pack"
)

$SkillRoot = Join-Path $RepoRoot ".agents\skills"
foreach ($SkillName in $RequiredSkills) {
  $SkillFile = Join-Path $SkillRoot "$SkillName\SKILL.md"
  if (-not (Test-Path $SkillFile)) {
    $Errors += "Missing bundled full skill: $SkillName"
  }
}

Get-ChildItem -Path $SkillRoot -Directory | ForEach-Object {
  $SkillFile = Join-Path $_.FullName "SKILL.md"
  if (-not (Test-Path $SkillFile)) {
    $Errors += "Missing SKILL.md in $($_.FullName)"
  } else {
    $Text = Get-Content -Raw -Path $SkillFile
    if ($Text -notmatch "(?s)^---.*name:.*description:.*---") {
      $Errors += "Missing frontmatter fields in $SkillFile"
    }
  }
}

$Forbidden = @()
$LocalForbiddenPath = Join-Path $RepoRoot ".private-terms.local.txt"
if (Test-Path $LocalForbiddenPath) {
  $Forbidden = Get-Content -Path $LocalForbiddenPath | Where-Object {
    $_.Trim().Length -gt 0 -and -not $_.Trim().StartsWith("#")
  }
}

foreach ($Term in $Forbidden) {
  $Hits = Get-ChildItem -Path $RepoRoot -Recurse -File | Where-Object {
    $_.FullName -notmatch "\\.git\\" -and $_.FullName -notmatch "\\node_modules\\"
  } | Select-String -Pattern $Term -SimpleMatch
  if ($Hits) {
    $Errors += "Forbidden private term found: $Term"
  }
}

$SecretPatterns = @(
  "sk-[A-Za-z0-9]{20,}",
  "ghp_[A-Za-z0-9]{20,}",
  "SUPABASE_SERVICE_ROLE_KEY\s*=",
  "OPENAI_API_KEY\s*=\s*[^<\s]"
)

foreach ($Pattern in $SecretPatterns) {
  $Hits = Get-ChildItem -Path $RepoRoot -Recurse -File | Where-Object {
    $_.FullName -notmatch "\\.git\\" -and $_.FullName -notmatch "\\node_modules\\"
  } | Select-String -Pattern $Pattern
  if ($Hits) {
    $Errors += "Possible secret pattern found: $Pattern"
  }
}

if ($Errors.Count -gt 0) {
  Write-Output "Validation failed:"
  $Errors | ForEach-Object { Write-Output " - $_" }
  exit 1
}

Write-Output "Validation passed."
