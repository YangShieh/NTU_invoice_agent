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
$WslExecutable = Join-Path $env:SystemRoot "System32\wsl.exe"
$SupervisorLogDirectory = Join-Path $ProjectRoot "logs\supervisor"

New-Item -ItemType Directory -Path $SupervisorLogDirectory -Force | Out-Null

if (-not (Test-Path -LiteralPath $WslExecutable)) {
    throw "wsl.exe was not found: $WslExecutable"
}

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
    $quote = "'"
    $replacement = "'\''"
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
        [string]$LinuxCommand,
        [string]$LogName
    )

    # Encode the complete Linux command so Windows, PowerShell, WSL and Bash
    # cannot reinterpret its quotes, dollar signs or backslashes in transit.
    $commandBytes = [System.Text.Encoding]::UTF8.GetBytes($LinuxCommand)
    $encodedCommand = [Convert]::ToBase64String($commandBytes)
    $runnerCommand = "printf '%s' '$encodedCommand' | base64 -d | bash"
    # The distro name has no spaces. Do not embed quote characters around it:
    # Windows PowerShell 5.1 Start-Process can pass those quotes literally.
    $argumentLine = "-d $WslDistro --exec bash -lc `"$runnerCommand`""

    $stdoutLog = Join-Path $SupervisorLogDirectory "$LogName.stdout.log"
    $stderrLog = Join-Path $SupervisorLogDirectory "$LogName.stderr.log"

    Write-Host "[$(Get-Date -Format HH:mm:ss)] Starting $Name..." -ForegroundColor Cyan
    Write-Host "  stdout: $stdoutLog" -ForegroundColor DarkGray
    Write-Host "  stderr: $stderrLog" -ForegroundColor DarkGray
    return Start-Process `
        -FilePath $WslExecutable `
        -ArgumentList $argumentLine `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru
}

function Show-ServiceLogTail {
    param(
        [string]$Name,
        [string]$LogName
    )

    $stdoutLog = Join-Path $SupervisorLogDirectory "$LogName.stdout.log"
    $stderrLog = Join-Path $SupervisorLogDirectory "$LogName.stderr.log"

    Write-Host "----- $Name stderr (last 30 lines) -----" -ForegroundColor DarkYellow
    if (Test-Path -LiteralPath $stderrLog) {
        Get-Content -LiteralPath $stderrLog -Tail 30
    }
    else {
        Write-Host "No stderr log was created."
    }

    Write-Host "----- $Name stdout (last 30 lines) -----" -ForegroundColor DarkYellow
    if (Test-Path -LiteralPath $stdoutLog) {
        Get-Content -LiteralPath $stdoutLog -Tail 30
    }
    else {
        Write-Host "No stdout log was created."
    }

    Write-Host "----------------------------------------" -ForegroundColor DarkYellow
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
$distributionNames = & $WslExecutable --list --quiet |
    ForEach-Object { $_.Trim([char]0).Trim() } |
    Where-Object { $_ }

if ($distributionNames -notcontains $WslDistro) {
    throw "WSL distribution '$WslDistro' was not found. Available: $($distributionNames -join ', ')"
}

$wslPathOutput = @(
    & $WslExecutable `
        -d $WslDistro `
        --exec /usr/bin/wslpath `
        -a `
        -u `
        $ProjectRoot 2>&1
)
$wslPathExitCode = $LASTEXITCODE
$WslProjectRoot = ($wslPathOutput -join [Environment]::NewLine).Trim()

if ($wslPathExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($WslProjectRoot)) {
    $wslPathDetails = ($wslPathOutput -join " ").Trim()
    throw (
        "Could not convert the project directory to a WSL path. " +
        "Windows path: '$ProjectRoot'. " +
        "wslpath exit code: $wslPathExitCode. " +
        "Output: $wslPathDetails"
    )
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
        Show-ServiceLogTail -Name "vLLM" -LogName "vllm"
        $vllmProcess = $null
    }
    if (-not (Test-TcpPort 8080) -and -not $vllmProcess -and
        ($now - $lastVllmStart).TotalSeconds -ge $RestartDelaySeconds) {
        $vllmProcess = Start-WslService `
            -Name "vLLM (port 8080)" `
            -LinuxCommand $vllmCommand `
            -LogName "vllm"
        $lastVllmStart = $now
    }

    if ($apiProcess -and $apiProcess.HasExited) {
        Write-Host "OCR API exited with code $($apiProcess.ExitCode). It will restart." -ForegroundColor Yellow
        Show-ServiceLogTail -Name "OCR API" -LogName "api"
        $apiProcess = $null
    }
    if ((Test-TcpPort 8080) -and -not (Test-TcpPort 8000) -and -not $apiProcess -and
        ($now - $lastApiStart).TotalSeconds -ge $RestartDelaySeconds) {
        $apiProcess = Start-WslService `
            -Name "OCR API (port 8000)" `
            -LinuxCommand $apiCommand `
            -LogName "api"
        $lastApiStart = $now
    }

    if ($frontendProcess -and $frontendProcess.HasExited) {
        Write-Host "Frontend exited with code $($frontendProcess.ExitCode). It will restart." -ForegroundColor Yellow
        Show-ServiceLogTail -Name "Frontend" -LogName "frontend"
        $frontendProcess = $null
    }
    if ((Test-TcpPort 8000) -and -not (Test-TcpPort 8001) -and -not $frontendProcess -and
        ($now - $lastFrontendStart).TotalSeconds -ge $RestartDelaySeconds) {
        $frontendProcess = Start-WslService `
            -Name "Frontend (port 8001)" `
            -LinuxCommand $frontendCommand `
            -LogName "frontend"
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
