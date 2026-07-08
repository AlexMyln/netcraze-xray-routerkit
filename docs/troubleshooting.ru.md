# Troubleshooting — RU

## Xray не стартует

Проверьте конфиг:

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

Этот набор инструментов не должен создавать firewall-правила.

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

- устройство назначено в правильную политику подключений;
- политика подключений содержит только нужное proxy connection;
- локальный SOCKS-порт Xray для этой политики запущен;
- устройство не сменило MAC из-за private/random MAC;
- Default policy не перехватывает устройство.

## После reboot Xray не запустился

Проверьте права init-скриптов:

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

- процесс Xray есть;
- нужные порты слушают только `127.0.0.1`;
- проверка firewall пустая по `xkeen|TPROXY|61219|1082|1083|1084`;
- IP через SOCKS отличается от IP при прямом подключении.
