param(
    [string]$ProjectRoot = "",
    [string]$WslDistro = "Ubuntu-22.04",
    [string]$BackendCondaEnv = "ntu-invoice-vllm",
    [string]$FrontendCondaEnv = "invoice_frontend",
    [int]$RestartDelaySeconds = 5
)

$ErrorActionPreference = "Stop"
$SupervisorDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ProjectRoot) {
    $ProjectRoot = $SupervisorDirectory
}
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$ElectronDir = Join-Path $ProjectRoot "electron"

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "backend"))) {
    throw "The selected project folder has no backend directory: $ProjectRoot"
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "frontend"))) {
    throw "The selected project folder has no frontend directory: $ProjectRoot"
}
if (-not (Test-Path -LiteralPath $ElectronDir)) {
    throw "The selected project folder has no electron directory: $ProjectRoot"
}

function Test-TcpPort {
    param([int]$Port)

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $result = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $result.AsyncWaitHandle.WaitOne(1000, $false)) {
            return $false
        }
        $client.EndConnect($result)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Convert-ToBashSingleQuoted {
    param([string]$Value)
    $quote = [char]39
    $backslash = [char]92
    $replacement = $quote + $backslash + $quote + $quote
    return $quote + $Value.Replace($quote, $replacement) + $quote
}

function New-CondaCommand {
    param(
        [string]$WorkingDirectory,
        [string]$EnvironmentName,
        [string]$Command
    )

    $quotedDirectory = Convert-ToBashSingleQuoted $WorkingDirectory
    $quotedEnvironment = Convert-ToBashSingleQuoted $EnvironmentName

    return @"
set -e;
if command -v conda >/dev/null 2>&1; then
  CONDA_EXE_PATH="`$(command -v conda)";
elif [ -x "`$HOME/miniconda3/bin/conda" ]; then
  CONDA_EXE_PATH="`$HOME/miniconda3/bin/conda";
elif [ -x "`$HOME/anaconda3/bin/conda" ]; then
  CONDA_EXE_PATH="`$HOME/anaconda3/bin/conda";
else
  echo "Conda was not found in WSL." >&2;
  exit 127;
fi;
CONDA_BASE="`$(`$CONDA_EXE_PATH info --base)";
source "`$CONDA_BASE/etc/profile.d/conda.sh";
cd $quotedDirectory;
exec conda run --no-capture-output -n $quotedEnvironment $Command
"@
}

function Start-WslService {
    param(
        [string]$Name,
        [string]$LinuxCommand
    )

    $escapedCommand = $LinuxCommand.Replace('"', '\"').Replace("`r", "").Replace("`n", " ")
    $argumentLine = "-d `"$WslDistro`" -- bash -lc `"$escapedCommand`""

    Write-Host "[$(Get-Date -Format HH:mm:ss)] Starting $Name..." -ForegroundColor Cyan
    return Start-Process `
        -FilePath "$env:SystemRoot\System32\wsl.exe" `
        -ArgumentList $argumentLine `
        -PassThru
}

function Start-Electron {
    Write-Host "[$(Get-Date -Format HH:mm:ss)] Starting Electron..." -ForegroundColor Cyan
    $env:NTU_EXTERNAL_FRONTEND = "1"
    return Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList "/d /c npm.cmd start" `
        -WorkingDirectory $ElectronDir `
        -PassThru
}

Write-Host "Checking WSL distribution: $WslDistro"
$distributionNames = & "$env:SystemRoot\System32\wsl.exe" --list --quiet |
    ForEach-Object { $_.Trim([char]0).Trim() } |
    Where-Object { $_ }

if ($distributionNames -notcontains $WslDistro) {
    throw "WSL distribution '$WslDistro' was not found. Available: $($distributionNames -join ', ')"
}

$WslProjectRoot = (& "$env:SystemRoot\System32\wsl.exe" -d $WslDistro -- wslpath -a -u $ProjectRoot).Trim()
if (-not $WslProjectRoot) {
    throw "Could not convert the project directory to a WSL path: $ProjectRoot"
}

Write-Host "Windows project: $ProjectRoot"
Write-Host "WSL project:     $WslProjectRoot"
Write-Host "Backend env:     $BackendCondaEnv"
Write-Host "Frontend env:    $FrontendCondaEnv"
Write-Host ""
Write-Host "Keep this supervisor open. Closed services restart automatically." -ForegroundColor Yellow
Write-Host ""

$vllmCommand = New-CondaCommand `
    -WorkingDirectory "$WslProjectRoot/backend" `
    -EnvironmentName $BackendCondaEnv `
    -Command "bash ./vllm_4bit.sh"

$apiCommand = New-CondaCommand `
    -WorkingDirectory "$WslProjectRoot/backend" `
    -EnvironmentName $BackendCondaEnv `
    -Command "python api.py"

$frontendCommand = New-CondaCommand `
    -WorkingDirectory "$WslProjectRoot/frontend" `
    -EnvironmentName $FrontendCondaEnv `
    -Command "python session_ui.py"

$vllmProcess = $null
$apiProcess = $null
$frontendProcess = $null
$electronProcess = $null
$lastVllmStart = [DateTime]::MinValue
$lastApiStart = [DateTime]::MinValue
$lastFrontendStart = [DateTime]::MinValue
$lastElectronStart = [DateTime]::MinValue

while ($true) {
    $now = Get-Date

    if ($vllmProcess -and $vllmProcess.HasExited) {
        Write-Host "vLLM exited with code $($vllmProcess.ExitCode). It will restart." -ForegroundColor Yellow
        $vllmProcess = $null
    }
    if (-not (Test-TcpPort 8080) -and -not $vllmProcess -and
        ($now - $lastVllmStart).TotalSeconds -ge $RestartDelaySeconds) {
        $vllmProcess = Start-WslService -Name "vLLM (port 8080)" -LinuxCommand $vllmCommand
        $lastVllmStart = $now
    }

    if ($apiProcess -and $apiProcess.HasExited) {
        Write-Host "OCR API exited with code $($apiProcess.ExitCode). It will restart." -ForegroundColor Yellow
        $apiProcess = $null
    }
    if ((Test-TcpPort 8080) -and -not (Test-TcpPort 8000) -and -not $apiProcess -and
        ($now - $lastApiStart).TotalSeconds -ge $RestartDelaySeconds) {
        $apiProcess = Start-WslService -Name "OCR API (port 8000)" -LinuxCommand $apiCommand
        $lastApiStart = $now
    }

    if ($frontendProcess -and $frontendProcess.HasExited) {
        Write-Host "Frontend exited with code $($frontendProcess.ExitCode). It will restart." -ForegroundColor Yellow
        $frontendProcess = $null
    }
    if ((Test-TcpPort 8000) -and -not (Test-TcpPort 8001) -and -not $frontendProcess -and
        ($now - $lastFrontendStart).TotalSeconds -ge $RestartDelaySeconds) {
        $frontendProcess = Start-WslService -Name "Frontend (port 8001)" -LinuxCommand $frontendCommand
        $lastFrontendStart = $now
    }

    if ($electronProcess -and $electronProcess.HasExited) {
        Write-Host "Electron closed. It will restart." -ForegroundColor Yellow
        $electronProcess = $null
    }
    if ((Test-TcpPort 8001) -and -not $electronProcess -and
        ($now - $lastElectronStart).TotalSeconds -ge $RestartDelaySeconds) {
        $electronProcess = Start-Electron
        $lastElectronStart = $now
    }

    Start-Sleep -Seconds 3
}
