#!/bin/sh

PORTS_RE="${PORTS_RE:-1082|1083|1084}"
FIREWALL_RE="${FIREWALL_RE:-xkeen|TPROXY|61219|1082|1083|1084}"

WARNINGS=0
CRITICAL=0

section() {
    printf '\n===== %s =====\n' "$1"
}

info() {
    printf '[INFO] %s\n' "$1"
}

ok() {
    printf '[OK] %s\n' "$1"
}

warn() {
    WARNINGS=$((WARNINGS + 1))
    printf '[WARN] %s\n' "$1"
}

fail() {
    CRITICAL=$((CRITICAL + 1))
    printf '[FAIL] %s\n' "$1"
}

check_dir_or_creatable() {
    path="$1"
    parent="$2"
    label="$3"

    if [ -d "$path" ]; then
        ok "$label exists: $path"
        return
    fi

    if [ -d "$parent" ] && [ -w "$parent" ] && [ -x "$parent" ]; then
        ok "$label does not exist, but parent appears writable: $parent"
        return
    fi

    fail "$label missing and parent is not writable: $path"
}

check_command_required() {
    name="$1"
    if command -v "$name" >/dev/null 2>&1; then
        path="$(command -v "$name")"
        ok "command available: $name ($path)"
    else
        fail "required command missing: $name"
    fi
}

check_command_optional() {
    name="$1"
    if command -v "$name" >/dev/null 2>&1; then
        path="$(command -v "$name")"
        ok "optional command available: $name ($path)"
    else
        warn "optional command not found: $name"
    fi
}

is_xray_running() {
    if command -v pgrep >/dev/null 2>&1; then
        pgrep xray >/dev/null 2>&1
        return $?
    fi

    ps 2>/dev/null | grep '[x]ray' >/dev/null 2>&1
}

print_xray_processes() {
    if command -v pgrep >/dev/null 2>&1; then
        pgrep -a xray 2>/dev/null || pgrep xray 2>/dev/null || true
    else
        ps 2>/dev/null | grep '[x]ray' || true
    fi
}

collect_listeners() {
    if command -v netstat >/dev/null 2>&1; then
        netstat -lntup 2>/dev/null || netstat -lnt 2>/dev/null || true
        return
    fi

    if command -v ss >/dev/null 2>&1; then
        ss -lntup 2>/dev/null || ss -lnt 2>/dev/null || true
        return
    fi
}

check_firewall_tool() {
    tool="$1"

    if ! command -v "$tool" >/dev/null 2>&1; then
        warn "$tool not found; firewall marker check skipped"
        return
    fi

    matches="$($tool 2>/dev/null | grep -Ei "$FIREWALL_RE" || true)"
    if [ -n "$matches" ]; then
        warn "$tool contains routerkit/xkeen/firewall-related markers"
        printf '%s\n' "$matches"
    else
        ok "$tool has no matching firewall markers"
    fi
}

section "system"
OS_NAME="$(uname -s 2>/dev/null || printf 'unknown')"
printf 'uname -s: %s\n' "$OS_NAME"

if [ "$OS_NAME" != "Linux" ]; then
    fail "expected Linux; this preflight is intended for Entware/router Linux"
    section "summary"
    printf 'warnings=%s\n' "$WARNINGS"
    printf 'critical_failures=%s\n' "$CRITICAL"
    printf 'PRECHECK_RESULT=FAIL\n'
    exit 2
fi

section "filesystem"
if [ -d /opt ]; then
    ok "/opt exists"
else
    fail "/opt does not exist"
fi

check_dir_or_creatable /opt/etc /opt "/opt/etc"

if [ -x /opt/sbin/xray ]; then
    ok "/opt/sbin/xray exists and is executable"
else
    fail "/opt/sbin/xray missing or not executable"
fi

if [ -d /opt/etc/xray/configs ]; then
    ok "/opt/etc/xray/configs exists"
elif [ -d /opt/etc/xray ] && [ -w /opt/etc/xray ] && [ -x /opt/etc/xray ]; then
    ok "/opt/etc/xray/configs missing, but /opt/etc/xray appears writable"
elif [ -d /opt/etc ] && [ -w /opt/etc ] && [ -x /opt/etc ]; then
    ok "/opt/etc/xray/configs missing, but /opt/etc appears writable"
elif [ -d /opt ] && [ -w /opt ] && [ -x /opt ]; then
    ok "/opt/etc/xray/configs missing, but /opt appears writable"
else
    fail "/opt/etc/xray/configs missing and parent directories are not writable"
fi

section "commands"
check_command_required sh
check_command_required curl
check_command_required tar
check_command_optional jq

section "init scripts"
if [ -e /opt/etc/init.d/S24xray ]; then
    ls -l /opt/etc/init.d/S24xray 2>/dev/null || warn "could not read permissions for /opt/etc/init.d/S24xray"
else
    info "/opt/etc/init.d/S24xray not found"
fi

if [ -e /opt/etc/init.d/S23xray-direct ]; then
    ls -l /opt/etc/init.d/S23xray-direct 2>/dev/null || warn "could not read permissions for /opt/etc/init.d/S23xray-direct"
else
    info "/opt/etc/init.d/S23xray-direct not found"
fi

section "xray process and listeners"
if is_xray_running; then
    info "xray process appears to be running"
    print_xray_processes
else
    info "xray process not detected"
fi

LISTENERS="$(collect_listeners)"
if [ -n "$LISTENERS" ]; then
    TARGET_LISTENERS="$(printf '%s\n' "$LISTENERS" | grep -E ":($PORTS_RE)([[:space:]]|$)" || true)"
    if [ -n "$TARGET_LISTENERS" ]; then
        printf '%s\n' "$TARGET_LISTENERS"
    else
        ok "no listeners found on target ports: $PORTS_RE"
    fi

    NON_LOOPBACK_TARGET_LISTENERS="$(printf '%s\n' "$TARGET_LISTENERS" | grep -Ev '(^|[[:space:]])127\.0\.0\.1:('"$PORTS_RE"')([[:space:]]|$)|(^|[[:space:]])::1:('"$PORTS_RE"')([[:space:]]|$)|(^|[[:space:]])\[::1\]:('"$PORTS_RE"')([[:space:]]|$)' || true)"
    if [ -n "$NON_LOOPBACK_TARGET_LISTENERS" ]; then
        fail "target port is listening on a non-loopback address"
        printf '%s\n' "$NON_LOOPBACK_TARGET_LISTENERS"
    else
        ok "target port listeners are loopback-only"
    fi
else
    if is_xray_running; then
        warn "netstat/ss unavailable or returned no data; could not verify listeners"
    else
        ok "listener check skipped because xray is not running and no listener data was available"
    fi
fi

section "firewall markers"
check_firewall_tool iptables-save
check_firewall_tool ip6tables-save

section "summary"
printf 'warnings=%s\n' "$WARNINGS"
printf 'critical_failures=%s\n' "$CRITICAL"

if [ "$CRITICAL" -gt 0 ]; then
    printf 'PRECHECK_RESULT=FAIL\n'
    exit 1
fi

if [ "$WARNINGS" -gt 0 ]; then
    printf 'PRECHECK_RESULT=WARN\n'
else
    printf 'PRECHECK_RESULT=PASS\n'
fi

exit 0
