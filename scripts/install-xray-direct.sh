#!/bin/sh
set -eu

# RouterKit/Entware commands must remain available even when the invoking
# management transport supplies a minimal PATH.
PATH="/opt/sbin:/opt/bin:/usr/sbin:/usr/bin:/sbin:/bin${PATH:+:$PATH}"
export PATH

SRC_DIR="${1:-generated}"
DEST_DIR="/opt/etc/xray/configs"
SCRIPT_DIR="$(dirname "$0")"
INIT_SRC="$SCRIPT_DIR/../templates/S23xray-direct"
INIT_DEST="/opt/etc/init.d/S23xray-direct"
ROUTING_STATE="/opt/etc/routerkit/routing-overrides.json"
ROUTING_HELPER="$SCRIPT_DIR/routerkit-routing.py"

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

# Local split-routing is persistent RouterKit state, not an ad-hoc edit of the
# generated Xray fragment.  A normal setup/regeneration deliberately writes a
# clean 05_routing.json first; when persistent overrides exist, restore their
# code-owned first direct rule before validating the installed config.
if [ -f "$ROUTING_STATE" ]; then
    if [ ! -f "$ROUTING_HELPER" ]; then
        echo "ERROR: persistent routing state exists but RouterKit routing helper is unavailable." >&2
        exit 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "ERROR: persistent routing state exists but python3 is unavailable." >&2
        exit 1
    fi
    python3 "$ROUTING_HELPER" reconcile --yes
fi

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
