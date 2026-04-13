#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build/appimage"
APPDIR="${BUILD_DIR}/ThemeAtlas.AppDir"
PYTHON_BIN="${PYTHON_BIN:-python3}"

rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/bin" "${APPDIR}/usr/share/applications" "${APPDIR}/usr/share/icons/hicolor/scalable/apps"

"${PYTHON_BIN}" -m pip install --upgrade pip
"${PYTHON_BIN}" -m pip install --prefix "${APPDIR}/usr" "${ROOT_DIR}"

install -m 0644 "${ROOT_DIR}/assets/themeatlas.desktop" "${APPDIR}/usr/share/applications/themeatlas.desktop"
install -m 0644 "${ROOT_DIR}/assets/icons/themeatlas-icon.svg" "${APPDIR}/usr/share/icons/hicolor/scalable/apps/themeatlas.svg"

cat >"${APPDIR}/AppRun" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SITE="$(find "${HERE}/usr/lib" -type d \( -path '*/site-packages' -o -path '*/dist-packages' \) | head -n 1)"
export PYTHONPATH="${PY_SITE}:${PYTHONPATH:-}"
exec "${HERE}/usr/bin/themeatlas" "$@"
EOF
chmod 0755 "${APPDIR}/AppRun"

echo "AppDir prepared at: ${APPDIR}"
echo "If appimagetool is installed, build the AppImage with:"
echo "  appimagetool \"${APPDIR}\""