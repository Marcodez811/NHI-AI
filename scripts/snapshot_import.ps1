# Restores a snapshot made by scripts/snapshot_export.sh. REPLACES this install's
# database and files. Run from the repo root in PowerShell, with the snapshot
# folder copied to .\snapshot:
#   powershell -ExecutionPolicy Bypass -File scripts\snapshot_import.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

foreach ($f in "snapshot\db.dump", "snapshot\data.tgz") {
    if (-not (Test-Path $f)) { throw "找不到 $f，請先把 snapshot 資料夾複製到專案根目錄。" }
}
if (Test-Path "snapshot\commit.txt") {
    $want = (Get-Content "snapshot\commit.txt").Trim()
    $have = (git rev-parse HEAD).Trim()
    if ($want -ne $have) { Write-Warning "程式碼版本不同（snapshot: $($want.Substring(0,8))，這裡: $($have.Substring(0,8))）。建議先 git checkout $want 再匯入。" }
}

Write-Host "→ stopping the app (database stays up)"
docker compose stop backend documents-worker tasks-worker scheduler frontend
docker compose up -d postgres
Start-Sleep -Seconds 5

Write-Host "→ restoring database"
docker compose cp snapshot\db.dump postgres:/tmp/db.dump
docker compose exec -T postgres pg_restore -U nhi_ai -d nhi_ai --clean --if-exists --no-owner /tmp/db.dump
if ($LASTEXITCODE -gt 1) { throw "pg_restore failed ($LASTEXITCODE)" }
docker compose exec -T postgres rm -f /tmp/db.dump

Write-Host "→ restoring files"
docker compose run -d --no-deps --name nhi-ai-restore --entrypoint sleep backend 600 | Out-Null
try {
    docker cp snapshot\data.tgz nhi-ai-restore:/tmp/data.tgz
    docker exec nhi-ai-restore sh -c "find /data -mindepth 1 -delete && tar xzf /tmp/data.tgz -C /data && rm -f /tmp/data.tgz"
} finally {
    docker rm -f nhi-ai-restore | Out-Null
}

Write-Host "→ starting the app"
docker compose up -d
Write-Host "✓ 匯入完成。開啟 http://localhost:3000"
