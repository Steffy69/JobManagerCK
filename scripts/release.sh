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
#   1. Refuses to run from a dirty working tree (the exe is built from the
#      tree, and a release must ship exactly what the repo records)
#   2. Bumps CURRENT_VERSION in source/updater.py (verified, not hoped)
#   3. Runs pytest (aborts on failure)
#   4. Rebuilds dist/JobManager.exe via PyInstaller
#   5. Commits the version bump and pushes to origin/main
#   6. Creates a GitHub release with the exe as the asset

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_DIR="$REPO_ROOT/source"
UPDATER="$SOURCE_DIR/updater.py"
DIST_EXE="$SOURCE_DIR/dist/JobManager.exe"
PYTHON="${PYTHON:-C:/Users/stefa/AppData/Local/Python/bin/python.exe}"
# Own basetemp: the default %TEMP%\pytest-of-<user> dir has been seen with
# broken ACLs, which fails every tmp_path test and blocks releases.
PYTEST_TMP="${LOCALAPPDATA:-$HOME}/Temp/jmck-release-pytest"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  PYTHON="python"
fi

# -- 1. clean tree ----------------------------------------------------------
if [ -n "$(cd "$REPO_ROOT" && git status --porcelain -- source/ scripts/ releases/)" ]; then
  echo "ERROR: uncommitted changes in the working tree." >&2
  echo "Commit (or stash) them first — the release must match the repo." >&2
  exit 1
fi

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

# -- 2. version bump (verified) --------------------------------------------
echo ">> Bumping CURRENT_VERSION in updater.py"
sed -i "s/CURRENT_VERSION = \"$CURRENT\"/CURRENT_VERSION = \"$NEW\"/" "$UPDATER"
if ! grep -q "CURRENT_VERSION = \"$NEW\"" "$UPDATER"; then
  echo "ERROR: version bump did not take (still $CURRENT?)" >&2
  exit 1
fi
grep 'CURRENT_VERSION' "$UPDATER"

# -- 3. tests ---------------------------------------------------------------
echo ">> Running tests"
(cd "$REPO_ROOT" && "$PYTHON" -m pytest -q --basetemp="$PYTEST_TMP")

# -- 4. build ---------------------------------------------------------------
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

# -- 5. commit + push -------------------------------------------------------
echo ">> Committing version bump"
(cd "$REPO_ROOT" && git add source/updater.py && git commit -m "chore: bump version to $NEW")

echo ">> Pushing to origin/main"
(cd "$REPO_ROOT" && git push origin main)

# -- 6. release -------------------------------------------------------------
echo ">> Creating GitHub release v$NEW"
if [ -n "$NOTES_FILE" ] && [ -f "$NOTES_FILE" ]; then
  (cd "$REPO_ROOT" && gh release create "v$NEW" "$DIST_EXE" --title "$TITLE" --notes-file "$NOTES_FILE")
else
  # Default notes: the commits since the last release. These are shown to
  # the operator in the update dialog, so a bare "Release vX" is noise.
  NOTES=$(cd "$REPO_ROOT" && git log --oneline "v$CURRENT"..HEAD 2>/dev/null || echo "Release v$NEW")
  (cd "$REPO_ROOT" && gh release create "v$NEW" "$DIST_EXE" --title "$TITLE" --notes "$NOTES")
fi

echo ""
echo "=========================================="
echo "  Released v$NEW"
echo "  https://github.com/Steffy69/JobManagerCK/releases/tag/v$NEW"
echo "=========================================="
