# Starts the whole app with Docker Compose and opens it in the browser. Meant to be
# run by double-clicking "Start NHI-AI.bat" in the repo root; the only prerequisite
# is Docker Desktop. On the first run it creates .env and backend\.env from the
# examples and asks for the OpenAI API key.
# Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts in the
# local code page, so non-ASCII text breaks parsing.
Set-Location (Join-Path $PSScriptRoot "..")
$AppUrl = "http://localhost:3000"

function Fail($msg) {
    Write-Host ""
    Write-Host "ERROR: $msg" -ForegroundColor Red
    exit 1
}

# Double-clicking the .bat inside a ZIP runs a throw-away copy in the temp folder,
# so the saved API key (and every later change) is lost on the next run.
$here = (Get-Location).Path
if ($here.StartsWith([System.IO.Path]::GetTempPath(), [System.StringComparison]::OrdinalIgnoreCase)) {
    Fail "This is running from a temporary folder ($here). Right-click the ZIP, choose 'Extract All...', and run 'Start NHI-AI.bat' from the extracted folder."
}

# Native commands write progress to stderr; keep the exit code as the only signal.
function Test-Docker {
    try { docker info *> $null } catch { return $false }
    return ($LASTEXITCODE -eq 0)
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker Desktop is not installed. Install it from https://www.docker.com/products/docker-desktop/ and run this again."
}

if (-not (Test-Docker)) {
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $desktop) {
        Write-Host "-> starting Docker Desktop (this can take a minute)"
        Start-Process $desktop
    } else {
        Write-Host "Please open Docker Desktop now."
    }
    $deadline = (Get-Date).AddMinutes(3)
    while (-not (Test-Docker)) {
        if ((Get-Date) -gt $deadline) { Fail "Docker Desktop did not start. Open it, wait until it says it is running, and try again." }
        Start-Sleep -Seconds 3
    }
}

# Env files: UTF-8 without BOM, so Compose and python-dotenv read the first key correctly.
$utf8 = New-Object System.Text.UTF8Encoding $false
foreach ($f in ".env", "backend\.env") {
    if (-not (Test-Path $f)) {
        Write-Host "-> creating $f"
        Copy-Item "$f.example" $f
    }
}
$envPath = (Resolve-Path "backend\.env").Path
$lines = [System.IO.File]::ReadAllLines($envPath)
$keyLine = $lines | Where-Object { $_ -match '^OPENAI_API_KEY=' } | Select-Object -First 1
if (-not $keyLine -or $keyLine -match '^OPENAI_API_KEY=(replace-me)?\s*$') {
    Write-Host ""
    $key = (Read-Host "Paste the OpenAI API key (ask the team if you do not have it)").Trim()
    if (-not $key) { Fail "No API key entered." }
    if ($keyLine) {
        $lines = $lines | ForEach-Object { if ($_ -match '^OPENAI_API_KEY=') { "OPENAI_API_KEY=$key" } else { $_ } }
    } else {
        $lines = @($lines) + "OPENAI_API_KEY=$key"
    }
    [System.IO.File]::WriteAllLines($envPath, [string[]]$lines, $utf8)
    Write-Host "-> saved the key to $envPath"
} else {
    Write-Host "-> using the API key in $envPath"
}

Write-Host ""
Write-Host "-> building and starting the app (the first run can take 10+ minutes)"
docker compose up --build -d
if ($LASTEXITCODE -ne 0) { Fail "Starting the app failed. Send a screenshot of this window to the team." }

Write-Host "-> waiting for the app to answer"
$deadline = (Get-Date).AddMinutes(5)
while ($true) {
    try {
        Invoke-WebRequest $AppUrl -UseBasicParsing -TimeoutSec 5 | Out-Null
        break
    } catch {
        if ((Get-Date) -gt $deadline) { Fail "The app did not come up in time. Send a screenshot of this window to the team." }
        Start-Sleep -Seconds 3
    }
}

Start-Process $AppUrl
Write-Host ""
Write-Host "Done. The app is open at $AppUrl" -ForegroundColor Green
Write-Host "It keeps running in the background; use 'Stop NHI-AI.bat' to stop it."
