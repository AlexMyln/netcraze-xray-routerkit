# Netcraze/Keenetic: Proxy и политики для выбранных устройств — RU

RouterKit создаёт локальные SOCKS endpoint, но **сам по себе не переводит клиентские устройства через VPN**.

Нужна штатная цепочка Netcraze/Keenetic:

```text
Xray loopback SOCKS
-> native Proxy interface
-> native Internet access policy
-> выбранный зарегистрированный клиент
```

Для боевой установки также см. [`live-install-runbook.ru.md`](live-install-runbook.ru.md).

## 1. Системный компонент Proxy client

Если конечная цель включает VPN для конкретных устройств, заранее проверьте наличие штатного системного компонента **Proxy client**.

Если его установка требует reboot и оператор разрешил закончить установку, компонент ставится до финальной настройки policies, а один controlled reboot одновременно используется для after-reboot проверки `/opt`/Entware/Xray/autostart.

Не обнаруживайте отсутствие Proxy client только после заявления «RouterKit полностью готов».

## 2. Proxy connections / interfaces

Создайте отдельный native Proxy interface для каждого локального SOCKS-порта Xray.

Пример:

| Name | Type | Server | Port |
|---|---|---|---:|
| `XRAY-PROFILE-A` | SOCKS5 | `127.0.0.1` | `1082` |
| `XRAY-PROFILE-B` | SOCKS5 | `127.0.0.1` | `1083` |
| `XRAY-PROFILE-C` | SOCKS5 | `127.0.0.1` | `1084` |

Authentication оставьте disabled, если SOCKS auth явно не включён в Xray.

На некоторых версиях NetcrazeOS нужный раздел может отсутствовать в Web UI. В этом случае используйте **штатный native CLI/structured interface текущей прошивки**, а не firewall/iptables workaround.

Перед записью определите свободные `ProxyN`; не угадывайте номера.

## 3. Connection / Internet access policies

Создайте отдельную policy для каждого режима:

| Policy | Connection |
|---|---|
| `CLIENT-PROFILE-A` | only `XRAY-PROFILE-A` |
| `CLIENT-PROFILE-B` | only `XRAY-PROFILE-B` |
| `CLIENT-PROFILE-C` | only `XRAY-PROFILE-C` |

Каждая VPN-policy должна использовать только свой Proxy interface как VPN path.

Назначайте в policy только явно выбранные оператором зарегистрированные устройства.

## 4. Default и прямой Internet

Обычное безопасное поведение:

- не выбранные устройства остаются на direct PPPoE/обычном Internet path;
- весь segment/Home network автоматически в VPN не переводится;
- Default/Main не переводится целиком на Proxy.

Если оператор **явно** разрешил добавить Proxy interfaces как fallback в Default, это отдельное допустимое решение. После этого в отчёте нужно описать фактическую приоритетную схему и нельзя утверждать `DEFAULT_POLICY_UNCHANGED=TRUE`.

Основной direct uplink должен оставаться основным, если оператор не запросил иное.

## 5. Безопасное переключение конкретного клиента

Типовая логика:

- выбранный клиент -> `CLIENT-PROFILE-A`;
- выбранный клиент -> `CLIENT-PROFILE-B`;
- выбранный клиент -> `CLIENT-PROFILE-C`;
- обратно напрямую -> Default/Main policy.

Это позволяет менять профиль конкретного устройства без изменения остальных клиентов.

## 6. Чего избегать

- Не добавляйте целый segment без явной команды оператора.
- Не добавляйте all clients автоматически.
- Не открывайте `1082`/`1083`/`1084` в LAN/WAN.
- Не используйте `xkeen -start`.
- Не создавайте TPROXY/REDIRECT/transparent firewall mode.
- Не используйте iptables marking или ручное редактирование config вместо известного native policy mechanism.
- Не меняйте Default/Main скрытно.

## 7. Проверка после настройки

Сначала RouterKit/Xray:

```sh
sh scripts/healthcheck.sh
```

Затем native router state:

- все нужные Proxy interfaces `UP`/ready;
- каждая policy содержит ожидаемый Proxy path;
- только выбранные устройства имеют VPN-policy assignment;
- остальные устройства остаются на обычном direct path;
- PPPoE/default route/DNS/LAN/Wi-Fi/RMM остаются healthy.

Для каждого назначенного клиента по возможности проверьте active sessions/counters и реальный ответный трафик через его policy.

Если RouterKit listener, PID/executable identity и реальный SOCKS/HTTPS traffic доказаны независимо, а отдельный diagnostic verifier имеет известный false-negative, фиксируйте tooling defect отдельно и не объявляйте рабочую VPN-службу сломанной.
