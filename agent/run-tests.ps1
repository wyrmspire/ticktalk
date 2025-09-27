# run-tests.ps1
param (
    [string]$AskQuery = "Describe a breakout failure pattern for MES 5m in trader lingo"
)
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logFile = "C:\ticktalk\agent\test-logs-$timestamp.txt"
$tempOut = "C:\ticktalk\agent\temp-stdout-$timestamp.txt"
$tempErr = "C:\ticktalk\agent\temp-stderr-$timestamp.txt"
Write-Host "Starting functions-framework, logging to $logFile..."

# Initialize log file
New-Item -Path $logFile -ItemType File -Force | Out-Null

# Start emulator
$process = Start-Process -FilePath "functions-framework" -ArgumentList "--target handler --port 8081" -RedirectStandardOutput $tempOut -RedirectStandardError $tempErr -PassThru -NoNewWindow

# Wait for server
Start-Sleep -Seconds 5

# Combine initial logs
if (Test-Path $tempOut) { Get-Content $tempOut | Out-File $logFile -Append }
if (Test-Path $tempErr) { Get-Content $tempErr | Out-File $logFile -Append }

# Test /bars
Write-Host "`nTesting /bars endpoint..."
Add-Content -Path $logFile -Value "`n=== Testing /bars endpoint ==="
try {
    $barsResult = Invoke-WebRequest -Uri "http://localhost:8081/bars?symbol=MES&tf=5m&start=2025-09-01T00:00:00Z&end=2025-09-02T00:00:00Z" | Select-Object StatusCode, Content | Out-String
    Add-Content -Path $logFile -Value $barsResult
    Write-Host $barsResult
} catch {
    $errorMsg = "Error testing /bars: $_"
    Add-Content -Path $logFile -Value $errorMsg
    Write-Host $errorMsg
}

# Test /journal
Write-Host "`nTesting /journal endpoint..."
Add-Content -Path $logFile -Value "`n=== Testing /journal endpoint ==="
try {
    $journalResult = Invoke-WebRequest -Uri "http://localhost:8081/journal" -Method POST -ContentType "application/json" -Body '{"timestamp":"2025-09-01T18:00:00Z","symbol":"MES","timeframe":"5m","entry":6532.0,"stop":6527.0,"target":6542.0,"rr":2.0,"notes":"Swing high rejection"}' | Select-Object StatusCode, Content | Out-String
    Add-Content -Path $logFile -Value $journalResult
    Write-Host $journalResult
} catch {
    $errorMsg = "Error testing /journal: $_"
    Add-Content -Path $logFile -Value $errorMsg
    Write-Host $errorMsg
}

# Test /ask
Write-Host "`nTesting /ask endpoint with query: $AskQuery..."
Add-Content -Path $logFile -Value "`n=== Testing /ask endpoint with query: $AskQuery ==="
try {
    $askResult = Invoke-WebRequest -Uri "http://localhost:8081/ask" -Method POST -ContentType "application/json" -Body (@"{"query":"$AskQuery"}"@ | ConvertTo-Json) | Select-Object StatusCode, Content | Out-String
    Add-Content -Path $logFile -Value $askResult
    Write-Host $askResult
} catch {
    $errorMsg = "Error testing /ask: $_"
    Add-Content -Path $logFile -Value $errorMsg
    Write-Host $errorMsg
}

# Open log file in Notepad
Write-Host "`nOpening logs in Notepad..."
Start-Process notepad $logFile

# Clean up temp files
Remove-Item $tempOut -Force -ErrorAction SilentlyContinue
Remove-Item $tempErr -Force -ErrorAction SilentlyContinue

Write-Host "`nKeeping server running. Press Ctrl+C to stop."
while ($true) { Start-Sleep -Seconds 1 }