# ============================================================
# Install / refresh KreuzbergParser dependencies on Windows
# without mixing incompatible stacks (torch CPU, paddlepaddle,
# multiple OpenCV providers, Linux-only nvidia-* wheels).
#
# Do NOT use `uv pip sync requirements.txt` on Windows — that lockfile
# includes Linux-only NVIDIA packages (e.g. nvidia-cufile).
#
# Usage:
#   .\scripts\update_deps.ps1                 # install/upgrade into .venv
#   .\scripts\update_deps.ps1 -Sync           # same (alias for clarity)
#   .\scripts\update_deps.ps1 -Check          # conflict check only
#   .\scripts\update_deps.ps1 -Cuda cu130     # PyTorch CUDA/CPU index
#   .\scripts\update_deps.ps1 -Cuda cpu
#   .\scripts\update_deps.ps1 -NoUpgrade      # install without --upgrade
#   .\scripts\update_deps.ps1 -Python 3.12
#
# See docs/DEPENDENCY_CONFLICTS.md
# ============================================================

param(
    [ValidateSet("cu118", "cu121", "cu124", "cu126", "cu128", "cu130", "cpu")]
    [string]$Cuda = "cu130",

    [string]$Python = "3.12",

    [switch]$Sync,
    [switch]$Check,
    [switch]$NoUpgrade,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

if ($Help) {
    Get-Help $MyInvocation.MyCommand.Path -Full
    exit 0
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
Set-Location $Root

$InFile = Join-Path $Root "requirements.in"
$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Die([string]$Message) {
    Write-Host "error: $Message" -ForegroundColor Red
    exit 1
}

function Get-PytorchIndexUrl([string]$Tag) {
    if ($Tag -eq "cpu") {
        return "https://download.pytorch.org/whl/cpu"
    }
    return "https://download.pytorch.org/whl/$Tag"
}

function Require-Uv {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Die "uv not found. Install: https://docs.astral.sh/uv/"
    }
}

function Ensure-Venv {
    if (Test-Path $PythonExe) {
        $ver = & $PythonExe -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"
        if ($LASTEXITCODE -ne 0) {
            Die "failed to query .venv Python version"
        }
        $ver = $ver.Trim()
        if ($ver -ne $Python) {
            Write-Host "WARN: .venv is Python $ver; preferred is $Python" -ForegroundColor Yellow
            Write-Host "      Recreate with: uv venv .venv --python $Python" -ForegroundColor Yellow
        } else {
            Write-Step "Using interpreter $PythonExe ($ver)"
        }
        return
    }

    Write-Step "Creating .venv with Python $Python"
    uv venv .venv --python $Python
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $PythonExe)) {
        Die "failed to create .venv with Python $Python"
    }
}

function Invoke-ConflictCheck {
    <#
    .SYNOPSIS
      Runs the conflict checker on the host; returns only an int exit code.
    #>
    Write-Step "Checking dependency conflicts"
    $checker = Join-Path $ScriptDir "check_dep_conflicts.py"
    if (-not (Test-Path $checker)) {
        Die "missing conflict checker: $checker"
    }
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # Start-Process evita misturar stdout nativo com o output stream do PowerShell.
        $p = Start-Process -FilePath $PythonExe `
            -ArgumentList @($checker, $Cuda, $PythonExe) `
            -WorkingDirectory $Root `
            -Wait -PassThru -NoNewWindow
        return ,[int]$p.ExitCode
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

function Remove-ForbiddenPackages {
    Write-Step "Removing forbidden / Linux-only packages if present"
    $forbidden = @(
        "paddleocr",
        "paddlex",
        "paddlepaddle",
        "paddlepaddle-gpu",
        "rapidocr",
        "nvidia-cufile",
        "nvidia-cufile-cu12",
        "nvidia-cufile-cu13"
    )
    uv pip uninstall @forbidden --python $PythonExe 2>$null | Out-Null
}

# --- main ----------------------------------------------------------------
Require-Uv

if (-not (Test-Path $InFile)) {
    Die "missing input file: $InFile"
}

if ($Check -and ($Sync -or -not $NoUpgrade)) {
    # -Check is independent; Sync/NoUpgrade ignored with a note if both passed
}

Ensure-Venv

if ($Check) {
    $rc = Invoke-ConflictCheck
    if ($rc -ne 0) {
        Write-Host "==> Check FAILED (dependency conflicts)" -ForegroundColor Red
        Write-Host "    See docs/DEPENDENCY_CONFLICTS.md" -ForegroundColor Yellow
        exit 1
    }
    Write-Host "==> Check OK" -ForegroundColor Green
    exit 0
}

$IndexUrl = Get-PytorchIndexUrl $Cuda
$UpgradeArgs = @()
if (-not $NoUpgrade) {
    $UpgradeArgs += "--upgrade"
}

Write-Step "Installing PyTorch ($Cuda) from $IndexUrl"
uv pip install torch torchvision `
    --python $PythonExe `
    --index-url $IndexUrl `
    @UpgradeArgs
if ($LASTEXITCODE -ne 0) {
    Die "failed to install torch/torchvision from $IndexUrl"
}

Write-Step "Installing project dependencies from requirements.in"
uv pip install -r $InFile `
    --python $PythonExe `
    @UpgradeArgs
if ($LASTEXITCODE -ne 0) {
    Die "failed to install requirements.in"
}

Remove-ForbiddenPackages

# OpenCV: keep only opencv-python-headless (EasyOCR); no contrib / GUI wheels.
Write-Step "Normalizing OpenCV providers (keep opencv-python-headless)"
$opencvExtras = @(
    "opencv-python",
    "opencv-contrib-python",
    "opencv-contrib-python-headless"
)
uv pip uninstall @opencvExtras --python $PythonExe 2>$null | Out-Null
uv pip install "opencv-python-headless" --python $PythonExe
if ($LASTEXITCODE -ne 0) {
    Die "failed to install opencv-python-headless"
}

$rc = Invoke-ConflictCheck
if ($rc -ne 0) {
    Write-Host "==> Install finished but conflict check FAILED" -ForegroundColor Red
    Write-Host "    See docs/DEPENDENCY_CONFLICTS.md" -ForegroundColor Yellow
    exit 1
}

Write-Step "Done"
Write-Host "    Source: $InFile"
Write-Host "    Python: $PythonExe"
Write-Host "    CUDA:   $Cuda ($IndexUrl)"
Write-Host "    Run:    .\.venv\Scripts\python.exe main.py"
if ($Sync) {
    Write-Host "    (-Sync acknowledged; Windows uses install, not requirements.txt sync)" -ForegroundColor Gray
}
