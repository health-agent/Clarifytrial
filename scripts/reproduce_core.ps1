[CmdletBinding()]
param(
    [ValidateSet("package", "core", "full")]
    [string]$Mode = "package",
    [string]$PythonExe = "python",
    [string]$VenvPath = ".venv-repro",
    [string]$OutputRoot = "runs/reproduction-core",
    [switch]$SkipInstall,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )
    Write-Host "`n== $Label =="
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

$pythonVersion = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw "Python could not be started: $PythonExe"
}
if ($pythonVersion.Trim() -ne "3.12") {
    throw "ClarifyTrial reproduction requires Python 3.12; found $pythonVersion"
}

if (Test-Path $OutputRoot) {
    throw "Output already exists. Preserve it and choose another -OutputRoot: $OutputRoot"
}

if (-not (Test-Path (Join-Path $VenvPath "Scripts\python.exe"))) {
    Invoke-Checked "Create Python 3.12 environment" {
        & $PythonExe -m venv $VenvPath
    }
}

$venvPython = (Resolve-Path (Join-Path $VenvPath "Scripts\python.exe")).Path
$clarifytrial = Join-Path (Resolve-Path $VenvPath).Path "Scripts\clarifytrial.exe"

if (-not $SkipInstall) {
    Invoke-Checked "Install the locked deterministic environment" {
        & $venvPython -m pip install `
            -c constraints\repro-python312.txt `
            -e ".[dev,retrieval-bm25]"
    }
    Invoke-Checked "Install the pinned NLTK tokenizer data" {
        & $venvPython -m nltk.downloader punkt
    }
}
if (-not (Test-Path $clarifytrial)) {
    throw "ClarifyTrial is not installed in $VenvPath"
}

if (-not $SkipTests) {
    Invoke-Checked "Run the full automated test suite" {
        & $venvPython -m pytest -q
    }
    Invoke-Checked "Check installed dependencies" {
        & $venvPython -m pip check
    }
}

New-Item -ItemType Directory -Force $OutputRoot | Out-Null
$sourceSnapshot = "data\interactive_public_benchmark_v1\source_snapshot"
$commonFacts = Join-Path $OutputRoot "public-protocol-common-facts"
$policyScale = Join-Path $OutputRoot "public-protocol-policy-scale"
$policyRunRoot = Join-Path $OutputRoot "policy-scale"
$routeOutput = Join-Path $policyRunRoot "route-choice-controlled"
$burdenOutput = Join-Path $policyRunRoot "burden-ablation-final"

Invoke-Checked "Run one complete deterministic patient workflow" {
    & $clarifytrial run-full-ui --auto --output (Join-Path $OutputRoot "full-ui-smoke")
}
Invoke-Checked "Reproduce the 30-patient direct-transition evaluation" {
    & $venvPython scripts\run_public_protocol_common_facts_known.py `
        --trial-set data\public_protocol_benchmark_v1\trial_set.json `
        --patient-pairs data\public_protocol_benchmark_v1\patient_pairs.json `
        --output $commonFacts
}
Invoke-Checked "Reproduce the public-protocol policy evaluation" {
    & $venvPython scripts\run_public_protocol_policy_scale.py `
        --trial-set data\public_protocol_benchmark_v1\trial_set.json `
        --patient-pairs data\public_protocol_benchmark_v1\patient_pairs.json `
        --output $policyScale
}
Invoke-Checked "Reproduce patient-aware route selection" {
    & $clarifytrial run-public-route-choice-benchmark `
        --source-cache $sourceSnapshot `
        --output $routeOutput
}
Invoke-Checked "Reproduce the patient-burden ablation" {
    & $clarifytrial run-public-burden-benchmark `
        --source-cache $sourceSnapshot `
        --action-budget 3 `
        --output $burdenOutput
}

$budgets = if ($Mode -eq "full") {
    @(1, 2, 3)
} elseif ($Mode -eq "core") {
    @(1)
} else {
    @()
}
foreach ($budget in $budgets) {
    if ($Mode -eq "full") {
        Invoke-Checked "Reproduce public patients at confirmation budget $budget" {
            & $clarifytrial run-public-interactive-benchmark `
                --source-cache $sourceSnapshot `
                --action-budget $budget `
                --output (Join-Path $policyRunRoot "budget-$budget\public-patients")
        }
        Invoke-Checked "Reproduce the public value grid at confirmation budget $budget" {
            & $clarifytrial run-public-grid-stress `
                --source-cache $sourceSnapshot `
                --action-budget $budget `
                --output (Join-Path $policyRunRoot "budget-$budget\public-grid")
        }
    }
    Invoke-Checked "Reproduce 1,800 structures at confirmation budget $budget" {
        & $clarifytrial run-interactive-stress `
            --structures-per-topology 200 `
            --seed 20260830 `
            --policy-seed 20260830 `
            --action-budget $budget `
            --output (Join-Path $policyRunRoot "budget-$budget\structural-1800")
    }
}

Invoke-Checked "Compare the rerun with committed evidence" {
    $verifyArguments = @(
        "scripts\verify_reproduction.py",
        "--output-root", $OutputRoot,
        "--common-facts", $commonFacts,
        "--policy-scale", $policyScale,
        "--route-summary", (Join-Path $routeOutput "summary.json"),
        "--burden-summary", (Join-Path $burdenOutput "summary.json")
    )
    if ($budgets.Count -gt 0) {
        $verifyArguments += @(
            "--structural-run-root", $policyRunRoot,
            "--structural-budgets"
        )
        $verifyArguments += $budgets
    }
    & $venvPython @verifyArguments
}

if ($Mode -eq "full") {
    $sharedFacts = Join-Path $OutputRoot "public-protocol-shared-facts"
    $presentationEvidence = Join-Path $OutputRoot "presentation-evidence"
    Invoke-Checked "Rebuild the shared-information report" {
        & $venvPython scripts\build_public_shared_fact_report.py `
            --trial-set data\public_protocol_benchmark_v1\trial_set.json `
            --output-dir $sharedFacts
    }
    Invoke-Checked "Rebuild the deterministic presentation evidence bundle" {
        & $venvPython scripts\build_policy_scale_tables.py `
            --run-root $policyRunRoot `
            --burden-summary (Join-Path $burdenOutput "summary.json") `
            --route-choice-summary (Join-Path $routeOutput "summary.json") `
            --public-protocol-scale $policyScale `
            --common-facts-known $commonFacts `
            --shared-fact-report (Join-Path $sharedFacts "shared-fact-report.json") `
            --archived-live-model-smoke-summary docs\internal\results\presentation-evidence-v2\live_model_smoke_summary.csv `
            --output $presentationEvidence
    }
    Invoke-Checked "Render the presentation evidence figures" {
        & $venvPython scripts\render_presentation_evidence_figures.py `
            --input-dir $presentationEvidence `
            --output-dir (Join-Path $OutputRoot "presentation-diagrams")
    }
}

Write-Host "`nClarifyTrial deterministic reproduction passed."
Write-Host "Report: $(Join-Path $OutputRoot 'reproduction-report.json')"
if ($Mode -eq "package") {
    Write-Host "Use -Mode core to add the already validated 1,800-structure budget-1 rerun."
    Write-Host "Use -Mode full to rebuild all three structural budgets and presentation figures."
} elseif ($Mode -eq "core") {
    Write-Host "Use -Mode full to run confirmation budgets 1, 2, and 3 (2,073,600 policy-state calculations)."
}
