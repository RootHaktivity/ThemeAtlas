#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build/appimage"
APPDIR="${BUILD_DIR}/ThemeAtlas.AppDir"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VERSION="${VERSION:-$(cd "${ROOT_DIR}" && python3 setup.py --version 2>/dev/null || echo 1.0.0)}"
ARCH="${ARCH:-$(uname -m)}"
OUTPUT_NAME="ThemeAtlas-${VERSION}-${ARCH}.AppImage"
OUTPUT_PATH="${BUILD_DIR}/${OUTPUT_NAME}"

rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/bin" "${APPDIR}/usr/share/applications" "${APPDIR}/usr/share/icons/hicolor/scalable/apps"

"${PYTHON_BIN}" -m pip install --upgrade pip
# Force dependency installation into AppDir so the artifact is portable across systems.
"${PYTHON_BIN}" -m pip install --upgrade --ignore-installed --prefix "${APPDIR}/usr" "${ROOT_DIR}"

install -m 0644 "${ROOT_DIR}/assets/themeatlas.desktop" "${APPDIR}/usr/share/applications/themeatlas.desktop"
install -m 0644 "${ROOT_DIR}/assets/icons/themeatlas-icon.svg" "${APPDIR}/usr/share/icons/hicolor/scalable/apps/themeatlas.svg"

# AppImage tooling expects desktop/icon metadata at AppDir root.
cp "${APPDIR}/usr/share/applications/themeatlas.desktop" "${APPDIR}/themeatlas.desktop"
cp "${APPDIR}/usr/share/icons/hicolor/scalable/apps/themeatlas.svg" "${APPDIR}/themeatlas.svg"
sed -i 's|^Exec=.*$|Exec=themeatlas gui|' "${APPDIR}/themeatlas.desktop"
sed -i 's|^Icon=.*$|Icon=themeatlas|' "${APPDIR}/themeatlas.desktop"

# Replace pip-generated entry points that embed local venv paths.
cat >"${APPDIR}/usr/bin/themeatlas" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_SITE="$(find "${HERE}/lib" -type d \( -path '*/site-packages' -o -path '*/dist-packages' \) | head -n 1)"
if [[ -n "${PY_SITE}" ]]; then
	export PYTHONPATH="${PY_SITE}:${PYTHONPATH:-}"
fi
exec python3 -c 'from theme_manager.cli import main; main()' "$@"
EOF
chmod 0755 "${APPDIR}/usr/bin/themeatlas"
ln -sf "themeatlas" "${APPDIR}/usr/bin/theme-manager"

cat >"${APPDIR}/AppRun" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ $# -eq 0 ]]; then
	set -- gui
fi
exec "${HERE}/usr/bin/themeatlas" "$@"
EOF
chmod 0755 "${APPDIR}/AppRun"

echo "AppDir prepared at: ${APPDIR}"

APPIMAGETOOL_BIN="${APPIMAGETOOL_BIN:-}"
if [[ -z "${APPIMAGETOOL_BIN}" ]] && command -v appimagetool >/dev/null 2>&1; then
	APPIMAGETOOL_BIN="$(command -v appimagetool)"
fi

if [[ -n "${APPIMAGETOOL_BIN}" ]] && [[ -x "${APPIMAGETOOL_BIN}" ]]; then
	"${APPIMAGETOOL_BIN}" "${APPDIR}" "${OUTPUT_PATH}"
	echo "AppImage created: ${OUTPUT_PATH}"
else
	echo "appimagetool not found. To finish packaging run:"
	echo "  appimagetool \"${APPDIR}\" \"${OUTPUT_PATH}\""
fi