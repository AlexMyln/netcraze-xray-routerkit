# Модель выполнения autostart

Этот ADR описывает явную transaction для `S23xray-direct` autostart. Это часть #14; она не завершает hardware/reboot validation #16 и epic #5.

## Scope

Transaction управляет только `/opt/etc/init.d/S23xray-direct`, `/opt/etc/init.d/S24xray` и direct Xray runtime, запущенным проверенным init script. Она не настраивает Netcraze Web UI, proxy connections, policies, default policy, devices, firewall, TPROXY/REDIRECT, reboot, Entware activation, bootstrap package installation, Docker, databases или servers.

Production CLI status, verify, enable и disable всегда смотрят настоящий `/proc`. Synthetic proc data доступен только importable Python functions для tests.

## Verification

Runtime verification fail-closed. RouterKit читает PID file без following symlinks, затем проверяет:

- `/proc/<pid>/stat` start time до и после identity checks;
- executable device/inode identity для `/opt/sbin/xray`;
- exact command line: `/opt/sbin/xray run -confdir /opt/etc/xray/configs`;
- bounded `/proc/<pid>/fd` socket ownership;
- expected listeners на `127.0.0.1:1082`, `127.0.0.1:1083` и `127.0.0.1:1084`;
- expected port не открыт на non-loopback address.

Если process identity меняется во время verification, listener tables нельзя прочитать, fd ownership нельзя доказать, init directory unreadable/oversized или найден executable conflicting Xray init script, verification завершается failure.

## Enable Contract

Enable apply поддерживает только буквальный `/opt` на Linux. Он отклоняет symlink, non-regular files, hardlinked init scripts, unsafe `S23xray-direct`, unsafe `S24xray`, missing executable Xray, missing/symlinked/non-directory Xray config directory, identity change config directory во время inspection и executable Xray init conflicts.

Если `S23xray-direct` уже enabled, `S24xray` disabled, installed template совпадает и runtime verification проходит, enable является verified no-op:

- `runtime_verified=true`;
- `restart_performed=false`;
- `restart_verified=false`.

Иначе enable выключает `S24xray`, временно выключает `S23xray-direct`, вызывает проверенный init script через `sh ... restart` и проверяет runtime до включения `S23xray-direct`. Если verified process работал до restart, post-restart identity обязан быть другим process epoch. PID reuse допустим только с другим `/proc/<pid>/stat` start time. Неизменившийся epoch — failure.

Успешный fresh/recovery message:

```text
Autostart enabled and restart-verified.
```

Verified no-op message:

```text
Autostart already enabled and runtime-verified; no restart was performed.
```

## Rollback

До mutation transaction сохраняет mode state и verified runtime state. При failure она восстанавливает исходный mode `S23xray-direct`, оставляет `S24xray` non-executable, удаляет stale autostart receipt state и доказывает runtime outcome:

- если Xray был verified running до transaction, RouterKit делает одну bounded попытку start через проверенный `S23xray-direct` и требует runtime verification;
- если Xray был verified not running до transaction, но transaction его запустила, RouterKit останавливает через проверенный init script и требует, чтобы matching runtime verification больше не проходила.

Rollback start/stop выполняется как recovery-critical section. Исходный catchable signal остаётся recorded, но не может отменить recovery child; новые catchable signals записываются и откладываются вместо forwarding в recovery child. Только первый signal определяет ordinary signal exit после завершения recovery и teardown. Если rollback или recovery cleanup нельзя доказать, enable выходит с `3` и печатает safe manual disable guidance. Rollback failures не понижаются до ordinary signal или generic failure.

## Disable Contract

Disable поддерживает только буквальный `/opt`. Он использует `lstat`/lexists semantics, поэтому dangling symlinks и special files отклоняются. После начала disable apply catchable signals записываются и откладываются, пока RouterKit не выключит `S24xray`, затем `S23xray-direct`, не проверит оба final modes, не удалит stale receipt state и не восстановит signal lifecycle state. После этого возвращается deferred signal code, если signal был recorded. Disable не останавливает runtime.

## Init Script

`S23xray-direct` fail-closed при process evidence. Он требует `/proc/<pid>`, readable `exe`, `cmdline` и `stat`, stable start time, exact executable/cmdline evidence и `kill -0`. Перед TERM и KILL он повторно проверяет PID plus start time plus executable/cmdline, bounded waits после каждого signal и возвращает failure, если original process epoch выжил.

Script публикует PID через owner-only temp file внутри private lock directory. Он отслеживает active direct child PID plus start time для текущего invocation. PID publication failure, start verification failure и catchable signal traps очищают этот active child через bounded exact-epoch TERM/KILL, удаляют только matching active PID file и не печатают success, если cleanup нельзя доказать. Signal traps возвращают `3` вместо `129`, `130` или `143`, когда active-child cleanup нельзя доказать. Lock path обязан быть real directory, записывает owner PID and start time, устанавливает catchable signal traps, освобождает только lock текущего invocation, удаляет только proven stale locks и fail-closed, если ownership unclear. Ошибка release owned lock также возвращает `3` из signal traps и не считается clean stop.

## Signals And JSON

Python apply владеет direct init child с `start_new_session=True` на POSIX, записывает первый catchable signal, forwards `SIGINT`, `SIGTERM` и `SIGHUP` только вне recovery-critical sections и сохраняет ownership до terminal/reaped child. Parent blocks handled signals during handler install and child spawn; child restores previous mask before exec. Cleanup, rollback и signal handler/mask teardown завершаются до final signal exit; rollback failure exit `3` имеет priority перед ordinary signal codes. Setup и install supervisors сохраняют meaningful nonzero autostart child codes и не печатают success summaries после autostart failure.

`--json` apply захватывает init stdout/stderr через bounded drain и выводит ровно один JSON document в stdout. JSON не содержит raw logs, config content, endpoints, command lines или PIDs. Если child output превышает bound, transaction fails safely после reaped child.

## Receipt Decision

Предыдущий autostart receipt не используется для idempotency или trust decisions. В этом milestone он удалён из trust boundary; stale receipt state удаляется во время enable/disable cleanup.

## Residual Risk

Reboot не выполняется и не доказывается. После реальной перезагрузки выполните:

```sh
python3 scripts/routerkit.py autostart --verify
```

Hardware canary, idempotency, reboot persistence и rollback matrix validation остаются в #16.
