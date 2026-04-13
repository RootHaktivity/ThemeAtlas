#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${HOME}/.local/share/applications"
ICON_BASE="${HOME}/.local/share/icons/hicolor"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_FILE="${APP_DIR}/themeatlas.desktop"
LAUNCHER="${BIN_DIR}/themeatlas-launcher"

mkdir -p "${APP_DIR}"
mkdir -p "${BIN_DIR}"

# Create a user-local launcher that works even when pip --user is blocked
# (PEP 668 / externally-managed Python environments).
cat >"${LAUNCHER}" <<EOF
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR}"

# Fall back to a project virtual environment if present.
if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  exec "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/main.py" gui "\$@"
fi

# Use a properly installed CLI command only when it is valid.
if command -v themeatlas >/dev/null 2>&1 && themeatlas --version >/dev/null 2>&1; then
  exec themeatlas gui "\$@"
fi

# Final fallback: system python from this checkout.
exec python3 "${ROOT_DIR}/main.py" gui "\$@"
EOF
chmod 0755 "${LAUNCHER}"

# Install desktop entry
install -m 0644 "${ROOT_DIR}/assets/themeatlas.desktop" "${DESKTOP_FILE}"
sed -i "s|^Exec=.*$|Exec=${LAUNCHER}|" "${DESKTOP_FILE}"

# Ensure this is treated as app-local startup context.
if ! grep -q '^Path=' "${DESKTOP_FILE}"; then
  printf 'Path=%s\n' "${ROOT_DIR}" >> "${DESKTOP_FILE}"
fi

# Install icon sizes
for size in 64 128 256 512; do
  src="${ROOT_DIR}/assets/icons/png/themeatlas-icon-${size}.png"
  if [[ -f "${src}" ]]; then
    dst_dir="${ICON_BASE}/${size}x${size}/apps"
    mkdir -p "${dst_dir}"
    install -m 0644 "${src}" "${dst_dir}/themeatlas.png"
  fi
done

# Install scalable SVG for launchers that prefer vector icons.
svg_src="${ROOT_DIR}/assets/icons/themeatlas-icon.svg"
if [[ -f "${svg_src}" ]]; then
  svg_dst_dir="${ICON_BASE}/scalable/apps"
  mkdir -p "${svg_dst_dir}"
  install -m 0644 "${svg_src}" "${svg_dst_dir}/themeatlas.svg"
fi

# Optional 1024 icon for high-res launchers
if [[ -f "${ROOT_DIR}/assets/icons/png/themeatlas-icon-1024.png" ]]; then
  dst_dir="${ICON_BASE}/1024x1024/apps"
  mkdir -p "${dst_dir}"
  install -m 0644 "${ROOT_DIR}/assets/icons/png/themeatlas-icon-1024.png" "${dst_dir}/themeatlas.png"
fi

update-desktop-database "${APP_DIR}" >/dev/null 2>&1 || true
gtk-update-icon-cache "${ICON_BASE}" >/dev/null 2>&1 || true

echo "Installed ThemeAtlas desktop entry and icons."
echo "You can launch it from your app menu as: ThemeAtlas"
echo "Launcher path: ${LAUNCHER}"
echo "This script installs the launcher only; it does not open ThemeAtlas automatically."
