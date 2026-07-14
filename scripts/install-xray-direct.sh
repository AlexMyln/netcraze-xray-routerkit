#!/bin/sh
set -eu

SRC_DIR="${1:-generated}"
DEST_DIR="/opt/etc/xray/configs"
INIT_SRC="$(dirname "$0")/../templates/S23xray-direct"
INIT_DEST="/opt/etc/init.d/S23xray-direct"

if [ "$(uname -s)" != "Linux" ]; then
    echo "ERROR: this script must run on Entware/Linux router, not macOS/Windows." >&2
    exit 1
fi

if [ ! -x /opt/sbin/xray ]; then
    echo "ERROR: /opt/sbin/xray not found or not executable." >&2
    exit 1
fi

for f in 03_inbounds.json 04_outbounds.json 05_routing.json; do
    if [ ! -f "$SRC_DIR/$f" ]; then
        echo "ERROR: missing $SRC_DIR/$f" >&2
        exit 1
    fi
done

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/opt/backups/xray-routerkit-install-$TS"
mkdir -p "$BACKUP_DIR"

if [ -d /opt/etc/xray ]; then
    cp -a /opt/etc/xray "$BACKUP_DIR/xray"
fi
if [ -f "$INIT_DEST" ]; then
    cp -a "$INIT_DEST" "$BACKUP_DIR/S23xray-direct.backup"
fi
if [ -f /opt/etc/init.d/S24xray ]; then
    cp -a /opt/etc/init.d/S24xray "$BACKUP_DIR/S24xray.backup"
fi

mkdir -p "$DEST_DIR"
cp -a "$SRC_DIR/03_inbounds.json" "$DEST_DIR/03_inbounds.json"
cp -a "$SRC_DIR/04_outbounds.json" "$DEST_DIR/04_outbounds.json"
cp -a "$SRC_DIR/05_routing.json" "$DEST_DIR/05_routing.json"
chmod 600 "$DEST_DIR/03_inbounds.json" "$DEST_DIR/04_outbounds.json" "$DEST_DIR/05_routing.json"

if [ ! -f "$INIT_SRC" ]; then
    echo "ERROR: init template not found: $INIT_SRC" >&2
    exit 1
fi

cp -a "$INIT_SRC" "$INIT_DEST"
chmod 644 "$INIT_DEST"

if [ -f /opt/etc/init.d/S24xray ]; then
    chmod 644 /opt/etc/init.d/S24xray
fi

/opt/sbin/xray run -test -confdir /opt/etc/xray/configs

echo "Installed configs and direct init script."
echo "Backup: $BACKUP_DIR"
echo
echo "To start manually:"
echo "  sh $INIT_DEST start"
echo
echo "To enable autostart after healthcheck:"
echo "  python3 scripts/routerkit-autostart.py --enable --apply"
echo
echo "S24xray remains disabled:"
ls -l /opt/etc/init.d/S24xray 2>/dev/null || true
