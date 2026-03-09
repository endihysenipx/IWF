param(
    [int]$Count = 2,
    [string]$WorkingDirectory = "C:\Users\Admin\Documents\GitHub\IWF"
)

$ErrorActionPreference = "Stop"

if ($Count -lt 1) {
    throw "Count must be at least 1."
}

if (-not (Test-Path -LiteralPath $WorkingDirectory)) {
    throw "Working directory not found: $WorkingDirectory"
}

for ($i = 1; $i -le $Count; $i++) {
    Start-Process powershell `
        -ArgumentList @(
            "-NoExit",
            "-Command",
            "Set-Location '$WorkingDirectory'; .\venv\Scripts\python worker.py"
        ) `
        -WorkingDirectory $WorkingDirectory

    Write-Host "Started worker $i"
}
