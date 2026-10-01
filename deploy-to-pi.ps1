# deploy-to-pi.ps1 - 1-Click transfer & installation to Pi Zero W across local Wi-Fi
# Usage: .\deploy-to-pi.ps1 -PiIP 192.168.1.50 -PiUser pi

param(
    [Parameter(Mandatory=$false, Position=0)]
    [string]$PiIP = "raspberrypi.local",

    [Parameter(Mandatory=$false)]
    [string]$PiUser = "pi"
)

$ErrorActionPreference = "Stop"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Deploying FlashForge WebServer to Pi Zero W ($PiIP)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Test ping
Write-Host "[1/3] Checking connectivity to $PiIP..." -ForegroundColor Yellow
if (!(Test-Connection -ComputerName $PiIP -Count 1 -Quiet)) {
    Write-Warning "Could not ping $PiIP directly, but attempting SSH connection anyway..."
}

# 2. Package and SCP transfer archive across local WiFi (fast & avoids .git permission issues)
Write-Host "[2/3] Packaging clean offline bundle (excluding .git) and transferring to ${PiUser}@${PiIP}..." -ForegroundColor Yellow
$localDir = $PSScriptRoot
$archivePath = Join-Path $env:TEMP "ffcp-bundle.tar.gz"

if (Test-Path $archivePath) {
    Remove-Item $archivePath -Force
}

# Package using Windows built-in tar.exe
& tar.exe -czf $archivePath --exclude=".git" --exclude="__pycache__" -C $localDir .

if (!(Test-Path $archivePath)) {
    Write-Error "Failed to create deployment archive."
    exit 1
}

# Transfer single compressed archive to /tmp on the Pi
& scp -O $archivePath "${PiUser}@${PiIP}:/tmp/ffcp-bundle.tar.gz"
$scpExit = $LASTEXITCODE
Remove-Item $archivePath -Force -ErrorAction SilentlyContinue

if ($scpExit -ne 0) {
    Write-Error "SCP file transfer failed! Please verify SSH is enabled on the Pi and IP is correct."
    exit 1
}

# 3. Clean unpack & trigger offline installer via SSH
Write-Host "[3/3] Unpacking bundle and executing 100% offline installation on the Pi..." -ForegroundColor Yellow
$remoteCmd = "sudo rm -rf ~/flashforge-webserver && mkdir -p ~/flashforge-webserver && tar -xzf /tmp/ffcp-bundle.tar.gz -C ~/flashforge-webserver && rm -f /tmp/ffcp-bundle.tar.gz && cd ~/flashforge-webserver && chmod +x scripts/*.sh && sudo ./scripts/install-offline.sh"

& ssh -t "${PiUser}@${PiIP}" $remoteCmd

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n============================================================" -ForegroundColor Green
    Write-Host " SUCCESS: Server is installed and running on your Pi Zero W!" -ForegroundColor Green
    Write-Host " Access Web UI at: http://${PiIP}:8080" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
} else {
    Write-Warning "Installation encountered a warning or error. Check output above."
}
