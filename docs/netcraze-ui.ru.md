# Netcraze/Keenetic Web UI guide — RU

Этот проект специально оставляет настройку Proxy connections и Connection policies вручную через веб-интерфейс. Так безопаснее: видно, какие клиенты уходят через proxy, а Default policy не меняется случайно.

## Proxy connections

Создайте отдельное proxy connection для каждого локального SOCKS-порта Xray.

Пример:

| Name | Type | Server | Port |
|---|---|---|---:|
| `XRAY-VERTPATH` | SOCKS5 | `127.0.0.1` | `1082` |
| `XRAY-WHITE5` | SOCKS5 | `127.0.0.1` | `1083` |
| `XRAY-FROST2` | SOCKS5 | `127.0.0.1` | `1084` |

Authentication оставьте disabled, если вы явно не включали SOCKS auth в Xray.

## Connection policies

Создайте отдельную политику подключений для каждого режима:

| Policy | Connection |
|---|---|
| `TV-VERTPATH` | only `XRAY-VERTPATH` |
| `TV-WHITE5` | only `XRAY-WHITE5` |
| `TV-FROST2` | only `XRAY-FROST2` |

Default policy не меняйте.

Назначайте только нужное устройство, например `TV`, в выбранную политику подключений.

## Безопасное переключение

- Основной режим: `TV` → `TV-VERTPATH`
- Резерв 1: `TV` → `TV-WHITE5`
- Резерв 2: `TV` → `TV-FROST2`
- Прямой интернет: `TV` → Default policy

## Чего избегать

- Не добавляйте целый segment.
- Не добавляйте all clients.
- Не переносите Default policy на proxy connection.
- Не открывайте локальные SOCKS-порты в LAN/WAN.
- Не меняйте Default policy, если цель — настроить только одно устройство.

## Проверка после настройки

На роутере:

```sh
sh scripts/healthcheck.sh
```

В Web UI:

- нужные proxy connections должны быть connected/up;
- выбранная политика подключений должна содержать только нужное устройство;
- Default policy должна оставаться основной для остальных клиентов.
