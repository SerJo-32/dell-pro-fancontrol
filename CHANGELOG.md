# Changelog

## 2.0 — 2026-10-10

First public release, as **Dell Pro Fan Control** (repository `dell-pro-fancontrol`;
the service inside keeps the short name `dellfan`).

- Driver: `dell-smm-hwmon` with Dell Pro Micro Plus QBM1250 in the fan-control whitelist (DKMS),
  source fetched from kernel.org stable for the running kernel series.
- Service: fan curve (quiet / BIOS / max) with hysteresis, safety limit 60–85 °C, fail-safe to BIOS
  on stop, crash or hang (systemd watchdog), sensor failure → max.
- CPU power limits through Intel RAPL: Eco 25/35 W, Stock 35/45 W, custom 15–45 W, or off.
- Live status: temperature, fan speed, CPU load, CPU watts, one hour of history.
- Desktop app (GTK 4 + libadwaita, English / Russian) and web page (Russian).
- Web page access chosen in the app: this computer / whole network (view only by default) / off,
  port, the address to open. The page itself cannot change its own access.
- `install.sh` / `uninstall.sh` for Ubuntu / Debian, Secure Boot (MOK) guidance.

Versions 1.x were private development builds.
