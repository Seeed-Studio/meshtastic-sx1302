#!/usr/bin/env bash
# Package the SenseCAP M1 (SX1302) meshtasticd binary + install script into a
# distributable tarball. Run on the x86 build machine after the Docker build.
#
# Prereq: the arm64 binary must already be extracted to release/:
#   docker run --rm -v "$PWD/release:/out" meshtastic-sensecap-m1:arm64 \
#       sh -c 'cp /opt/firmware/.pio/build/sensecap-m1/meshtasticd \
#              /out/meshtasticd_linux_aarch64'
#
# Usage:  bash bin/package-sensecap-m1.sh
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

BINARY="release/meshtasticd_linux_aarch64"
if [[ ! -f "$BINARY" ]]; then
    echo "error: $BINARY not found. Build and extract the binary first." >&2
    exit 1
fi

# Verify the binary is actually ARM64.
arch=$(file -b "$BINARY")
case "$arch" in
    *ARM*aarch64*) ;;
    *) echo "error: $BINARY is not an aarch64 ELF: $arch" >&2; exit 1 ;;
esac

# Version label for the tarball name.
HASH=$(git rev-parse --short HEAD)
PKG_NAME="meshtasticd-sensecap-m1-aarch64-${HASH}"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

PKG_DIR="$STAGE/$PKG_NAME"
mkdir -p "$PKG_DIR"

echo "=== Staging package: $PKG_NAME ==="

# Binary.
cp "$BINARY" "$PKG_DIR/meshtasticd"

# Install script (renamed to install.sh inside the tarball).
cp bin/install-sensecap-m1.sh "$PKG_DIR/install.sh"
chmod +x "$PKG_DIR/install.sh"

# Config template + pre-activated SX1302 lora config.
cp bin/config-dist.yaml "$PKG_DIR/config-dist.yaml"
cp bin/config.d/lora-sensecap-m1-sx1302.yaml "$PKG_DIR/lora-sensecap-m1-sx1302.yaml"

# Full hardware config catalog (→ available.d on install).
cp -r bin/config.d "$PKG_DIR/config.d"

# udev rules (spidev/gpio permissions).
cp bin/99-meshtasticd-udev.rules "$PKG_DIR/99-meshtasticd-udev.rules"

# --- Web assets (optional) ---------------------------------------------------
# Fetch the Meshtastic web UI release. Failure is non-fatal — the HTTPS API
# still works without the frontend; the browser just gets a 404 on the UI page.
#
# Defaults to bin/web.version (repo pin) but can be overridden:
#   WEB_VERSION=2.7.2 bash bin/package-sensecap-m1.sh
WEB_VERSION="${WEB_VERSION:-$(cat bin/web.version 2>/dev/null || echo "")}"
if [[ -n "$WEB_VERSION" ]]; then
    echo "Fetching web UI (v$WEB_VERSION) ..."
    mkdir -p "$PKG_DIR/web"
    if curl -fsSL "https://github.com/meshtastic/web/releases/download/v${WEB_VERSION}/build.tar" \
            -o "$STAGE/web.tar"; then
        tar -xf "$STAGE/web.tar" -C "$PKG_DIR/web/"
        # The release ships .gz-compressed assets; decompress them in place.
        gzip -dr "$PKG_DIR/web" 2>/dev/null || true
        rm -f "$STAGE/web.tar"
        echo "  web UI bundled."
    else
        echo "  WARNING: failed to fetch web UI — package without it." >&2
        echo "  (HTTPS API still works; install web assets separately if needed)" >&2
        rmdir "$PKG_DIR/web" 2>/dev/null || true
    fi
fi

# --- Create tarball ----------------------------------------------------------
echo "Creating tarball ..."
tar -czf "release/${PKG_NAME}.tar.gz" -C "$STAGE" "$PKG_NAME"

echo
echo "=== Package ready ==="
echo "  release/${PKG_NAME}.tar.gz"
echo
echo "Deploy to Pi:"
echo "  scp release/${PKG_NAME}.tar.gz pi@<IP>:~"
echo "  # On Pi:"
echo "  tar xzf ${PKG_NAME}.tar.gz"
echo "  cd ${PKG_NAME}"
echo "  sudo ./install.sh"
