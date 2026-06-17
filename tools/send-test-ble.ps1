param(
  [string]$DeviceName = "Wio AI Quota",
  [string]$ServiceUuid = "6e400001-b5a3-f393-e0a9-e50e24dcca9e",
  [string]$RxUuid = "6e400002-b5a3-f393-e0a9-e50e24dcca9e",
  [string]$TxUuid = "6e400003-b5a3-f393-e0a9-e50e24dcca9e",
  [string]$PayloadPath = "",
  [switch]$ListOnly,
  [switch]$ListAllBle
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Runtime.WindowsRuntime

function Await-WinRtOperation {
  param(
    [Parameter(Mandatory = $true)] $Operation,
    [Parameter(Mandatory = $true)] [Type] $ResultType
  )

  $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
      $_.Name -eq "AsTask" -and
      $_.IsGenericMethodDefinition -and
      $_.GetParameters().Count -eq 1 -and
      $_.ToString().Contains("IAsyncOperation") -and
      -not $_.ToString().Contains("WithProgress")
    } |
    Select-Object -First 1

  if ($null -eq $method) {
    throw "Cannot find WindowsRuntimeSystemExtensions.AsTask(IAsyncOperation<TResult>)."
  }

  $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
  $task.Wait()
  return $task.Result
}

function Get-DefaultPayload {
  return @'
{"u":"12:34","f":{"c":"TEST $1","k":"9.99TToken","t":"12:34"},"p":{"c":{"r":23,"s":{"l":"5h","p":23,"z":"12:50"},"w":{"l":"7d 45%","p":45,"z":"6/10 09:00"}},"x":{"r":78,"s":{"l":"5h","p":78,"z":"13:10"},"w":{"l":"7d 66%","p":66,"z":"6/10 11:40"}}}}
'@
}

$serviceGuid = [Guid]$ServiceUuid
$rxGuid = [Guid]$RxUuid
$txGuid = [Guid]$TxUuid

$gattServiceType = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceService, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
$bluetoothLeDeviceType = [Windows.Devices.Bluetooth.BluetoothLEDevice, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
$deviceInfoType = [Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
$deviceInfoCollectionType = [Windows.Devices.Enumeration.DeviceInformationCollection, Windows.Devices.Enumeration, ContentType = WindowsRuntime]
$characteristicsResultType = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
$writeResultType = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattWriteResult, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
$writeOptionType = [Windows.Devices.Bluetooth.GenericAttributeProfile.GattWriteOption, Windows.Devices.Bluetooth, ContentType = WindowsRuntime]
$cryptoBufferType = [Windows.Security.Cryptography.CryptographicBuffer, Windows.Security.Cryptography, ContentType = WindowsRuntime]
$binaryEncodingType = [Windows.Security.Cryptography.BinaryStringEncoding, Windows.Security.Cryptography, ContentType = WindowsRuntime]

if ($ListAllBle) {
  Write-Host "Scanning all cached/visible Bluetooth LE devices ..."
  $allSelector = $bluetoothLeDeviceType::GetDeviceSelector()
  $allDevices = Await-WinRtOperation -Operation ($deviceInfoType::FindAllAsync($allSelector)) -ResultType $deviceInfoCollectionType
  if ($allDevices.Count -eq 0) {
    Write-Host "No Bluetooth LE devices were visible to Windows."
    exit 0
  }
  for ($i = 0; $i -lt $allDevices.Count; $i++) {
    Write-Host ("[{0}] {1}  {2}" -f $i, $allDevices[$i].Name, $allDevices[$i].Id)
  }
  exit 0
}

Write-Host "Scanning BLE GATT service $ServiceUuid ..."
$selector = $gattServiceType::GetDeviceSelectorFromUuid($serviceGuid)
$devices = Await-WinRtOperation -Operation ($deviceInfoType::FindAllAsync($selector)) -ResultType $deviceInfoCollectionType

if ($devices.Count -eq 0) {
  throw "No BLE device advertising service $ServiceUuid was found. Make sure Wio Terminal is powered on and running the firmware."
}

Write-Host "Discovered devices:"
for ($i = 0; $i -lt $devices.Count; $i++) {
  Write-Host ("[{0}] {1}  {2}" -f $i, $devices[$i].Name, $devices[$i].Id)
}

if ($ListOnly) {
  exit 0
}

$device = $devices | Where-Object { $_.Name -like "*$DeviceName*" } | Select-Object -First 1
if ($null -eq $device) {
  $device = $devices[0]
  Write-Warning "No device named '$DeviceName' was found. Using first discovered device: $($device.Name)"
}

Write-Host "Opening GATT service on: $($device.Name)"
$service = Await-WinRtOperation -Operation ($gattServiceType::FromIdAsync($device.Id)) -ResultType $gattServiceType
if ($null -eq $service) {
  throw "Failed to open GATT service. Try pairing the device in Windows Bluetooth settings, then run this script again."
}
Write-Host "GATT service opened."

$charResult = Await-WinRtOperation -Operation ($service.GetCharacteristicsForUuidAsync($rxGuid)) -ResultType $characteristicsResultType
if ($charResult.Characteristics.Count -eq 0) {
  throw "RX characteristic $RxUuid was not found on $($device.Name)."
}

$rx = $charResult.Characteristics[0]
Write-Host "RX characteristic found: $RxUuid"

$txResult = Await-WinRtOperation -Operation ($service.GetCharacteristicsForUuidAsync($txGuid)) -ResultType $characteristicsResultType
if ($txResult.Characteristics.Count -gt 0) {
  Write-Host "TX notify characteristic found: $TxUuid"
} else {
  Write-Warning "TX notify characteristic was not found. Writes can still be tested, but no device acknowledgment will be received."
}

if ($PayloadPath -and (Test-Path -LiteralPath $PayloadPath)) {
  $payload = Get-Content -LiteralPath $PayloadPath -Raw
} else {
  $payload = Get-DefaultPayload
}

if (-not $payload.EndsWith("`n")) {
  $payload += "`n"
}

Write-Host "Sending payload:"
Write-Host $payload

$chunkSize = 100
for ($offset = 0; $offset -lt $payload.Length; $offset += $chunkSize) {
  $length = [Math]::Min($chunkSize, $payload.Length - $offset)
  $chunk = $payload.Substring($offset, $length)
  $buffer = $cryptoBufferType::ConvertStringToBinary($chunk, $binaryEncodingType::Utf8)

  try {
    Write-Host ("Writing chunk offset={0} bytes={1} without response" -f $offset, $length)
    $result = Await-WinRtOperation -Operation ($rx.WriteValueWithResultAsync($buffer, $writeOptionType::WriteWithoutResponse)) -ResultType $writeResultType
  } catch {
    Write-Host ("WriteWithoutResponse failed: {0}" -f $_.Exception.Message)
    Write-Host ("Writing chunk offset={0} bytes={1} with response" -f $offset, $length)
    $result = Await-WinRtOperation -Operation ($rx.WriteValueWithResultAsync($buffer, $writeOptionType::WriteWithResponse)) -ResultType $writeResultType
  }

  Write-Host "Write status: $($result.Status)"
  if ($result.Status.ToString() -ne "Success") {
    throw "BLE write failed: $($result.Status)"
  }
}

Write-Host "BLE test payload sent successfully."
