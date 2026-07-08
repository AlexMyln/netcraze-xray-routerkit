# Анонс netcraze-xray-routerkit

Собрал небольшой public starter-kit для Netcraze/Keenetic-style роутеров: Entware/OPKG + Xray + локальные SOCKS-профили + ручное переключение устройств через Web UI policies.

Главная идея — безопасный и проверяемый режим без transparent proxy и без автоматической firewall-магии:

- Xray слушает только `127.0.0.1`;
- `xkeen -start` не используется;
- TPROXY/REDIRECT режим не включается;
- default policy роутера не трогается;
- через proxy можно отправить только выбранное устройство;
- конфиги генерируются из локальных ignored-файлов;
- в публичный репозиторий не попадают реальные подписки, backup-файлы и секреты.

Типовая схема:

```text
Entware on USB
↓
Xray direct init script
↓
127.0.0.1:1082 / 1083 / 1084
↓
Netcraze proxy connections
↓
Connection policies для выбранного клиента
```

Это не Docker image, не готовый образ флешки и не one-click installer. Это набор шаблонов, скриптов и документации, чтобы аккуратно собрать похожую схему и не заворачивать весь дом в proxy случайным движением мыши.

Репозиторий: https://github.com/AlexMyln/netcraze-xray-routerkit
