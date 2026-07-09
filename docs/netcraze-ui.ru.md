# Netcraze/Keenetic Web UI guide — RU

Этот проект специально оставляет настройку Proxy connections и Connection policies вручную через веб-интерфейс. Так безопаснее: видно, какие клиенты уходят через proxy, а Default policy не меняется случайно.

## Proxy connections

Создайте отдельное proxy connection для каждого локального SOCKS-порта Xray.

Пример:

| Name | Type | Server | Port |
|---|---|---|---:|
| `XRAY-PROFILE-A` | SOCKS5 | `127.0.0.1` | `1082` |
| `XRAY-PROFILE-B` | SOCKS5 | `127.0.0.1` | `1083` |
| `XRAY-PROFILE-C` | SOCKS5 | `127.0.0.1` | `1084` |

Authentication оставьте disabled, если вы явно не включали SOCKS auth в Xray.

## Connection policies

Создайте отдельную политику подключений для каждого режима:

| Policy | Connection |
|---|---|
| `CLIENT-PROFILE-A` | only `XRAY-PROFILE-A` |
| `CLIENT-PROFILE-B` | only `XRAY-PROFILE-B` |
| `CLIENT-PROFILE-C` | only `XRAY-PROFILE-C` |

Default policy не меняйте.

Назначайте только нужное устройство, например `TV`, в выбранную политику подключений.

## Безопасное переключение

- Основной режим: выбранный клиент -> `CLIENT-PROFILE-A`
- Резерв 1: выбранный клиент -> `CLIENT-PROFILE-B`
- Резерв 2: выбранный клиент -> `CLIENT-PROFILE-C`
- Прямой интернет: выбранный клиент -> Default policy

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
