# Troubleshooting

## Xray does not start

Run:

```sh
/opt/sbin/xray run -test -confdir /opt/etc/xray/configs
tail -80 /opt/var/log/xray-direct.log
```

## SOCKS listens on 0.0.0.0

Stop immediately:

```sh
sh /opt/etc/init.d/S23xray-direct stop
```

Check that every inbound has:

```json
"listen": "127.0.0.1"
```

## Firewall rules appeared

This toolkit should not create firewall rules.

Check:

```sh
iptables-save | grep -Ei 'xkeen|TPROXY|61219|1082|1083|1084'
ip6tables-save | grep -Ei 'xkeen|TPROXY|61219|1082|1083|1084'
```

Do not use:

```sh
xkeen -start
```

## Netcraze proxy connection is up, but the selected client is direct

Check:

- The selected client is assigned to the correct connection policy.
- Policy contains only the intended proxy connection.
- Xray listener for that policy is running.
- The client did not change MAC address due to private/random MAC.

## After reboot Xray is not running

Check executable bits:

```sh
ls -l /opt/etc/init.d/S23xray-direct
ls -l /opt/etc/init.d/S24xray
```

Expected:

```text
S23xray-direct -> 755
S24xray        -> 644
```

Then run:

```sh
sh /opt/etc/init.d/S23xray-direct start
```
