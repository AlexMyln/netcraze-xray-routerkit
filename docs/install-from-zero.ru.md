# Установка с нуля — RU

Этот документ описывает общий путь от USB-накопителя до рабочей схемы Xray + Netcraze policies.

Важно: это не one-click installer. Часть шагов выполняется вручную через Web UI роутера.

## 1. Подготовить USB

Нужен USB-накопитель с EXT4-разделом.

Рекомендуется:

- нормальная брендовая флешка или USB SSD;
- EXT4;
- не хранить на накопителе лишние данные;
- не вытаскивать накопитель из роутера во время работы.

## 2. Установить компоненты на роутере

В Web UI роутера должны быть установлены компоненты:

- EXT filesystem support;
- Open Package support / OPKG;
- SSH server;
- при необходимости SMB/SFTP для удобной передачи файлов.

## 3. Установить Entware/OPKG

Установите Entware/OPKG на USB-накопитель штатным способом для вашей модели Netcraze/Keenetic.

После установки проверьте:

```sh
opkg update
```

## 4. Установить Xray

Xray должен быть доступен как:

```text
/opt/sbin/xray
```

Проверка:

```sh
/opt/sbin/xray version
```

## 5. Подготовить profiles.json локально

На компьютере скопируйте пример:

```sh
cp examples/profiles.example.json profiles.json
```

`profiles.json` игнорируется git и не должен попадать в публичный репозиторий.

Реальные subscription URLs храните локально или в environment variables.

## 6. Сгенерировать Xray configs

```sh
python3 scripts/generate-xray-profiles.py \
  --profiles profiles.json \
  --out generated
```

Generated configs могут содержать секреты, поэтому не коммитьте директорию `generated/`.

## 7. Установить generated configs на роутер

Скопируйте репозиторий или нужные файлы на роутер/Entware и выполните:

```sh
sh scripts/install-xray-direct.sh generated
```

Скрипт:

- сделает backup текущего `/opt/etc/xray`;
- установит generated configs;
- установит direct init script `S23xray-direct`;
- оставит старый `S24xray` выключенным;
- не вызовет `xkeen -start`.

## 8. Запустить и проверить

```sh
sh /opt/etc/init.d/S23xray-direct start
sh scripts/healthcheck.sh
```

Ожидаемо:

- Xray process есть;
- SOCKS-порты слушают только `127.0.0.1`;
- firewall check пустой по `xkeen|TPROXY|61219|1082|1083|1084`;
- IP через SOCKS отличается от direct IP.

## 9. Настроить Web UI

В Web UI создайте proxy connections:

```text
XRAY-PROFILE-A -> SOCKS5 127.0.0.1:1082
XRAY-PROFILE-B -> SOCKS5 127.0.0.1:1083
XRAY-PROFILE-C -> SOCKS5 127.0.0.1:1084
```

Затем создайте policies и назначьте только нужное устройство.

Не меняйте Default policy.

## 10. Включить autostart

Только после успешного теста:

```sh
chmod 755 /opt/etc/init.d/S23xray-direct
chmod 644 /opt/etc/init.d/S24xray
```

После этого можно сделать контролируемый reboot-test.

## 11. Backup

После успешной настройки сделайте два backup:

1. Router startup-config через Web UI.
2. Entware/Xray backup:

```sh
sh scripts/backup.sh
```

Backup может содержать секреты. Не публикуйте его.
