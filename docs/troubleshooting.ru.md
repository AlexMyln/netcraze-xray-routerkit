# Troubleshooting — RU

## Xray не стартует

Проверьте config:

```sh
/opt/sbin/xray run -test -confdir /opt/etc/xray/configs
```

Проверьте последние строки лога:

```sh
tail -80 /opt/var/log/xray-direct.log
```

## SOCKS слушает на 0.0.0.0

Это небезопасно. Остановите Xray:

```sh
sh /opt/etc/init.d/S23xray-direct stop
```

Проверьте, что каждый inbound содержит:

```json
"listen": "127.0.0.1"
```

После исправления снова выполните:

```sh
/opt/sbin/xray run -test -confdir /opt/etc/xray/configs
sh /opt/etc/init.d/S23xray-direct start
```

## Появились firewall rules

Этот toolkit не должен создавать firewall rules.

Проверьте:

```sh
iptables-save | grep -Ei 'xkeen|TPROXY|61219|1082|1083|1084'
ip6tables-save | grep -Ei 'xkeen|TPROXY|61219|1082|1083|1084'
```

Не используйте:

```sh
xkeen -start
```

## Proxy connection включён, но устройство идёт напрямую

Проверьте:

- устройство назначено в правильную connection policy;
- policy содержит только нужное proxy connection;
- Xray listener для этой policy запущен;
- устройство не сменило MAC из-за private/random MAC;
- default policy не перехватывает устройство.

## После reboot Xray не запустился

Проверьте права init scripts:

```sh
ls -l /opt/etc/init.d/S23xray-direct
ls -l /opt/etc/init.d/S24xray
```

Ожидаемо:

```text
S23xray-direct -> 755
S24xray        -> 644
```

Запустить вручную:

```sh
sh /opt/etc/init.d/S23xray-direct start
```

## Проверка здоровья

```sh
sh scripts/healthcheck.sh
```

Ожидаемо:

- Xray process есть;
- нужные порты слушают только `127.0.0.1`;
- firewall check пустой по `xkeen|TPROXY|61219|1082|1083|1084`;
- IP через SOCKS отличается от direct IP.
