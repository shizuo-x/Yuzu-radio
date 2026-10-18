<#
.SYNOPSIS
  One-command deploy for the Yuzu radio bot — replaces the old
  "PuTTY-copy folder + docker compose up" flow.

.DESCRIPTION
  1. Runs the full test gate locally (fails fast — nothing is pushed on red).
  2. Pushes the current branch to the production server's repo over SSH
     (git transport — real history, no folder copying, no line-ending drift).
     The server has receive.denyCurrentBranch=updateInstead, so the push
     updates the checked-out deploy branch's working tree in place.
  3. Rebuilds + restarts the container on the server.
  4. Verifies the boot from live logs and prints a rollback line.

.EXAMPLE
  .\scripts\deploy.ps1                 # gate + push + rebuild + verify
  .\scripts\deploy.ps1 -SkipBuild      # push only (no container restart)
  .\scripts\deploy.ps1 -SkipGate       # push + rebuild, skip tests (emergencies)
#>
param(
    [string]$Branch       = "feature/yuzu-revamp",
    [string]$DeployBranch = "revamp-prod",
    [string]$Server       = "rock@192.168.1.3",
    [string]$ServerRepo   = "/home/rock/Yuzu-radio",
    [switch]$SkipGate,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot   # repo root = parent of scripts/
Set-Location $Repo
$PushUrl = "ssh://$Server$ServerRepo"

Write-Host "== 1/4 GATE ==" -ForegroundColor Cyan
if ($SkipGate) {
    Write-Host "skipped (-SkipGate)" -ForegroundColor Yellow
} else {
    $failed = $false
    foreach ($t in "test_stations.py", "test_playback_state.py", "test_ai_tool_loop.py", "test_station_browser.py") {
        python "tests\$t" | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host "FAIL  $t" -ForegroundColor Red; $failed = $true }
        else                     { Write-Host "ok    $t" -ForegroundColor Green }
    }
    if ($failed) { Write-Host "`nGate red - nothing was pushed." -ForegroundColor Red; exit 1 }
    python -m py_compile core/stations.py cogs/playback.py cogs/utility.py cogs/ai_assistant.py config.py
    if ($LASTEXITCODE -ne 0) { Write-Host "py_compile FAILED" -ForegroundColor Red; exit 1 }
}

Write-Host "`n== 2/4 PUSH (local $Branch -> $DeployBranch on server) ==" -ForegroundColor Cyan
git push $PushUrl "${Branch}:refs/heads/$DeployBranch"
if ($LASTEXITCODE -ne 0) { Write-Host "push failed." -ForegroundColor Red; exit 1 }

if ($SkipBuild) {
    Write-Host "`nPushed. -SkipBuild given: container NOT restarted." -ForegroundColor Yellow
    exit 0
}

Write-Host "`n== 3/4 REBUILD + RESTART on server ==" -ForegroundColor Cyan
ssh -o BatchMode=yes $Server "cd $ServerRepo && docker compose up -d --build 2>&1 | tail -5 && sleep 20"

Write-Host "`n== 4/4 VERIFY (live boot logs) ==" -ForegroundColor Cyan
$verify = ssh -o BatchMode=yes $Server "cd $ServerRepo && docker ps --filter name=yuzu-discord-bot --format '{{.Status}}' && docker logs --since 3m yuzu-discord-bot 2>&1 | grep -E 'PocketBase|Loaded [0-9]+ cogs|Logged in|Synced|ERROR|Traceback' | tail -10"
$verify

$ok = ($verify -match "Logged in") -and -not ($verify -match "Traceback")
if ($ok) {
    $sha = git rev-parse --short HEAD
    Write-Host "`nDEPLOY OK  ($Branch @ $sha -> $DeployBranch)" -ForegroundColor Green
    Write-Host "Rollback:  ssh $Server `"cd $ServerRepo && git checkout main && docker compose up -d --build`""
} else {
    Write-Host "`nVERIFY FAILED - inspect logs: ssh $Server `"docker logs yuzu-discord-bot --tail 100`"" -ForegroundColor Red
    exit 1
}
