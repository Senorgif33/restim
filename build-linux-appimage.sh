#!/usr/bin/env bash
# build-linux-appimage.sh
#
# Build a standalone Restim-x86_64.AppImage from a Restim checkout:
#
#     ./build-linux-appimage.sh
#
# Flags:
#     --keep-venv     Reuse existing venv-linux/ instead of recreating it.
#
# Requirements: Linux x86_64, Python 3.10+, network (pip + appimagetool).

set -euo pipefail

VENV_PATH="./venv-linux"
APP_NAME="Restim"
APPIMAGE_NAME="Restim-x86_64.AppImage"
APPDIR="Restim.AppDir"
AHRS_REPO_URL="https://github.com/Mayitzin/ahrs.git"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
APPIMAGETOOL_BIN="build/appimagetool-x86_64.AppImage"

KEEP_VENV=0
for arg in "$@"; do
  case "$arg" in
    --keep-venv) KEEP_VENV=1 ;;
    -h|--help)
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown flag: $arg" >&2
      exit 2
      ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: this script only runs on Linux."
  exit 1
fi

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "ERROR: only x86_64 AppImage builds are supported right now (found $(uname -m))."
  exit 1
fi

if [[ ! -f "restim-linux.spec" ]] || [[ ! -f "requirements.txt" ]]; then
  echo "ERROR: run this from the root of a Restim checkout."
  echo "       Expected restim-linux.spec and requirements.txt in $(pwd)."
  exit 1
fi

for cmd in python3 curl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: $cmd not found."
    exit 1
  fi
done

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,10) else 0)')
if [[ "$PY_OK" != "1" ]]; then
  PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  echo "ERROR: Python 3.10+ required, found $PY_VER."
  exit 1
fi

# --- Virtual environment ---
if [[ "$KEEP_VENV" == "1" ]] && [[ -d "$VENV_PATH" ]]; then
  echo "==> Reusing existing $VENV_PATH/"
else
  echo "==> Creating virtual environment at $VENV_PATH/..."
  rm -rf "$VENV_PATH"
  python3 -m venv "$VENV_PATH"
fi
# shellcheck disable=SC1091
source "$VENV_PATH/bin/activate"

echo "==> Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
pip install --quiet pyinstaller

# --- Verify ahrs data files (same issue as macOS builds) ---
echo "==> Verifying ahrs data files in venv..."
AHRS_SRC=$(python -c 'import ahrs, os; print(os.path.dirname(ahrs.__file__))')
if [[ ! -f "$AHRS_SRC/utils/WMM2025/WMM.COF" ]]; then
  echo "    WMM.COF missing from pip-installed ahrs. Reinstalling from git..."
  pip uninstall --quiet --yes ahrs
  pip install --quiet "git+${AHRS_REPO_URL}"
  AHRS_SRC=$(python -c 'import ahrs, os; print(os.path.dirname(ahrs.__file__))')
  if [[ ! -f "$AHRS_SRC/utils/WMM2025/WMM.COF" ]]; then
    echo "ERROR: WMM.COF still missing after git reinstall of ahrs."
    exit 1
  fi
fi

# --- PyInstaller onedir ---
echo "==> Building onedir bundle with PyInstaller..."
rm -rf build/restim build/restim-linux dist/restim "$APPDIR" "$APPIMAGE_NAME"
pyinstaller restim-linux.spec --clean --noconfirm

if [[ ! -x "dist/restim/restim" ]]; then
  echo "ERROR: Build finished but dist/restim/restim not found."
  ls -la dist/ || true
  ls -la dist/restim/ || true
  exit 1
fi

# --- Stage AppDir ---
echo "==> Staging AppDir..."
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -a dist/restim/. "$APPDIR/usr/bin/"

# Safety net: never ship host-conflicting C++ runtime libs in the AppImage.
# restim-linux.spec already excludes these; remove any stragglers from deps.
find "$APPDIR" \( -name 'libstdc++.so*' -o -name 'libgcc_s.so*' \) -print -delete || true

cp resources/favicon.png "$APPDIR/restim.png"

cat > "$APPDIR/restim.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=${APP_NAME}
Comment=Realtime e-stim signal generator
Exec=restim
Icon=restim
Categories=Utility;
Terminal=false
EOF

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/bash
set -euo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
# Restim writes restim.log to CWD; desktop launches may use a bad CWD.
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/Restim"
mkdir -p "$STATE_DIR"
cd "$STATE_DIR"
exec "$HERE/usr/bin/restim" "$@"
EOF
chmod +x "$APPDIR/AppRun" "$APPDIR/usr/bin/restim"

# --- appimagetool ---
mkdir -p build
if [[ ! -x "$APPIMAGETOOL_BIN" ]]; then
  echo "==> Downloading appimagetool..."
  curl -fsSL -o "$APPIMAGETOOL_BIN" "$APPIMAGETOOL_URL"
  chmod +x "$APPIMAGETOOL_BIN"
fi

echo "==> Packing AppImage..."
export ARCH=x86_64
export APPIMAGE_EXTRACT_AND_RUN=1
"$APPIMAGETOOL_BIN" "$APPDIR" "$APPIMAGE_NAME"
chmod +x "$APPIMAGE_NAME"

echo ""
echo "Build complete: $APPIMAGE_NAME"
echo "Run with: ./$APPIMAGE_NAME"
echo "Logs: \${XDG_STATE_HOME:-\$HOME/.local/state}/Restim/restim.log"
