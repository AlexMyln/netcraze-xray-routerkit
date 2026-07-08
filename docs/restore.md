# Restore notes

There are two independent restore layers:

1. Router Web UI startup-config backup.
2. Entware/Xray backup archive.

## Router config restore

Use the router Web UI to restore the saved startup-config.

This restores proxy connections and connection policies.

## Entware/Xray restore

Copy backup archive to the router, then extract selected files carefully.

Example:

```sh
cd /opt/backups
tar -tzf final-netcraze-xray-YYYYMMDD-HHMMSS.tar.gz
```

To restore Xray config:

```sh
sh /opt/etc/init.d/S23xray-direct stop
cp -a /opt/etc/xray /opt/etc/xray.before-restore.$(date +%Y%m%d-%H%M%S)
cp -a /opt/backups/final-netcraze-xray-YYYYMMDD-HHMMSS/xray /opt/etc/xray
/opt/sbin/xray run -test -confdir /opt/etc/xray/configs
sh /opt/etc/init.d/S23xray-direct start
```

Do not restore secrets from untrusted archives.
