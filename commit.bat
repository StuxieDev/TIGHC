@echo off
REM TIGHC Engine — Git commit script (Windows)
REM v3.9.14 — Add commit.bat/commit.sh

git add -A
git commit -m "chore(v3.9.14): add commit.bat/commit.sh — pre-written commit+tag scripts, rewritten with each commit's exact message/tag before being run — Version: v3.9.14"
git tag -a v3.9.14 -m "TIGHC Engine v3.9.14 — Add commit.bat/commit.sh"
