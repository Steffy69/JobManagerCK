#!/usr/bin/env bash
# Release script for JobManagerCK.
#
# Usage:
#   scripts/release.sh                       # auto-bump patch (e.g. 2.1.1 -> 2.1.2)
#   scripts/release.sh 2.2.0                 # explicit version
#   scripts/release.sh 2.2.0 "Big refactor"  # explicit version + title
#   scripts/release.sh 2.2.0 "Title" notes.md  # + notes from file
#
# Does:
#   1. Bumps CURRENT_VERSION in source/updater.py
#   2. Runs pytest (aborts on failure)
#   3. Rebuilds dist/JobManager.exe via PyInstaller
#   4. Commits the version bump and pushes to origin/main
#   5. Creates a GitHub release with the exe as the asset

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_DIR="$REPO_ROOT/source"
UPDATER="$SOURCE_DIR/updater.py"
DIST_EXE="$SOURCE_DIR/dist/JobManager.exe"
PYTHON="${PYTHON:-C:/Users/stefa/AppData/Local/Python/bin/python.exe}"

CURRENT=$(grep -oP 'CURRENT_VERSION = "\K[^"]+' "$UPDATER")
if [ -z "${CURRENT:-}" ]; then
  echo "ERROR: could not read CURRENT_VERSION from $UPDATER" >&2
  exit 1
fi

if [ $# -ge 1 ] && [ -n "$1" ]; then
  NEW="$1"
else
  IFS='.' read -ra PARTS <<< "$CURRENT"
  NEW="${PARTS[0]}.${PARTS[1]}.$((PARTS[2] + 1))"
fi

TITLE="${2:-v$NEW}"
NOTES_FILE="${3:-}"

echo "=========================================="
echo "  JobManagerCK release: v$CURRENT -> v$NEW"
echo "=========================================="

echo ">> Bumping CURRENT_VERSION in updater.py"
sed -i "s/CURRENT_VERSION = \"$CURRENT\"/CURRENT_VERSION = \"$NEW\"/" "$UPDATER"
grep 'CURRENT_VERSION' "$UPDATER"

echo ">> Running tests"
(cd "$SOURCE_DIR" && "$PYTHON" -m pytest tests/ -q)

echo ">> Cleaning build artifacts"
rm -rf "$SOURCE_DIR/build" "$SOURCE_DIR/dist"

echo ">> Building JobManager.exe"
(cd "$SOURCE_DIR" && "$PYTHON" -m PyInstaller job_manager.spec --noconfirm >/dev/null)
if [ ! -f "$DIST_EXE" ]; then
  echo "ERROR: PyInstaller did not produce $DIST_EXE" >&2
  exit 1
fi
EXE_SIZE=$(du -h "$DIST_EXE" | cut -f1)
echo "   built: $DIST_EXE ($EXE_SIZE)"

echo ">> Committing version bump"
(cd "$REPO_ROOT" && git add source/updater.py && git commit -m "chore: bump version to $NEW")

echo ">> Pushing to origin/main"
(cd "$REPO_ROOT" && git push origin main)

echo ">> Creating GitHub release v$NEW"
if [ -n "$NOTES_FILE" ] && [ -f "$NOTES_FILE" ]; then
  (cd "$REPO_ROOT" && gh release create "v$NEW" "$DIST_EXE" --title "$TITLE" --notes-file "$NOTES_FILE")
else
  (cd "$REPO_ROOT" && gh release create "v$NEW" "$DIST_EXE" --title "$TITLE" --notes "Release v$NEW")
fi

echo ""
echo "=========================================="
echo "  Released v$NEW"
echo "  https://github.com/Steffy69/JobManagerCK/releases/tag/v$NEW"
echo "=========================================="
