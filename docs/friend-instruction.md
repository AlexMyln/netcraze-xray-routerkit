# End-user instruction: client internet modes

The router has separate internet modes for one selected client device.

Other devices remain on the normal/default internet connection.

## Normal mode

Use:

**CLIENT-PROFILE-A**

This is the main mode.

## Backup modes

If the normal mode does not work, switch the selected client to:

**CLIENT-PROFILE-B**

or:

**CLIENT-PROFILE-C**

## Direct mode

To disable proxy/VLESS for the selected client, return it to the default policy.

Then the client will use the normal SIM/WAN connection like other devices.

## Check current IP

Open on the selected client:

```text
https://api.ipify.org
```

If the IP differs from the direct SIM/WAN IP, the client is using one of the proxy modes.

## Do not touch

Do not change or delete:

- `XRAY-PROFILE-A`
- `XRAY-PROFILE-B`
- `XRAY-PROFILE-C`
- `CLIENT-PROFILE-A`
- `CLIENT-PROFILE-B`
- `CLIENT-PROFILE-C`
- default policy
- USB drive
- OPKG / Entware / Xray settings

## Fast recovery

If the selected client's internet stops working:

1. Open the router Web UI.
2. Find the selected client device.
3. Move it back to the default policy.
4. Test internet access again.
