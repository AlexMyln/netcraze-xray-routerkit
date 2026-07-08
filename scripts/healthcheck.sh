#!/bin/sh

PORTS="${PORTS:-1082|1083|1084}"

echo "===== environment ====="
uname -a
echo

echo "===== init scripts ====="
ls -l /opt/etc/init.d/S23xray-direct 2>/dev/null || true
ls -l /opt/etc/init.d/S24xray 2>/dev/null || true
echo

echo "===== xray process ====="
pgrep -a xray || ps | grep '[x]ray' || true
echo

echo "===== listeners ====="
netstat -lntup 2>/dev/null | grep -E "$PORTS" || true
echo

echo "===== firewall check ====="
if command -v iptables-save >/dev/null 2>&1; then
    iptables-save | grep -Ei "xkeen|TPROXY|61219|$PORTS" || true
else
    echo "iptables-save not found"
fi

if command -v ip6tables-save >/dev/null 2>&1; then
    ip6tables-save | grep -Ei "xkeen|TPROXY|61219|$PORTS" || true
else
    echo "ip6tables-save not found"
fi
echo

echo "===== IP checks ====="
echo -n "direct: "
curl -4 --connect-timeout 10 -m 20 https://api.ipify.org ; echo

for p in $(echo "$PORTS" | tr '|' ' '); do
    echo -n "$p: "
    curl -4 --socks5-hostname "127.0.0.1:$p" --connect-timeout 10 -m 30 https://api.ipify.org ; echo
done
