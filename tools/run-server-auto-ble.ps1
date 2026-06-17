param(
  [int]$Port = 8765,
  [string]$DeviceName = "Wio AI Quota",
  [int]$Interval = 10
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Set-Location $root
Write-Host "Starting Python server with BLE auto-sync ..."
Write-Host "URL: http://127.0.0.1:$Port/preview.html"
Write-Host "BLE device: $DeviceName"

python .\server.py $Port --ble --ble-name $DeviceName --ble-interval $Interval
