# Restores a snapshot made by scripts/snapshot_export.sh. REPLACES this install's
# database and files. Run from the repo root in PowerShell, with the snapshot
# folder copied to .\snapshot and the app started once (docker compose up --build -d):
#   powershell -ExecutionPolicy Bypass -File scripts\snapshot_import.ps1
# Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts in the
# local code page, so non-ASCII text breaks parsing.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

foreach ($f in "snapshot\db.dump", "snapshot\data.tgz") {
    if (-not (Test-Path $f)) { throw "Missing $f - copy the snapshot folder into the project root first." }
}
if (Test-Path "snapshot\commit.txt") {
    # Best effort: a ZIP download has no git history, so skip the check then.
    $want = (Get-Content "snapshot\commit.txt").Trim()
    $have = $null
    try { $have = (& git rev-parse HEAD 2>$null) } catch { }
    if ($have) {
        $have = "$have".Trim()
        if ($want -ne $have) { Write-Warning "Code version differs (snapshot: $($want.Substring(0,8)), here: $($have.Substring(0,8))). Update the code before importing." }
    } else {
        Write-Host "Cannot check the code version (no git). The snapshot was made from commit $($want.Substring(0,8)); make sure this code is that version or newer."
    }
}

Write-Host "-> stopping the app (database stays up)"
docker compose stop backend documents-worker tasks-worker scheduler frontend
docker compose up -d postgres
Start-Sleep -Seconds 5

Write-Host "-> restoring database"
docker compose cp snapshot\db.dump postgres:/tmp/db.dump
docker compose exec -T postgres pg_restore -U nhi_ai -d nhi_ai --clean --if-exists --no-owner /tmp/db.dump
if ($LASTEXITCODE -gt 1) { throw "pg_restore failed ($LASTEXITCODE)" }
docker compose exec -T postgres rm -f /tmp/db.dump

Write-Host "-> restoring files"
docker compose run -d --no-deps --name nhi-ai-restore --entrypoint sleep backend 600 | Out-Null
try {
    docker cp snapshot\data.tgz nhi-ai-restore:/tmp/data.tgz
    docker exec nhi-ai-restore sh -c "find /data -mindepth 1 -delete && tar xzf /tmp/data.tgz -C /data && rm -f /tmp/data.tgz"
} finally {
    docker rm -f nhi-ai-restore | Out-Null
}

Write-Host "-> starting the app"
docker compose up -d
Write-Host "Done. Open http://localhost:3000"
