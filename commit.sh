#!/bin/bash
# TIGHC Engine — Git commit script
# v3.9.14 — Add commit.bat/commit.sh

git add -A
git commit -m "chore(v3.9.14): add commit.bat/commit.sh

Pre-written commit+tag scripts (Windows/Unix), rewritten with each
commit's exact message/tag before being run - keeps multi-line commit
messages consistent across shells and leaves a record of exactly what
each commit and its tag said.

Version: v3.9.14"

git tag -a v3.9.14 -m "TIGHC Engine v3.9.14 — Add commit.bat/commit.sh"
