#!/usr/bin/env bash
# Build the desktop app for the current OS into dist/.
#   macOS   -> dist/MCP Creds.app  (+ dist/MCP-Creds-macOS.zip)
#   Windows -> dist/MCP Creds/MCP Creds.exe
#   Linux   -> dist/MCP Creds/MCP Creds
# Needs uv (https://docs.astral.sh/uv/). Everything else is fetched into a throwaway env.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON_VERSION:-3.12}"
# Build tools are pinned so a release never silently picks up a new upstream version.
PYINSTALLER="pyinstaller==6.22.3"; PYWEBVIEW="pywebview==6.2.1"; PILLOW="pillow==13.0.0"
uv run -q --python "$PY" --with "$PILLOW" python packaging/make_icon.py
ICON=packaging/icon.png
case "$(uname -s)" in Darwin) ICON=packaging/icon.icns ;; MINGW*|MSYS*|CYGWIN*) ICON=packaging/icon.ico ;; esac
SEP=":"; case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) SEP=";" ;; esac
rm -rf build dist
# Native window via pywebview on macOS/Windows. Linux opens in the browser (pywebview needs system GTK/Qt there).
WITH="--with $PYINSTALLER --with $PYWEBVIEW"; [ "$(uname -s)" = "Linux" ] && WITH="--with $PYINSTALLER"
# shellcheck disable=SC2086
uv run -q --python "$PY" $WITH pyinstaller \
  --noconfirm --clean --windowed --name "MCP Creds" \
  --icon "$ICON" \
  --add-data "ui.html${SEP}." \
  --osx-bundle-identifier io.github.mcpcreds \
  mcpcreds.py
if [ "$(uname -s)" = "Darwin" ]; then
  /usr/libexec/PlistBuddy -c "Add :NSHumanReadableCopyright string 'MIT License'" "dist/MCP Creds.app/Contents/Info.plist" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $(python3 mcpcreds.py --version)" "dist/MCP Creds.app/Contents/Info.plist"
  codesign --force --deep --sign - "dist/MCP Creds.app" 2>/dev/null || true
  (cd dist && ditto -c -k --keepParent "MCP Creds.app" MCP-Creds-macOS.zip)
fi
if [ "$(uname -s)" = "Linux" ]; then (cd dist && tar czf MCP-Creds-Linux.tar.gz "MCP Creds"); fi
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) (cd dist && powershell -NoProfile -Command "Compress-Archive -Force -Path 'MCP Creds' -DestinationPath MCP-Creds-Windows.zip") ;; esac
echo "Built:"; ls dist
