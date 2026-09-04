#!/usr/bin/env bash
# One-click installer for the SenseCAP M1 (SX1302) meshtasticd daemon.
# Runs on the target Raspberry Pi (Debian arm64) with sudo.
#
# Usage:  sudo ./install.sh
#
# Idempotent — safe to re-run. Existing /etc/meshtasticd/config.yaml is
# preserved on upgrade; the service is re-enabled if already installed.
set -euo pipefail

# --- must be root ------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    echo "error: run with sudo:  sudo ./install.sh" >&2
    exit 1
fi

# Resolve the package directory (script's own location).
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== SenseCAP M1 meshtasticd installer ==="
echo "Package dir: $PKG_DIR"
echo

# --- 1. Install system dependencies -------------------------------------------
echo "[1/9] Installing system dependencies (Debian 13) ..."
# Runtime libs from `readelf -d meshtasticd`; libulfius pulls in liborcania/
# libyder/libmicrohttpd etc. via apt dependency resolution.
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libusb-1.0-0 \
    libyaml-cpp0.8 \
    libuv1t64 \
    libgpiod3 \
    libi2c0 \
    libulfius2.7t64

# --- 2. Create meshtasticd user/group ----------------------------------------
echo "[2/9] Creating meshtasticd user/group ..."
if ! getent group meshtasticd >/dev/null; then
    addgroup --system meshtasticd
fi
if ! getent passwd meshtasticd >/dev/null; then
    adduser --system --home /var/lib/meshtasticd --no-create-home \
            --disabled-password --ingroup meshtasticd meshtasticd
fi
# Grant hardware-access groups (spi/gpio are essential for SX1302 + GPIO reset).
for grp in spi gpio dialout i2c; do
    if getent group "$grp" >/dev/null; then
        usermod -aG "$grp" meshtasticd
    fi
done

# --- 3. Install binary -------------------------------------------------------
echo "[3/9] Installing meshtasticd binary ..."
install -D -m 0755 "$PKG_DIR/meshtasticd" /usr/bin/meshtasticd

# --- 4. Create directory structure -------------------------------------------
echo "[4/9] Creating directories ..."
mkdir -p /etc/meshtasticd/{config.d,available.d,ssl}
mkdir -p /var/lib/meshtasticd
mkdir -p /usr/share/meshtasticd/web

# --- 5. Install config files -------------------------------------------------
echo "[5/9] Installing config files ..."

# Main config — preserve existing user config on upgrade.
if [[ -f /etc/meshtasticd/config.yaml ]]; then
    echo "    config.yaml already exists, preserving (not overwritten)"
else
    cp "$PKG_DIR/config-dist.yaml" /etc/meshtasticd/config.yaml
fi

# Pre-activate the SX1302 lora config (copy into config.d = active).
cp "$PKG_DIR/lora-sensecap-m1-sx1302.yaml" /etc/meshtasticd/config.d/

# Full hardware config catalog → available.d (for future hardware changes).
if [[ -d "$PKG_DIR/config.d" ]]; then
    cp -r "$PKG_DIR/config.d/." /etc/meshtasticd/available.d/
fi

# --- 6. Enable HTTPS web backend ---------------------------------------------
echo "[6/9] Enabling HTTPS web backend (port 9443) ..."
# Uncomment "Port: 9443" under the Webserver: section.
sed -i 's/^#\s*Port: 9443/  Port: 9443/' /etc/meshtasticd/config.yaml
# Ensure the RootPath/SSLKey/SSLCert lines are uncommented too.
sed -i 's/^#\s*RootPath:/  RootPath:/' /etc/meshtasticd/config.yaml
sed -i 's/^#\s*SSLKey:/  SSLKey:/' /etc/meshtasticd/config.yaml
sed -i 's/^#\s*SSLCert:/  SSLCert:/' /etc/meshtasticd/config.yaml

# Install web assets if bundled.
if [[ -d "$PKG_DIR/web" ]] && [[ -n "$(ls -A "$PKG_DIR/web" 2>/dev/null)" ]]; then
    echo "    Installing web UI assets ..."
    cp -r "$PKG_DIR/web/." /usr/share/meshtasticd/web/
fi

# --- 7. Install udev rules ----------------------------------------------------
echo "[7/9] Installing udev rules ..."
install -D -m 0644 "$PKG_DIR/99-meshtasticd-udev.rules" \
        /lib/udev/rules.d/99-meshtasticd-udev.rules
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger --subsystem-match=spidev 2>/dev/null || true
udevadm trigger --subsystem-match=gpio 2>/dev/null || true

# --- 8. Install systemd service ----------------------------------------------
echo "[8/9] Installing systemd service ..."
cat > /lib/systemd/system/meshtasticd.service <<'UNIT'
[Unit]
Description=Meshtastic Daemon (SenseCAP M1 / SX1302)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=meshtasticd
Group=meshtasticd
ExecStart=/usr/bin/meshtasticd --config=/etc/meshtasticd/config.yaml --fsdir=/var/lib/meshtasticd
Restart=on-failure
RestartSec=3
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
UNIT

# --- 9. Set ownership + activate service -------------------------------------
echo "[9/9] Setting ownership and enabling service ..."
chown -R meshtasticd:meshtasticd /var/lib/meshtasticd
chown -R meshtasticd:meshtasticd /etc/meshtasticd
chown -R meshtasticd:meshtasticd /usr/share/meshtasticd 2>/dev/null || true

systemctl daemon-reload
systemctl enable meshtasticd

# Restart (or start) the service.
if systemctl is-active --quiet meshtasticd; then
    systemctl restart meshtasticd
else
    systemctl start meshtasticd
fi

echo
echo "=== Installation complete ==="
echo
echo "Service:  systemctl status meshtasticd"
echo "Logs:     journalctl -u meshtasticd -f"
echo "Web UI:   https://$(hostname -I | awk '{print $1}'):9443"
echo "          (self-signed cert — accept the browser warning)"
echo "Config:   /etc/meshtasticd/config.yaml"
echo "Lora:     /etc/meshtasticd/config.d/lora-sensecap-m1-sx1302.yaml"
echo
