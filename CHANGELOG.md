# Changelog

## 2.5 — 2026-11-14

- **Response time** (like in FanControl): the curve must ask for a faster fan step for this many seconds
  in a row, and twice as long for a slower one, before the fan changes. The CPU sensor of the Dell Pro
  Micro Plus jumps by 10–17 °C for a second or two even at 1 % load, and every jump used to move the
  fan: one hour at idle had about 190 step changes. Replayed through the new logic, the same hour gives
  2 changes at 6 s and none at 10 s. Separate times for a faster and a slower step: default 6 s up
  (0–30) and 20 s down (0–60), so the heatsink cools before the fan slows down and bursty loads do
  not swing it (config `response_up` / `response_down`, 0 = at once as before). Safety, a lost sensor
  and the BIOS/Max modes never wait.
- **Safety limit confirmed by a second reading.** A single 1-second spike of the package sensor
  (81 °C, back to 62 °C the next second) set the fan to max with the safety reason. Now it takes
  2 readings in a row (4 s; the fan needs ~14 s to spin up anyway), or one reading 10 °C above the
  limit. A lost sensor still means max at once.

## 2.4 — 2026-11-12

- Web page: hovering over the history charts (or tapping them on a phone) shows the values at that
  moment — time, temperature, fan step and speed, CPU watts and load — with a marker line through
  both charts. Before, the charts could only be looked at. Each measurement has its own colour in the
  tip: temperature red, fan teal, watts amber, load blue.

## 2.3 — 2026-11-12

- **Linux 7.0 support.** Ubuntu 24.04 replaced the 6.17 OEM kernel with the 7.0 HWE kernel
  (`linux-oem-24.04d` → `linux-generic-hwe-24.04`). Before, the driver package held one source — the
  series it was installed on — and DKMS rebuilt that same 6.17 source for 7.0. It compiled, but it was
  the wrong driver version for that kernel.
- Driver package **1.1**: one patched source per kernel series (`src/dell-smm-hwmon-<series>.c`);
  `select-source.sh` picks the right one before each build. The installer prepares the running
  series, every installed one, and 6.17 and 7.0. DKMS builds only for these series
  (`BUILD_EXCLUSIVE_KERNEL`); for any other series the build is skipped instead of failing the kernel
  install — run `install.sh` again to add it. Series whose kernel already knows the model (Linux 7.5+,
  where the patch has been accepted) are skipped.
- The driver is built for every installed kernel that has headers, not only the running one.
- Upgrading from 2.0–2.2 removes the old 1.0 driver package; `uninstall.sh` removes both versions.
- README: *Ubuntu 24.04: OEM 6.17 → HWE 7.0* — Dell's factory `oem-flavour.cfg` keeps GRUB on the
  old 6.17 kernel; how to boot 7.0 (and try it once first).
- Tested on Dell Pro Micro Plus QBM1250, BIOS 1.20.0, kernels 6.17.0-1033-oem and 7.0.0-43-generic,
  Secure Boot on.

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
