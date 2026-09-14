# Локальные direct-исключения RouterKit

RouterKit умеет хранить отдельные локальные правила split routing независимо от VPN-провайдера. Выбранные доменные зоны идут через существующий Xray outbound `direct` (`freedom`), а весь остальной трафик каждого SOCKS-профиля продолжает идти через соответствующий VLESS outbound.

```text
клиент -> native Netcraze policy -> RouterKit SOCKS -> Xray
                                         |-- выбранный сервис -> direct -> обычный WAN
                                         `-- остальной трафик -> выбранный VLESS
```

Эта функция **выключена по умолчанию**. RouterKit не выводит российские сайты из VPN автоматически.

## Service packs

Версионируемые наборы находятся в `routing/services/`.

Первый hardware-proven preset — `kinopoisk`. На Netcraze Hopper SE NC-3812 / NetcrazeOS 5.1.5 11 сентября 2026 года был подтверждён рабочий direct bypass для Samsung TV при сохранении остального трафика телевизора через RouterKit VPN.

Текущий набор `kinopoisk`:

```text
kinopoisk.ru
kinopoisk-ru.clstorage.net
ott.yandex.ru
ott.yandex.net
strm.yandex.ru
yastatic.net
yndx.net
yccdn.ru
```

Набор считается **достаточным для подтверждённого сценария**, но это не утверждение, что каждый домен в нём минимально необходим.

Дополнительно доступны opt-in candidate-паки `rutube`, `ivi`, `okko` и `wink`. Это **community-derived candidates**, а не заявление о hardware-proven совместимости RouterKit. Их стартовые domain suffix взяты из `v2fly/domain-list-community` на зафиксированном commit `5d939545c84e2a534f8e85ba6ffb2b51fa18fb76`. Точная provenance и статус доказательств описаны в `routing/services/README.md`.

Candidate-паку могут потребоваться дополнительные service-specific API/CDN/DRM suffix после реальной проверки клиента. Неполный pack не следует «чинить» добавлением широкого `RU_ALL`, всего `yandex.ru` или несвязанных IP ranges.

## Использование после установки

Статус:

```sh
python3 scripts/routerkit-routing.py status
```

Предпросмотр:

```sh
python3 scripts/routerkit-routing.py plan --add-service kinopoisk
```

Применение:

```sh
python3 scripts/routerkit-routing.py apply --add-service kinopoisk --yes
```

Повторное добавление уже выбранного service pack является no-op.

Несколько сервисов можно выбрать одной проверяемой транзакцией, потому что `--add-service` допускает повторение:

```sh
python3 scripts/routerkit-routing.py plan \
  --add-service kinopoisk \
  --add-service rutube \
  --add-service ivi \
  --add-service okko \
  --add-service wink
```

После просмотра expanded domain set те же repeated flags можно передать в `apply --yes`.

Удаление сервиса:

```sh
python3 scripts/routerkit-routing.py apply --remove-service kinopoisk --yes
```

Можно добавить собственную доменную зону без создания service pack:

```sh
python3 scripts/routerkit-routing.py plan --add-domain example.ru
python3 scripts/routerkit-routing.py apply --add-domain example.ru --yes
```

Указываются доменные suffix, а не URL, wildcard или IP-адреса.

## Постоянное состояние

Желаемое состояние хранится локально:

```text
/opt/etc/routerkit/routing-overrides.json
```

Файл owner-only и не содержит VPN-ключей или subscription secrets.

Обычный `install-xray-direct.sh` сначала устанавливает свежие сгенерированные Xray fragments, а затем, если persistent routing state существует, выполняет внутренний `reconcile`. Поэтому последующий setup/regenerate не должен молча уничтожать выбранные direct-исключения.

## Безопасность применения

`apply`:

1. читает текущие `03_inbounds.json`, `04_outbounds.json`, `05_routing.json`;
2. требует существующий единственный `direct` outbound с `protocol=freedom`;
3. ставит RouterKit direct-rule **перед** per-profile VLESS catch-all rules;
4. не меняет Proxy/Policy Netcraze, DNS, PPPoE, Wi-Fi или firewall;
5. создаёт rollback backup;
6. проверяет будущий config через `xray run -test -confdir` в staging directory;
7. атомарно публикует `05_routing.json`;
8. повторно проверяет активный Xray config;
9. перезапускает только `S23xray-direct` с полноценным Entware `PATH`;
10. проверяет Xray/listeners штатным RouterKit verifier и HTTPS через каждый локальный SOCKS-профиль;
11. только после успешной проверки фиксирует persistent desired state.

При ошибке после публикации routing fragment RouterKit пытается вернуть старый `05_routing.json` и повторно поднять предыдущий Xray state.

## Защита от чужих правил

RouterKit управляет только своим первым direct-domain rule. Если первым правилом уже находится неизвестный direct rule, команда fail-closed и не перезаписывает его.

Исключение: если persistent state ещё отсутствует, но первый rule **точно совпадает** с известным service pack, RouterKit может распознать его как уже существующий state. Это позволяет безопасно принять ранее проверенный live-rule `kinopoisk` без дублирования.

## DNS

Direct routing не отменяет требования к рабочему DNS path. Для TCP-only native Proxy policies DNS upstream должен быть доступен этим policy. На hardware-proven NC-3812 рабочим вариантом стали системные DoH upstream без привязки к конкретному WAN interface (`Any`), чтобы policy-specific DNS proxies могли их использовать.

Не привязывайте protected DNS к одному WAN-интерфейсу только потому, что сам DoH через него работает: policy, в которой этого интерфейса нет, может потерять DNS при полностью рабочем Xray/Proxy TCP path.
