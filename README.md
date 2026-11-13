# Dell Pro Fan Control — fan curve and CPU power limit for Dell desktops on Linux

**English** · [Русский](README.ru.md)

On some new Dell desktops the BIOS keeps the fan at ~1750 rpm and Linux has no way to change it:
the kernel driver `dell-smm-hwmon` only allows manual fan control on models from its whitelist.
**Dell Pro Fan Control** adds the model to that whitelist (a small DKMS driver patch) and runs a service with a
temperature curve, a hard safety limit and optional CPU power limits — with a desktop app and a web page.

| Desktop app (GTK 4) | Web page |
|---|---|
| ![Desktop app](docs/screenshots/app-en.png) | ![Web page](docs/screenshots/web.png) |

> **Status:** tested on one model only — **Dell Pro Micro Plus QBM1250** (BIOS 1.18.2 and 1.20.0,
> Ubuntu 24.04, kernels 6.17 OEM and 7.0 HWE). It changes how your hardware is cooled. Use at your own risk
> and read [Safety](#safety) first.

Inside, the service is called `dellfan` (systemd unit, `/opt/dellfan`, group `dellfan`).

> Unofficial project, not affiliated with or endorsed by Dell Inc. "Dell" and "Dell Pro" are trademarks of Dell Inc.

## What it does

- **Fan curve** with three real fan levels the hardware supports:
  quiet (~1000 rpm) · BIOS auto (~1750 rpm) · max (~4000 rpm). You drag zone boundaries on a
  temperature ruler, with hysteresis and a response time so the fan does not flap between levels
  on short spikes of the CPU sensor.
- **Safety limit** (60–85 °C): at or above it the fan is always at max, whatever the curve says.
- **CPU power limit** (Intel RAPL): presets *Eco 25/35 W*, *Faster 35/45 W*, your own values
  (15 W up to this computer's factory values, 65/121 W here), or *Don't manage* to leave it to the firmware. Note: the Dell factory default is much
  higher — **65 W long term / 121 W short term** on the Dell Pro Micro Plus — and *Don't manage* puts
  back the values the firmware set at boot right away (no reboot needed). Only the RAPL zones the
  firmware itself enables are set (on this model the MSR one; the MMIO zone stays off, as Dell
  leaves it). Every 30 s the service checks the limit: a change by another program (usually Intel
  `thermald` while the CPU is hot) is shown in the app and the page and left alone; if it is still
  there 5 minutes later on a cool CPU, the chosen limit is set again. In this small case that means noticeably higher
  temperatures.
- **Live view:** temperature, fan speed, CPU load and watts, history for 5 min / 30 min / 1 hour.
- **Desktop app** (GTK 4 + libadwaita, English/Russian) and a **web page** (Russian for now).

## Safety

The service is built so that every failure ends with a working fan:

| Situation | Fan |
|---|---|
| CPU at or above the safety limit | max, always |
| temperature sensor cannot be read | max |
| the service stops, crashes or hangs | handed back to BIOS (systemd `ExecStopPost` + watchdog) |
| driver without fan control (e.g. after a kernel series upgrade) | BIOS — the service refuses to start |
| uninstall | BIOS |

The safety limit cannot be set above 85 °C, and curve points must stay below it.
The app and the web page never touch the hardware: all decisions are made by the service.

## Requirements

- A Dell desktop where `dell-smm-hwmon` works (see [Other Dell models](#other-dell-models))
- Ubuntu / Debian with systemd (the installer uses `apt`), Python 3
- Internet access during installation (driver source is downloaded from kernel.org)
- For the app: GTK 4 and libadwaita 1.5+ (Ubuntu 24.04 or newer)

## Install

```sh
git clone https://github.com/SerJo-32/dell-pro-fancontrol.git
cd dell-pro-fancontrol
sudo ./install.sh
```

The installer:

1. checks the model (refuses on untested models unless `--force`);
2. downloads `dell-smm-hwmon.c` from kernel.org for **every kernel series it needs** — the running one,
   every installed one, and 6.17 and 7.0 (Ubuntu 24.04 moves from one to the other) — applies the patch
   from [`driver/`](driver/) to each and builds it with DKMS. Kernel updates within these series are
   rebuilt automatically, each from the source of its own series; a series whose kernel already knows
   the model (Linux 7.5+) is skipped;
3. installs the service to `/opt/dellfan`, creates the `dellfan` group, enables `dellfan.service`;
4. installs the app ("Dell Pro Fan Control" in the menu) and adds you to the `dellfan` group.

Options: `--no-gui` (servers), `--no-driver` (your kernel already supports fan control on your model),
`--force` (skip the model check).

### Secure Boot

With Secure Boot on, the kernel only loads modules signed by a trusted key. The installer signs the
driver with the standard Ubuntu MOK key (creating one if needed) and, the first time, asks you for a
**one-time password**. Then:

1. reboot;
2. a blue **MOK management** screen appears (it waits 10 seconds — press any key);
3. **Enroll MOK → Continue → Yes →** type the password **→ Reboot**.

This happens once. Later kernel updates are signed automatically.

## Use

- **App:** "Dell Pro Fan Control" in the applications menu (log out and back in once after installing,
  so the `dellfan` group membership applies).
- **Web page:** by default it opens only **on the Dell itself**: `http://127.0.0.1:8095`.
  Who can open it is set in the app, **Settings → Web page**:

  | Access | Who can open the page |
  |---|---|
  | **This computer** (default) | only a browser on the Dell |
  | **Whole network** | any device at home: phone, laptop. **View only** is on by default — settings can then be changed only in the app |
  | **Off** | nobody, the page is not running |

  The app shows the exact address to open (e.g. `http://192.168.1.50:8095`) and lets you change the
  port if 8095 is taken by another program. The page has no password, so turning *view only* off
  shows a warning. Access can be changed **only in the app** — the page itself cannot open itself to
  the network. Fan control keeps working whatever happens to the page.
  - No monitor on the Dell and you'd rather not open the page to the network? Use an SSH tunnel:
    `ssh -L 8095:127.0.0.1:8095 user@dell-address`, then open `http://127.0.0.1:8095` on your laptop.
- **Status and log:** `systemctl status dellfan`, `journalctl -u dellfan -b`

### Settings file

`/etc/dellfan/config.json` is written by the app and the web page; you rarely need to edit it.

```json
{
  "mode": "curve",
  "base_level": "quiet",
  "points": [{"temp": 50, "level": "bios"}, {"temp": 65, "level": "max"}],
  "hysteresis": 4,
  "response_time": 6,
  "safety_max": 75,
  "power": {"profile": "eco"},
  "web": {"access": "local", "port": 8095, "network_readonly": true}
}
```

- `mode` — `curve`, `bios` (Dell's own behaviour) or `max`
- `points` — from this temperature (°C) use this level: `quiet`, `bios` or `max`
- `hysteresis` — a level goes down only when the CPU is this many degrees below the boundary
- `response_time` — 0…30 s: the curve must ask for a faster level this long in a row (twice as long
  for a slower one) before the fan changes; 0 = at once. Safety and a lost sensor never wait
- `safety_max` — 60…85 °C
- `power.profile` — `eco` (25/35 W), `stock` (35/45 W, shown as *Faster*), `custom` with `pl1`/`pl2`, or `off`
- `web.access` — `local`, `network` or `off`; `web.port` — 1024…65535; `web.network_readonly`
  (`/etc/default/dellfan` only gives the starting value on first run)

### API

Both the app socket `/run/dellfan/dellfan.sock` (group `dellfan`) and the web port speak HTTP + JSON:
`GET /api/status` (add `?since=<unix time>` to limit history), `POST /api/config` with a partial config.

## Uninstall

```sh
sudo ./uninstall.sh          # keeps /etc/dellfan
sudo ./uninstall.sh --purge  # removes settings and the group too
```

The fan goes back to BIOS control and the kernel's own driver is loaded again.
The Secure Boot key stays enrolled (other DKMS modules may use it); remove it with
`sudo mokutil --delete /var/lib/shim-signed/mok/MOK.der` if you want.

## Troubleshooting

| Symptom | What to check |
|---|---|
| `dellfan` is inactive, log says *pwm1_enable not found* | the patched driver is not loaded: `dkms status`, `modinfo -F filename dell-smm-hwmon` should point to `updates/dkms` |
| driver builds but does not load, *Key was rejected* | Secure Boot key not enrolled yet — reboot and finish the blue MOK screen |
| driver stopped working after a kernel update to a new series (e.g. 7.0 → 7.1) | DKMS skips series it has no source for — run `sudo ./install.sh` again, it adds the source for every installed kernel |
| Ubuntu 24.04 installed kernel 7.0, but `uname -r` still shows 6.17 OEM | see [Ubuntu 24.04: OEM 6.17 → HWE 7.0](#ubuntu-2404-oem-617--hwe-70) |
| the app says it cannot reach the service | log out and back in (group `dellfan`), or check `systemctl status dellfan` |

## Ubuntu 24.04: OEM 6.17 → HWE 7.0

In November 2026 Ubuntu ended the OEM kernel line on 24.04: the packages `linux-oem-24.04d` and
`linux-image-oem-24.04d` now pull the regular HWE kernel **7.0** (`linux-generic-hwe-24.04`).
Version 2.3 prepares the driver for both 6.17 and 7.0, so the fan keeps working after the switch.

On computers that came with Dell's Ubuntu image, the file `/etc/default/grub.d/oem-flavour.cfg`
(from the `oem-*-meta` package, `GRUB_FLAVOUR_ORDER=oem`) makes GRUB keep booting the **old 6.17 OEM**
kernel even after 7.0 is installed. If you want 7.0 to boot, add a file that is read after it:

```sh
echo 'GRUB_FLAVOUR_ORDER=generic' | sudo tee /etc/default/grub.d/zz-kernel-flavour.cfg
sudo update-grub
```

The 6.17 kernel stays in *Advanced options for Ubuntu* as a fallback. Tip: to try 7.0 once first,
use `sudo grub-reboot "Advanced options for Ubuntu>Ubuntu, with Linux 7.0.0-43-generic"`
(needs `GRUB_DEFAULT=saved`); the next reboot goes back to the default.

## Other Dell models

The patch adds exactly one model: `Dell Pro Micro Plus QBM1250`, using the `30A3/31A3` fan-control
method. Other recent Dell desktops may work the same way, but **do not assume it**:

1. check `cat /sys/class/dmi/id/product_name`;
2. edit the model name in [`driver/dell-smm-hwmon-qbm1250.patch`](driver/dell-smm-hwmon-qbm1250.patch)
   and the `SUPPORTED_MODELS` line in `install.sh`;
3. install, then watch `cat /sys/class/hwmon/hwmon*/fan1_input` while switching modes in the app.

If it works (or not), please [open an issue](../../issues) with your model, BIOS version and
results — that is how the model list grows.

## Upstream

The right long-term fix is to add the model to the kernel's whitelist. Once that lands in your
kernel, install with `--no-driver` (or the installer will notice and skip the patch).

Status: requested in [Wer-Wolf/i8kutils#20](https://github.com/Wer-Wolf/i8kutils/issues/20)
(Dell Pro Micro Plus QBM1250). The patch "hwmon: (dell-smm) Add Dell Pro Micro Plus QBM1250 to fan
control whitelist" (Armin Wolf, co-developed and tested by Sergei) was **applied to hwmon-next in
October 2026** and is expected in **Linux 7.5**. Until it reaches the
kernel your distribution ships, the patched driver from this project is still needed.

## License

[GPL-2.0-or-later](LICENSE). The driver part is derived from the Linux kernel's
`drivers/hwmon/dell-smm-hwmon.c`.

## Credits

- `dell-smm-hwmon` — Massimo Dal Zotto, Jean Delvare, Guenter Roeck, Pali Rohár, Armin Wolf
  and other kernel contributors
- [i8kutils](https://github.com/Wer-Wolf/i8kutils) — the reference user-space tools for this driver
