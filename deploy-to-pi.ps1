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

# 2. SCP transfer files across local WiFi
Write-Host "[2/3] Transferring complete offline bundle to ${PiUser}@${PiIP}:~/flashforge-webserver..." -ForegroundColor Yellow
$localDir = $PSScriptRoot

# Exclude git objects if not needed to make transfer even faster
& scp -r -O $localDir "${PiUser}@${PiIP}:~/flashforge-webserver"

if ($LASTEXITCODE -ne 0) {
    Write-Error "SCP file transfer failed! Please verify SSH is enabled on the Pi and IP is correct."
    exit 1
}

# 3. Trigger offline installer via SSH
Write-Host "[3/3] Executing 100% offline installation on the Pi..." -ForegroundColor Yellow
& ssh "${PiUser}@${PiIP}" "cd ~/flashforge-webserver && chmod +x scripts/*.sh && sudo ./scripts/install-offline.sh"

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n============================================================" -ForegroundColor Green
    Write-Host " SUCCESS: Server is installed and running on your Pi Zero W!" -ForegroundColor Green
    Write-Host " Access Web UI at: http://${PiIP}:8080" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
} else {
    Write-Warning "Installation encountered a warning or error. Check output above."
}
