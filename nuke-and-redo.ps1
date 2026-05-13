# =============================================================
# TopicForge - Nuke & Redo: fresh git history, single commit
# =============================================================
# Run from: C:\Users\yanis\Documents\Projects\TopicForge
# Prerequisite: the GitHub repo yaniswav/TopicForge has been
# DELETED and RE-CREATED EMPTY (no README, no .gitignore).
# =============================================================

$ErrorActionPreference = "Stop"
Set-Location "C:\Users\yanis\Documents\Projects\TopicForge"

Write-Host ""
Write-Host "=== 1. Safety check: backup must exist ===" -ForegroundColor Cyan
if (-not (Test-Path ".\.git-backup\HEAD")) {
    Write-Error "Safety backup not found at .\.git-backup\ - aborting"
    exit 1
}
Write-Host "Backup found: $(Resolve-Path .\.git-backup)"

Write-Host ""
Write-Host "=== 2. Remove old .git (full nuke) ===" -ForegroundColor Cyan
if (Test-Path .git) {
    # -Force needed for read-only files inside .git
    Remove-Item -Recurse -Force .git
    Write-Host ".git removed"
} else {
    Write-Host "No .git folder present, skipping"
}

Write-Host ""
Write-Host "=== 3. Fresh init ===" -ForegroundColor Cyan
git init -b main
git config user.name "yaniswav"
git config user.email "azellaxmc@gmail.com"

Write-Host ""
Write-Host "=== 4. Make sure .git-backup is NOT tracked ===" -ForegroundColor Cyan
# Use local exclude so .gitignore stays clean for the public repo
Add-Content -Path .git\info\exclude -Value "`n# Local safety backup - delete after successful push`n.git-backup/"
Write-Host ".git-backup/ added to .git/info/exclude"

Write-Host ""
Write-Host "=== 5. Stage all files ===" -ForegroundColor Cyan
git add -A

Write-Host ""
Write-Host "=== 6. Sanity check: no Co-Authored-By in any tracked file ===" -ForegroundColor Cyan
# Filter out the script itself and the .git-backup folder
$hits = git grep -l "Co-Authored-By" 2>$null | Where-Object { $_ -notmatch "nuke-and-redo.ps1$" }
if ($hits) {
    Write-Warning "Found Co-Authored-By in tracked files:"
    $hits | ForEach-Object { Write-Warning "  $_" }
    Write-Host "Aborting - clean these before committing." -ForegroundColor Red
    exit 1
} else {
    Write-Host "Clean. No Co-Authored-By in any tracked file."
}

Write-Host ""
Write-Host "=== 7. Single commit (NO trailer) ===" -ForegroundColor Cyan
$msg = @"
Initial release v0.1.0

TopicForge - ROS Topic Inspector & Bag Analyzer MCP.

Production-minded Python MCP server that lets AI agents inspect ROS2
topics and analyze ROS2 bag files through a small, well-typed tool surface.

MVP exposes five read-only MCP tools:
- health_check
- list_topics
- get_topic_info
- sample_messages
- analyze_bag

Three runtime modes: mock, live, auto (default).
"@
git commit -m $msg

Write-Host ""
Write-Host "=== 8. Tag v0.1.0 ===" -ForegroundColor Cyan
git tag -a v0.1.0 -m "v0.1.0 - Initial release"

Write-Host ""
Write-Host "=== 9. Add remote ===" -ForegroundColor Cyan
git remote add origin https://github.com/yaniswav/TopicForge.git

Write-Host ""
Write-Host "=== 10. Verify ===" -ForegroundColor Cyan
git log --pretty=fuller
Write-Host ""
git tag -l
Write-Host ""
git remote -v
Write-Host ""
Write-Host "Files staged in commit:"
git ls-tree -r HEAD --name-only | Measure-Object | Select-Object -ExpandProperty Count
Write-Host "(should be around 50, NOT include .git-backup/)"

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host " READY TO PUSH. Run these two commands when you're happy:" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "   git push -u origin main"
Write-Host "   git push --tags"
Write-Host ""
Write-Host "After push: recreate the GitHub Environment 'pypi' with:" -ForegroundColor Yellow
Write-Host "   - Required reviewer: yaniswav"
Write-Host "   - Deployment tag rule: v*"
Write-Host ""
Write-Host "Once the push is confirmed working, you can delete:" -ForegroundColor DarkGray
Write-Host "   Remove-Item -Recurse -Force .\.git-backup"
Write-Host "   Remove-Item .\nuke-and-redo.ps1"
Write-Host ""
