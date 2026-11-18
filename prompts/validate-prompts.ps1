$ErrorActionPreference = 'Stop'

$promptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$requiredFiles = @(
    '00A-1-discovery.md',
    '00A-2-governance.md',
    '00B-1-foundation.md',
    '00B-2-live-provider.md',
    '00B-3-jobs-visual.md',
    '01-auth.md',
    '02-groups-brief.md',
    '03-interview.md',
    '04-role-todos.md',
    '05-execution-review.md',
    '06-account-lifecycle.md',
    '07-production-readiness.md',
    'GATE-B.md',
    'GATE-D.md'
)

$errors = [System.Collections.Generic.List[string]]::new()

foreach ($name in $requiredFiles) {
    $path = Join-Path $promptRoot $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $errors.Add("Missing file: $name")
        continue
    }

    $content = Get-Content -Raw -LiteralPath $path
    $sectionCount = ([regex]::Matches($content, '(?m)^## ')).Count
    if ($sectionCount -ne 6) {
        $errors.Add("${name}: expected 6 H2 sections, found $sectionCount")
    }

    foreach ($term in @('HTML', 'BLOCKED')) {
        if (-not $content.Contains($term)) {
            $errors.Add("${name}: missing safety term '$term'")
        }
    }
}

# Numbered steps inside each "## 수행" section must stay contiguous after edits.
foreach ($name in $requiredFiles) {
    $path = Join-Path $promptRoot $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }

    $content = Get-Content -Raw -Encoding UTF8 -LiteralPath $path
    # Steps heading built from code points so Windows PowerShell 5.1 does not misread a BOM-less script.
    $stepsHeading = [string][char]0xC218 + [char]0xD589
    $section = [regex]::Match($content, '(?ms)^## ' + $stepsHeading + '\s*\r?\n(.*?)(?=^## |\z)')
    if (-not $section.Success) {
        $errors.Add("${name}: steps section heading not found")
        continue
    }

    $steps = [regex]::Matches($section.Groups[1].Value, '(?m)^(\d+)\.\s')
    for ($index = 0; $index -lt $steps.Count; $index++) {
        $actual = [int]$steps[$index].Groups[1].Value
        $expected = $index + 1
        if ($actual -ne $expected) {
            $errors.Add("${name}: step numbering expected $expected, found $actual")
        }
    }
}

$liveFiles = @(
    '00B-2-live-provider.md',
    '02-groups-brief.md',
    '03-interview.md',
    '04-role-todos.md',
    '05-execution-review.md',
    'GATE-D.md'
)

foreach ($name in $liveFiles) {
    $content = Get-Content -Raw -LiteralPath (Join-Path $promptRoot $name)
    foreach ($term in @('LIVE', 'Timely')) {
        if (-not $content.Contains($term)) {
            $errors.Add("${name}: missing LIVE safety term '$term'")
        }
    }
}

$readme = Get-Content -Raw -LiteralPath (Join-Path $promptRoot 'README.md')
foreach ($name in $requiredFiles) {
    if (-not $readme.Contains($name)) {
        $errors.Add("README.md: missing workflow file '$name'")
    }
}

$governance = Get-Content -Raw -LiteralPath (Join-Path $promptRoot '00A-2-governance.md')
foreach ($term in @('prompts/README.md', 'prompts/VALIDATION.md', 'docs/HANDOFF.md')) {
    if (-not $governance.Contains($term)) {
        $errors.Add("00A-2-governance.md: missing common-rule link '$term'")
    }
}

foreach ($name in @('00B-2-live-provider.md', '00B-3-jobs-visual.md', '01-auth.md')) {
    $content = Get-Content -Raw -LiteralPath (Join-Path $promptRoot $name)
    if (-not $content.Contains('DETERMINISTIC PASS')) {
        $errors.Add("${name}: missing deterministic progress marker")
    }
}

$provider = Get-Content -Raw -LiteralPath (Join-Path $promptRoot '00B-2-live-provider.md')
if (-not $provider.Contains('LIVE BLOCKED')) {
    $errors.Add('00B-2-live-provider.md: missing non-blocking LIVE marker')
}

$gateD = Get-Content -Raw -LiteralPath (Join-Path $promptRoot 'GATE-D.md')
$visionIndex = $gateD.IndexOf('vision')
$reviewIndex = $gateD.IndexOf('M2.review')
if ($visionIndex -lt 0 -or $reviewIndex -lt 0 -or $visionIndex -gt $reviewIndex) {
    $errors.Add('GATE-D.md: vision preflight must appear before M2.review recommendation')
}

foreach ($name in @('GATE-B.md', 'GATE-D.md')) {
    $content = Get-Content -Raw -LiteralPath (Join-Path $promptRoot $name)
    foreach ($term in @('docs/PRODUCT.md', 'docs/ROADMAP.md', 'docs/HANDOFF.md')) {
        if (-not $content.Contains($term)) {
            $errors.Add("${name}: missing post-approval record target '$term'")
        }
    }
}

if (-not $gateD.Contains('docs/LIVE_TESTING.md')) {
    $errors.Add('GATE-D.md: missing durable vision result target')
}

$flowTerms = @{
    '02-groups-brief.md' = @('FORMING', 'ACTIVE', 'CANCELLED', 'GROUP_NOT_JOINABLE', 'moim-group-create-position.html')
    '03-interview.md' = @('WAITING_ASSIGNMENT', 'candidate revision', 'source_project_brief_revision', 'preference submission revision')
    '04-role-todos.md' = @('source_assignment_revision', 'assignment revision', 'todo draft', '재배정 operation')
}

foreach ($name in $flowTerms.Keys) {
    $content = Get-Content -Raw -LiteralPath (Join-Path $promptRoot $name)
    foreach ($term in $flowTerms[$name]) {
        if (-not $content.Contains($term)) {
            $errors.Add("${name}: missing D-A5 flow invariant '$term'")
        }
    }
}

if (-not $readme.Contains('| D-A5 |')) {
    $errors.Add('README.md: D-A5 is referenced but not recorded in the approval table')
}

foreach ($name in @('00A-1-discovery.md', 'GATE-B.md', '04-role-todos.md')) {
    # Korean patterns are \u-escaped so Windows PowerShell 5.1 parses this BOM-less script correctly.
    # Stale GATE A conditionals about the position screen (decided by D-A5).
    $content = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $promptRoot $name)
    $stalePattern = 'GATE A\uC5D0\uC11C \uD3EC\uC9C0\uC158|GATE A \uB610\uB294 GATE B|GATE A \uB514\uC790\uC778 \uACB0\uC815'
    if ([regex]::IsMatch($content, $stalePattern)) {
        $errors.Add("${name}: position screen is decided by D-A5; remove GATE A conditional wording")
    }
}

$interviewRow = ($readme -split "`r?`n" | Where-Object { $_.Contains('03-interview.md') } | Select-Object -First 1)
foreach ($term in @('LIVE BLOCKED', '00B-2-live-provider.md')) {
    if ([string]::IsNullOrEmpty($interviewRow) -or -not $interviewRow.Contains($term)) {
        $errors.Add("README.md: stage 3 row missing deferred LIVE instruction '$term'")
    }
}

if ($errors.Count -gt 0) {
    Write-Host "FAIL: $($errors.Count) issue(s)" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "- $_" }
    exit 1
}

Write-Host "PASS: validated $($requiredFiles.Count) workflow prompt files" -ForegroundColor Green
