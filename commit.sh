#!/bin/bash
# TIGHC Engine — Git commit + tag script
# Commits whatever's staged/unstaged and tags it with the version currently
# in VERSION.md, read dynamically so this script never goes stale the way a
# hardcoded version number does.
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$DIR/VERSION.md")"

git add -A
if ! git diff --cached --quiet; then
    git commit -m "$(cat <<EOF
Release v${VERSION}

See CHANGELOG.md for details.
EOF
)"
else
    echo "Nothing to commit — tagging the current HEAD as v${VERSION}."
fi

if git rev-parse "v${VERSION}" >/dev/null 2>&1; then
    echo "Tag v${VERSION} already exists — skipping."
else
    git tag -a "v${VERSION}" -m "TIGHC Engine v${VERSION}"
    echo "Tagged v${VERSION}."
fi

echo "Push with: git push origin main --tags"
