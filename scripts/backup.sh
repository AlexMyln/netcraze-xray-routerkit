#!/bin/sh
set -eu

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/opt/backups/final-netcraze-xray-$TS"
ARCHIVE="/opt/backups/final-netcraze-xray-$TS.tar.gz"

mkdir -p "$BACKUP_DIR"

cp -a /opt/etc/xray "$BACKUP_DIR/xray" 2>/dev/null || true
cp -a /opt/etc/init.d/S23xray-direct "$BACKUP_DIR/S23xray-direct" 2>/dev/null || true
cp -a /opt/etc/init.d/S24xray "$BACKUP_DIR/S24xray" 2>/dev/null || true

{
    echo "date: $(date)"
    echo "uname:"
    uname -a
    echo
    echo "init scripts:"
    ls -l /opt/etc/init.d/S23xray-direct /opt/etc/init.d/S24xray 2>/dev/null || true
    echo
    echo "xray version:"
    /opt/sbin/xray version 2>/dev/null || true
    echo
    echo "xray process:"
    pgrep -a xray || ps | grep '[x]ray' || true
    echo
    echo "listen:"
    netstat -lntup 2>/dev/null | grep -E '1082|1083|1084' || true
    echo
    echo "firewall check:"
    iptables-save | grep -Ei 'xkeen|TPROXY|61219|1082|1083|1084' || true
    ip6tables-save | grep -Ei 'xkeen|TPROXY|61219|1082|1083|1084' || true
} > "$BACKUP_DIR/manifest.txt"

tar -C /opt/backups -czf "$ARCHIVE" "$(basename "$BACKUP_DIR")"

echo "Backup directory: $BACKUP_DIR"
echo "Archive: $ARCHIVE"
ls -lh "$ARCHIVE"
echo "WARNING: backup archive may contain secrets. Do not publish it."
