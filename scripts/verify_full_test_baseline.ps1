<#
.SYNOPSIS
    Authoritative test suite verification and execution harness for CF-Intelligence.

.DESCRIPTION
    Executes backend, scientific verification, SDK, frontend, and smart contract test suites
    using the existing environment and Python interpreter.
    
    Guarantees:
    - Never mutates application databases, security ledgers, or audit trails.
    - Never deletes production files or Redis namespaces.
    - Never automatically updates snapshots or weakens test assertions.
    - Preserves exact exit codes and records complete test output to log files.
    - Produces a machine-readable JSON summary for audit and CI compliance.

.PARAMETER Suite
    The test suite to execute: 'backend', 'verification', 'sdk', 'frontend', 'contracts', or 'all'.
    Defaults to 'backend'.

.PARAMETER OutputDir
    Directory path where full console logs and summary JSON will be stored.
    Defaults to 'reports/test_baseline_logs'.

.PARAMETER IncludeRealData
    If specified, runs external real-data benchmark tests (Gate B).
    Default is skipped (runs synthetic benchmark tests only).

.PARAMETER VerboseOutput
    If specified, displays detailed verbose test execution output.

.EXAMPLE
    .\scripts\verify_full_test_baseline.ps1 -Suite backend
    .\scripts\verify_full_test_baseline.ps1 -Suite all
    .\scripts\verify_full_test_baseline.ps1 -Suite verification -OutputDir "reports/my_run"
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("backend", "verification", "sdk", "frontend", "contracts", "all")]
    [string]$Suite = "backend",

    [Parameter(Position = 1)]
    [string]$OutputDir = "reports/test_baseline_logs",

    [Parameter()]
    [switch]$IncludeRealData,

    [Parameter()]
    [switch]$VerboseOutput
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $RepoRoot

# ── 1. Create Dedicated Log Output Directory ───────────────────────────────────
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ResolvedOutputDir = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $OutputDir))
if (-not (Test-Path $ResolvedOutputDir)) {
    New-Item -ItemType Directory -Path $ResolvedOutputDir -Force | Out-Null
}

$MasterLogFile = Join-Path $ResolvedOutputDir "verification_run_$Timestamp.log"
$SummaryJsonFile = Join-Path $ResolvedOutputDir "verification_summary_$Timestamp.json"

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $TimeStr = Get-Date -Format "HH:mm:ss"
    $Formatted = "[$TimeStr] [$Level] $Message"
    Write-Host $Formatted
    Add-Content -Path $MasterLogFile -Value $Formatted -Encoding UTF8
}

function Write-Banner {
    param([string]$Title)
    $Line = "=" * 80
    Write-Host ""
    Write-Host $Line -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host $Line -ForegroundColor Cyan
    Add-Content -Path $MasterLogFile -Value "`n$Line`n  $Title`n$Line" -Encoding UTF8
}

Write-Banner "CF-INTELLIGENCE: TEST BASELINE VERIFICATION HARNESS"
Write-Log "Repository Root: $RepoRoot"
Write-Log "Target Suite: $Suite"
Write-Log "Output Directory: $ResolvedOutputDir"
Write-Log "Log File: $MasterLogFile"

# ── 2. Environment & Dependency Detection ──────────────────────────────────────
Write-Banner "1. ENVIRONMENT & TOOLCHAIN DETECTION"

# Detect Python
$PythonExe = $null
if (Get-Command "python" -ErrorAction SilentlyContinue) {
    $PythonExe = (Get-Command "python").Source
    $PyVersion = python --version 2>&1
    Write-Log "Detected Python: $PythonExe ($PyVersion)"
} else {
    Write-Log "Python executable was not found on PATH!" "ERROR"
    exit 1
}

# Detect Git
$GitBranch = "unknown"
$GitCommit = "unknown"
if (Get-Command "git" -ErrorAction SilentlyContinue) {
    $GitBranch = (git rev-parse --abbrev-ref HEAD 2>&1).Trim()
    $GitCommit = (git rev-parse HEAD 2>&1).Trim()
    Write-Log "Git Branch: $GitBranch (Commit: $GitCommit)"
}

# Detect Node / NPM
$NpmExe = $null
if (Get-Command "npm" -ErrorAction SilentlyContinue) {
    $NpmExe = (Get-Command "npm").Source
    $NpmVersion = npm --version 2>&1
    Write-Log "Detected NPM: $NpmExe (v$NpmVersion)"
} else {
    Write-Log "NPM was not detected on PATH. Frontend/contracts suites will require Node.js." "WARN"
}

# Detect Local Redis
$RedisHost = "127.0.0.1"
$RedisPort = 6379
$RedisLive = $false
try {
    $TcpClient = New-Object System.Net.Sockets.TcpClient
    $ConnectTask = $TcpClient.ConnectAsync($RedisHost, $RedisPort)
    if ($ConnectTask.Wait(500)) {
        $RedisLive = $true
        $TcpClient.Close()
        Write-Log "Local Redis detected on $RedisHost`:$RedisPort (live connection active)."
    } else {
        Write-Log "Local Redis is not responding on $RedisHost`:$RedisPort. In-memory fallback will activate." "WARN"
    }
} catch {
    Write-Log "Local Redis is not responding on $RedisHost`:$RedisPort. In-memory fallback will activate." "WARN"
}

# ── 3. Test Execution Engine ───────────────────────────────────────────────────
$SuiteResults = [System.Collections.Generic.List[PSObject]]::new()

function Execute-Suite {
    param(
        [string]$Name,
        [string]$CommandStr,
        [string]$WorkingDir = $RepoRoot,
        [string]$SuiteLogName = ""
    )

    Write-Banner "EXECUTING SUITE: $Name"
    Write-Log "Command: $CommandStr"
    Write-Log "Working Directory: $WorkingDir"

    $SuiteLogFile = if ($SuiteLogName) {
        Join-Path $ResolvedOutputDir "$SuiteLogName`_$Timestamp.log"
    } else {
        Join-Path $ResolvedOutputDir "$($Name.ToLower().Replace(' ', '_'))_$Timestamp.log"
    }

    $StartTime = Get-Date
    $Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

    # Execute command, stream output to both console and log file
    $ExitCode = 0
    try {
        $ProcessInfo = New-Object System.Diagnostics.ProcessStartInfo
        $ProcessInfo.FileName = "cmd.exe"
        $ProcessInfo.Arguments = "/c `"$CommandStr`""
        $ProcessInfo.WorkingDirectory = $WorkingDir
        $ProcessInfo.RedirectStandardOutput = $true
        $ProcessInfo.RedirectStandardError = $true
        $ProcessInfo.UseShellExecute = $false
        $ProcessInfo.CreateNoWindow = $true

        $Process = New-Object System.Diagnostics.Process
        $Process.StartInfo = $ProcessInfo

        $OutputBuilder = [System.Text.StringBuilder]::new()

        $Process.OutputDataReceived += [System.Diagnostics.DataReceivedEventHandler]{
            param($sender, $e)
            if ($e.Data -ne $null) {
                Write-Host $e.Data
                $null = $OutputBuilder.AppendLine($e.Data)
            }
        }
        $Process.ErrorDataReceived += [System.Diagnostics.DataReceivedEventHandler]{
            param($sender, $e)
            if ($e.Data -ne $null) {
                Write-Host $e.Data -ForegroundColor Yellow
                $null = $OutputBuilder.AppendLine($e.Data)
            }
        }

        $Process.Start() | Out-Null
        $Process.BeginOutputReadLine()
        $Process.BeginErrorReadLine()
        $Process.WaitForExit()
        $ExitCode = $Process.ExitCode

        $FullOutput = $OutputBuilder.ToString()
        [System.IO.File]::WriteAllText($SuiteLogFile, $FullOutput, [System.Text.Encoding]::UTF8)
        Add-Content -Path $MasterLogFile -Value $FullOutput -Encoding UTF8
    } catch {
        Write-Log "Execution exception occurred: $_" "ERROR"
        $ExitCode = 1
        $FullOutput = $_.ToString()
    }

    $Stopwatch.Stop()
    $DurationSeconds = [Math]::Round($Stopwatch.Elapsed.TotalSeconds, 2)

    # Parse Pytest summary counts if available
    $Passed = 0
    $Failed = 0
    $Skipped = 0
    $Errors = 0

    if ($FullOutput -match "(\d+)\s+passed") { $Passed = [int]$Matches[1] }
    if ($FullOutput -match "(\d+)\s+failed") { $Failed = [int]$Matches[1] }
    if ($FullOutput -match "(\d+)\s+skipped") { $Skipped = [int]$Matches[1] }
    if ($FullOutput -match "(\d+)\s+error") { $Errors = [int]$Matches[1] }

    $Status = if ($ExitCode -eq 0) { "PASSED" } else { "FAILED" }
    $StatusColor = if ($ExitCode -eq 0) { "Green" } else { "Red" }

    Write-Host ""
    Write-Host ">> $Name Result: $Status (Exit Code: $ExitCode) in $DurationSeconds s" -ForegroundColor $StatusColor
    Write-Log "$Name Summary: $Passed passed, $Failed failed, $Skipped skipped, $Errors errors (Duration: ${DurationSeconds}s)"

    $ResultObj = [PSCustomObject]@{
        SuiteName        = $Name
        Status           = $Status
        ExitCode         = $ExitCode
        DurationSeconds  = $DurationSeconds
        Passed           = $Passed
        Failed           = $Failed
        Skipped          = $Skipped
        Errors           = $Errors
        LogFile          = $SuiteLogFile
    }
    $SuiteResults.Add($ResultObj)

    return $ExitCode
}

# ── 4. Suite Dispatcher ────────────────────────────────────────────────────────
$OverallExitCode = 0

if ($Suite -in @("backend", "all")) {
    $BackendArgs = "backend/tests/ -v --tb=short"
    if ($IncludeRealData) {
        $BackendArgs += " --include-real-data"
    }
    $BackendCmd = "python -m pytest $BackendArgs"
    $rc = Execute-Suite -Name "Backend Test Suite" -CommandStr $BackendCmd -SuiteLogName "backend_suite"
    if ($rc -ne 0) { $OverallExitCode = $rc }
}

if ($Suite -in @("verification", "all")) {
    $VerifCmd = "python -m pytest verification/ -v --tb=short"
    $rc = Execute-Suite -Name "Scientific Verification Suite" -CommandStr $VerifCmd -SuiteLogName "verification_suite"
    if ($rc -ne 0) { $OverallExitCode = $rc }
}

if ($Suite -in @("sdk", "all")) {
    $SdkCmd = "python -m pytest sdk/tests/ -v --tb=short"
    $rc = Execute-Suite -Name "Python SDK Suite" -CommandStr $SdkCmd -SuiteLogName "sdk_suite"
    if ($rc -ne 0) { $OverallExitCode = $rc }
}

if ($Suite -in @("frontend", "all")) {
    if ($NpmExe) {
        $FeCmd = "npm --prefix frontend test -- --run"
        $rc = Execute-Suite -Name "Frontend Vitest Suite" -CommandStr $FeCmd -SuiteLogName "frontend_suite"
        if ($rc -ne 0) { $OverallExitCode = $rc }
    } else {
        Write-Log "Skipping Frontend Suite (NPM not found)." "WARN"
    }
}

if ($Suite -in @("contracts", "all")) {
    if ($NpmExe) {
        $ContractsCmd = "npm --prefix contracts test"
        $rc = Execute-Suite -Name "EVM Smart Contracts Suite" -CommandStr $ContractsCmd -SuiteLogName "contracts_suite"
        if ($rc -ne 0) { $OverallExitCode = $rc }
    } else {
        Write-Log "Skipping Smart Contracts Suite (NPM not found)." "WARN"
    }
}

# ── 5. Machine-Readable Summary Report Generation ─────────────────────────────
Write-Banner "VERIFICATION SUMMARY & AUDIT LEDGER"

$FinalStatus = if ($OverallExitCode -eq 0) { "SUCCESS" } else { "COMPLETED_WITH_FAILURES" }
$SummaryReport = [ordered]@{
    HarnessVersion    = "1.0.0"
    GeneratedAtUtc    = (Get-Date).ToUniversalTime().ToString("o")
    GitBranch         = $GitBranch
    GitCommit         = $GitCommit
    PythonVersion     = (python --version 2>&1).ToString().Trim()
    TargetSuite       = $Suite
    OverallStatus     = $FinalStatus
    OverallExitCode   = $OverallExitCode
    TotalSuitesRun    = $SuiteResults.Count
    Suites            = $SuiteResults
}

$SummaryJson = $SummaryReport | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText($SummaryJsonFile, $SummaryJson, [System.Text.Encoding]::UTF8)

Write-Log "Summary JSON generated: $SummaryJsonFile"
Write-Log "Master log generated: $MasterLogFile"
Write-Host ""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "  FINAL OVERALL STATUS: $FinalStatus (Exit Code: $OverallExitCode)" -ForegroundColor $(if ($OverallExitCode -eq 0) { "Green" } else { "Yellow" })
Write-Host "================================================================================" -ForegroundColor Cyan

exit $OverallExitCode
