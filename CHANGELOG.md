# Changelog

## 2.2 — 2026-10-21

- *Custom* now goes up to this computer's factory values (65 W long term / 121 W short term on the
  Dell Pro Micro Plus) instead of 45/45 W, so an in-between setting like 35/80 W is possible. The
  ceiling is read from the firmware limits recorded at boot; 45/45 W if they are unknown. A value
  above the factory limits is never written.
- README: status of the request to add the Dell Pro Micro Plus to the kernel's whitelist.

## 2.1 — 2026-10-13

- **Fixed: the short-term power burst did not work.** The service switched on the RAPL MMIO zone,
  which the firmware of the Dell Pro Micro Plus leaves off (0/0 W, a ~1 ms time window). With that
  window the long-term limit acted as a hard cap: under full load the CPU stayed at 35 W in *Faster*
  instead of bursting to 45 W. Now only the zones the firmware enables are set; a zone the firmware
  keeps off is left off (and switched back off if an older version turned it on).
- *Don't manage* hands a zone the firmware keeps off back by switching it off — never by writing a
  0 W limit into a live zone.
- Watts and "now set in CPU" are read from the MSR zone (the one the CPU runs on).
- A failed power-limit write no longer leaves the error message up until the service restarts.
- `uninstall.sh` puts back the power limits the computer booted with.
- The 35/45 W preset is now called **Faster** instead of *Stock*: the real Dell factory default is
  65/121 W, so "Stock" was misleading. The setting name in the config file stays `stock`.
- *Don't manage* now puts back the CPU power limits the firmware set at boot (Dell defaults, 65/121 W
  on the Dell Pro Micro Plus) right away. Before, the last preset stayed until the next reboot.
  The boot values are kept per RAPL zone (MSR and MMIO separately) in `/run/dellfan/boot-power.json`,
  written atomically, so a service restart does not lose them and a damaged record is ignored instead
  of stopping the service; if the service never changed the limits, it still writes nothing.
- Every 30 s the service checks the CPU power limits. A limit changed by another program (usually
  Intel `thermald` lowering it while the CPU is hot) is logged and shown in the app and on the page,
  and left alone; if it is still changed 5 minutes later on a cool CPU (below 60 °C), the chosen
  limit is set again.
- A RAPL zone that appears after the service started (the MMIO zone can show up a moment late at
  boot) is picked up, and gets the chosen limit if the firmware has it enabled.
- Power changes from the app, the web page and the checks no longer overlap.

## 2.0 — 2026-10-10

First public release, as **Dell Pro Fan Control** (repository `dell-pro-fancontrol`;
the service inside keeps the short name `dellfan`).

- Driver: `dell-smm-hwmon` with Dell Pro Micro Plus QBM1250 in the fan-control whitelist (DKMS),
  source fetched from kernel.org stable for the running kernel series.
- Service: fan curve (quiet / BIOS / max) with hysteresis, safety limit 60–85 °C, fail-safe to BIOS
  on stop, crash or hang (systemd watchdog), sensor failure → max.
- CPU power limits through Intel RAPL: Eco 25/35 W, Stock (now Faster) 35/45 W, custom 15–45 W, or off.
- Live status: temperature, fan speed, CPU load, CPU watts, one hour of history.
- Desktop app (GTK 4 + libadwaita, English / Russian) and web page (Russian).
- Web page access chosen in the app: this computer / whole network (view only by default) / off,
  port, the address to open. The page itself cannot change its own access.
- `install.sh` / `uninstall.sh` for Ubuntu / Debian, Secure Boot (MOK) guidance.

Versions 1.x were private development builds.
