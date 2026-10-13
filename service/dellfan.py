#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""dellfan — fan curve + CPU power limit service for Dell desktops (dell-smm-hwmon).

Needs a dell-smm-hwmon build that exposes pwm1_enable (model in the fan-control
whitelist). Fan levels on Dell Pro Micro Plus QBM1250: pwm 128 ≈ 1000 rpm,
BIOS auto ≈ 1750, pwm 255 ≈ 4000. Python 3 standard library only.

API (HTTP+JSON):
  * unix socket  /run/dellfan/dellfan.sock  — for the desktop app, group "dellfan"
  * TCP web page — config "web": access local (127.0.0.1) / network (all
    interfaces) / off, port, network_readonly. Only the app socket may change
    "web" — the page can never open itself to the network.
    GET /api/status, POST /api/config (partial update), GET / (web page)
  DELLFAN_WEB / DELLFAN_HOST / DELLFAN_PORT / DELLFAN_WEB_READONLY in
  /etc/default/dellfan only give the starting value of "web".

Safety (not negotiable):
  * CPU at/above safety_max (60..85 °C, never above SAFETY_CEILING) -> fan at max;
  * temperature cannot be read                                    -> fan at max;
  * service stops / crashes / hangs -> systemd ExecStopPost hands the fan to BIOS.
"""
import grp, json, os, socket, socketserver, subprocess, sys, threading, time, traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "2.0"
CONFIG_PATH = os.environ.get("DELLFAN_CONFIG", "/etc/dellfan/config.json")
SOCKET_PATH = os.environ.get("DELLFAN_SOCKET", "/run/dellfan/dellfan.sock")
SOCKET_GROUP = os.environ.get("DELLFAN_GROUP", "dellfan")
# CPU power limits the firmware set at boot; /run is cleared on reboot, so a service restart keeps them
BOOT_POWER_PATH = os.environ.get("DELLFAN_BOOT_POWER", "/run/dellfan/boot-power.json")
WEB = os.environ.get("DELLFAN_WEB", "0") == "1"
WEB_READONLY = os.environ.get("DELLFAN_WEB_READONLY", "0") == "1"
WEB_LISTEN = (os.environ.get("DELLFAN_HOST", "127.0.0.1"), int(os.environ.get("DELLFAN_PORT", "8095")))
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

INTERVAL = 2.0              # seconds between decisions
HISTORY = 1800              # points in memory (1 hour)
SAFETY_CEILING = 85         # safety_max can never be set above this
SAFETY_FLOOR = 60
LEVELS = {"quiet": 128, "bios": None, "max": 255}   # None = automatic BIOS control
MODES = ("curve", "bios", "max")
POWER_PROFILES = {"eco": (25, 35), "stock": (35, 45)}
POWER_MIN, POWER_MAX = 15, 45   # watts allowed for "custom"
FOREIGN_GRACE, FOREIGN_COOL = 300, 60   # limit changed by someone else: wait 5 min, CPU below 60 °C
EXIT_NO_DRIVER = 3              # systemd: RestartPreventExitStatus=3 — no point restarting

WEB_ACCESS = ("local", "network", "off")
WEB_HOSTS = {"local": "127.0.0.1", "network": "0.0.0.0"}


def web_default():
    """Starting value of config "web", from the environment (older installs set it there)."""
    if not WEB:
        access = "off"
    elif WEB_LISTEN[0] in ("127.0.0.1", "localhost", "::1"):
        access = "local"
    else:
        access = "network"
    return {"access": access, "port": WEB_LISTEN[1],
            "network_readonly": WEB_READONLY if access == "network" else True}


DEFAULT_CONFIG = {
    "mode": "curve",
    "base_level": "quiet",
    "points": [{"temp": 50, "level": "bios"}, {"temp": 62, "level": "max"}],
    "hysteresis": 4,
    "safety_max": 75,
    "power": {"profile": "off"},          # off = leave CPU power limits alone
    "web": web_default(),
}


def log(*a):
    print(*a, flush=True)


def find_hwmon(name):
    base = "/sys/class/hwmon"
    for d in sorted(os.listdir(base)):
        try:
            with open(os.path.join(base, d, "name")) as f:
                if f.read().strip() == name:
                    return os.path.join(base, d)
        except OSError:
            pass
    return None


def rapl_zones():
    """Top-level CPU package zones of both RAPL interfaces (MSR and MMIO)."""
    out = []
    base = "/sys/class/powercap"
    for d in sorted(os.listdir(base)):
        if d.count(":") != 1 or not d.startswith("intel-rapl"):
            continue
        p = os.path.join(base, d)
        try:
            with open(os.path.join(p, "name")) as f:
                if f.read().strip().startswith("package"):
                    out.append(p)
        except OSError:
            pass
    return out


def read_int(path):
    with open(path) as f:
        return int(f.read().strip())


def write(path, value):
    with open(path, "w") as f:
        f.write(str(value))


def sd_notify(msg):
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.connect(addr)
        s.sendall(msg.encode())


def validate(cfg):
    """Full config in, clean config out, or ValueError with a readable reason."""
    mode = cfg.get("mode", "curve")
    if mode not in MODES:
        raise ValueError("mode: one of %s" % (MODES,))
    base = cfg.get("base_level", "quiet")
    if base not in LEVELS:
        raise ValueError("base_level: one of %s" % list(LEVELS))
    safety = int(cfg.get("safety_max", 75))
    if not SAFETY_FLOOR <= safety <= SAFETY_CEILING:
        raise ValueError("safety_max: %d..%d °C" % (SAFETY_FLOOR, SAFETY_CEILING))
    pts = cfg.get("points", [])
    if not isinstance(pts, list) or len(pts) > 6:
        raise ValueError("points: up to 6")
    clean = []
    for p in pts:
        t, lv = int(p["temp"]), p["level"]
        if not 30 <= t < safety:
            raise ValueError("curve point %d °C must be between 30 and safety limit %d °C" % (t, safety))
        if lv not in LEVELS:
            raise ValueError("point level: one of %s" % list(LEVELS))
        clean.append({"temp": t, "level": lv})
    clean.sort(key=lambda p: p["temp"])
    if len({p["temp"] for p in clean}) != len(clean):
        raise ValueError("two curve points at the same temperature")
    hyst = int(cfg.get("hysteresis", 4))
    if not 1 <= hyst <= 10:
        raise ValueError("hysteresis: 1..10 °C")
    pw = cfg.get("power", {"profile": "off"}) or {"profile": "off"}
    prof = pw.get("profile", "off")
    if prof == "custom":
        pl1, pl2 = int(pw.get("pl1", 25)), int(pw.get("pl2", 35))
        if not (POWER_MIN <= pl1 <= POWER_MAX and POWER_MIN <= pl2 <= POWER_MAX):
            raise ValueError("power: %d..%d W" % (POWER_MIN, POWER_MAX))
        if pl2 < pl1:
            raise ValueError("power: short-term limit must be ≥ long-term")
        power = {"profile": "custom", "pl1": pl1, "pl2": pl2}
    elif prof in POWER_PROFILES or prof == "off":
        power = {"profile": prof}
    else:
        raise ValueError("power profile: eco, stock, custom or off")
    w = cfg.get("web") or web_default()
    access = w.get("access", "local")
    if access not in WEB_ACCESS:
        raise ValueError("web access: local, network or off")
    port = int(w.get("port", 8095))
    if not 1024 <= port <= 65535:
        raise ValueError("web port: 1024..65535")
    web = {"access": access, "port": port, "network_readonly": bool(w.get("network_readonly", True))}
    return {"mode": mode, "base_level": base, "points": clean, "hysteresis": hyst,
            "safety_max": safety, "power": power, "web": web}


class Controller:
    def __init__(self):
        self.lock = threading.Lock()
        self.fan = find_hwmon("dell_smm")
        self.cpu = find_hwmon("coretemp")
        self.rapl = rapl_zones()
        if not self.fan or not os.path.exists(os.path.join(self.fan, "pwm1_enable")):
            log("dell_smm hwmon with pwm1_enable not found — is the patched driver loaded? "
                "The fan stays under BIOS control. See README → Troubleshooting.")
            sys.exit(EXIT_NO_DRIVER)
        self.config = self.load()
        self.level, self.reason, self.temp, self.watts, self.load = None, "start", None, None, None
        self.history = deque(maxlen=HISTORY)
        self.last_error = None
        self._energy = None
        self._cpu_times = None
        self.power_lock = threading.RLock()  # one power change at a time (app socket + web page + checks)
        self.power_expected = None           # (pl1, pl2) W we set, None when power is not managed
        self.power_foreign = None            # [[pl1, pl2], ...] W set by someone else — shown in the app
        self._power_seen = {}                # zone -> last foreign value already logged
        self._foreign_since = None           # monotonic time the foreign limit was first seen
        self._steps = 0
        self.boot_power = self.load_boot_power()
        self.apply_power()

    # ---- config ----
    def load(self):
        try:
            with open(CONFIG_PATH) as f:
                return validate({**DEFAULT_CONFIG, **json.load(f)})
        except FileNotFoundError:
            return validate(DEFAULT_CONFIG)
        except Exception as e:
            log("config ignored:", e)
            return validate(DEFAULT_CONFIG)

    def update(self, patch):
        with self.lock:
            cfg = validate({**self.config, **patch})
            web_changed = cfg["web"] != self.config["web"]
        if web_changed:
            WEBSERVER.apply(cfg["web"])        # ValueError if it cannot listen — nothing is saved
        with self.lock:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            tmp = CONFIG_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG_PATH)
            power_changed = cfg["power"] != self.config["power"]
            self.config = cfg
        if power_changed:
            self.apply_power()
        return cfg

    # ---- hardware ----
    def power_limits(self):
        if not self.rapl:
            return None
        z = self.rapl[0]
        try:
            return {"pl1": read_int(z + "/constraint_0_power_limit_uw") // 1000000,
                    "pl2": read_int(z + "/constraint_1_power_limit_uw") // 1000000}
        except OSError:
            return None

    # Boot record, kept in /run (cleared on reboot, kept across service restarts):
    #   {"zones": {"/sys/class/powercap/intel-rapl:0": {"pl1_uw": .., "pl2_uw": .., "enabled": 1}, ...},
    #    "written": true}   — "written": this service changed the limits since boot
    @staticmethod
    def read_zone(z):
        try:
            return {"pl1_uw": read_int(z + "/constraint_0_power_limit_uw"),
                    "pl2_uw": read_int(z + "/constraint_1_power_limit_uw"),
                    "enabled": read_int(z + "/enabled")}
        except (OSError, ValueError):
            return None

    def load_boot_power(self):
        """Limits as the firmware set them at boot, per zone. Recorded once per boot, before we write."""
        if not self.rapl:
            return None
        try:
            with open(BOOT_POWER_PATH) as f:
                data = json.load(f)
        except FileNotFoundError:
            data = None
        except (OSError, ValueError) as e:
            log("boot power record unreadable (%s) — \"Don't manage\" will leave the current limits" % e)
            return None
        if data is not None:
            if isinstance(data, dict) and "pl1" in data and "zones" not in data:     # record from 2.0-dev
                data = {"zones": {z: {"pl1_uw": int(data["pl1"]) * 1000000, "pl2_uw": int(data["pl2"]) * 1000000,
                                      "enabled": 1} for z in self.rapl}, "written": bool(data.get("written"))}
            try:
                zones = data["zones"]
                for v in zones.values():
                    int(v["pl1_uw"]), int(v["pl2_uw"]), int(v["enabled"])
                data["written"] = bool(data.get("written"))
            except (KeyError, TypeError, ValueError, AttributeError):
                log("boot power record malformed — \"Don't manage\" will leave the current limits")
                return None
        else:
            data = {"zones": {}, "written": False}
        for z in self.rapl:                       # zones never seen this boot: their values are still firmware's
            if z not in data["zones"] and (v := self.read_zone(z)):
                data["zones"][z] = v
        self.save_boot_power(data)
        return data

    def save_boot_power(self, data):
        """Whole file or nothing — a power cut while writing must not leave half a record."""
        try:
            os.makedirs(os.path.dirname(BOOT_POWER_PATH), exist_ok=True)
            tmp = BOOT_POWER_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, BOOT_POWER_PATH)
        except OSError as e:
            log("cannot save boot power limits:", e)

    def write_zone(self, z, pl1_uw, pl2_uw, enabled=1):
        try:
            write(z + "/constraint_0_power_limit_uw", pl1_uw)
            write(z + "/constraint_1_power_limit_uw", pl2_uw)
            write(z + "/enabled", enabled)
        except OSError as e:
            self.last_error = "power limit %s: %s" % (os.path.basename(z), e)

    def apply_power(self):
        with self.power_lock:
            pw = self.config["power"]
            if not self.rapl:
                return
            bp = self.boot_power
            self.power_foreign, self._power_seen, self._foreign_since = None, {}, None
            if pw["profile"] == "off":
                self.power_expected = None
                # "leave alone": write nothing — unless we changed the limits earlier this boot,
                # then hand each zone its firmware values back, once
                if bp and bp["written"]:
                    for z in self.rapl:
                        v = bp["zones"].get(z)
                        if v:
                            self.write_zone(z, v["pl1_uw"], v["pl2_uw"], v["enabled"])
                    bp["written"] = False
                    self.save_boot_power(bp)
                    log("power limit off: restored boot values " + ", ".join(
                        "%s %d/%d W" % (os.path.basename(z), v["pl1_uw"] // 1000000, v["pl2_uw"] // 1000000)
                        for z, v in bp["zones"].items() if z in self.rapl))
                return
            pl1, pl2 = POWER_PROFILES.get(pw["profile"], (pw.get("pl1"), pw.get("pl2")))
            for z in self.rapl:
                self.write_zone(z, pl1 * 1000000, pl2 * 1000000)
            self.power_expected = (pl1, pl2)
            if bp and not bp["written"]:
                bp["written"] = True
                self.save_boot_power(bp)
            log("power limit %s: %d/%d W" % (pw["profile"], pl1, pl2))

    def check_power(self):
        """Every 30 s: pick up a RAPL zone that appeared after start, and notice limits changed
        by someone else (thermald, BIOS, another tool). They are reported and left alone while the
        CPU is hot; if still changed after FOREIGN_GRACE on a cool CPU, ours is set again."""
        with self.power_lock:
            for z in rapl_zones():
                if z in self.rapl:
                    continue
                self.rapl.append(z)
                if self.boot_power is not None and z not in self.boot_power["zones"] and (v := self.read_zone(z)):
                    self.boot_power["zones"][z] = v
                    self.save_boot_power(self.boot_power)
                if self.power_expected:
                    pl1, pl2 = self.power_expected
                    self.write_zone(z, pl1 * 1000000, pl2 * 1000000)
                log("power zone appeared after start: %s%s" % (
                    z, " — limit %d/%d W set" % self.power_expected if self.power_expected else ""))
            if not self.power_expected:
                self.power_foreign = None
                return
            foreign = {}
            for z in self.rapl:
                v = self.read_zone(z)
                got = (v["pl1_uw"] // 1000000, v["pl2_uw"] // 1000000) if v else None
                if got and got != tuple(self.power_expected):      # whole watts: no alarm on hardware rounding
                    foreign[z] = got
            for z, got in foreign.items():
                if self._power_seen.get(z) != got:
                    log("power limit on %s changed outside dellfan: %d/%d W (set %d/%d W) — left as is" % (
                        os.path.basename(z), got[0], got[1], *self.power_expected))
            self._power_seen = foreign
            self.power_foreign = [list(g) for g in sorted(set(foreign.values()))] or None
            if not foreign:
                self._foreign_since = None
                return
            now = time.monotonic()
            self._foreign_since = self._foreign_since or now
            # thermald lowers the limit only while the CPU is hot and normally puts it back itself.
            # Still changed 5 min later on a cool CPU → stale or another tool: set ours again (at most every 5 min).
            if now - self._foreign_since >= FOREIGN_GRACE and self.temp is not None and self.temp < FOREIGN_COOL:
                log("power limit still changed after %d min on a cool CPU (%.0f °C) — setting %d/%d W again" % (
                    FOREIGN_GRACE // 60, self.temp, *self.power_expected))
                self.apply_power()

    def read_temp(self):
        return read_int(os.path.join(self.cpu, "temp1_input")) / 1000.0   # Package id 0

    def read_watts(self):
        if not self.rapl:
            return None
        try:
            e, now = read_int(self.rapl[0] + "/energy_uj"), time.monotonic()
        except OSError:
            return None
        prev, self._energy = self._energy, (e, now)
        if not prev or e < prev[0] or now <= prev[1]:
            return None
        return round((e - prev[0]) / 1e6 / (now - prev[1]), 1)

    def read_load(self):
        """CPU busy %, all cores, since the previous call (from /proc/stat)."""
        try:
            with open("/proc/stat") as f:
                v = [int(x) for x in f.readline().split()[1:]]
        except (OSError, ValueError):
            return None
        idle, total = v[3] + v[4], sum(v[:8])      # idle + iowait; steal included, guest already in user
        prev, self._cpu_times = self._cpu_times, (idle, total)
        if not prev or total <= prev[1]:
            return None
        return round(100.0 * (1 - (idle - prev[0]) / (total - prev[1])), 1)

    def apply_fan(self, level):
        pwm = LEVELS[level]
        if pwm is None:
            write(os.path.join(self.fan, "pwm1_enable"), 2)
        else:   # re-assert every cycle: some BIOSes take the fan back on their own
            write(os.path.join(self.fan, "pwm1_enable"), 1)
            write(os.path.join(self.fan, "pwm1"), pwm)

    # ---- decision ----
    def curve_level(self, t):
        cfg = self.config
        pts, h = cfg["points"], cfg["hysteresis"]
        target = cfg["base_level"]
        for p in pts:
            if t >= p["temp"]:
                target = p["level"]
        cur = self.level
        if cur is not None and cur != target:
            order = [cfg["base_level"]] + [p["level"] for p in pts]
            cur_idx = max((i for i, lv in enumerate(order) if lv == cur), default=0)
            new_idx = max((i for i, lv in enumerate(order) if lv == target), default=0)
            if new_idx < cur_idx and cur_idx >= 1 and t > pts[cur_idx - 1]["temp"] - h:
                return cur      # going down: wait for the hysteresis gap
        return target

    def step(self):
        with self.lock:
            try:
                t = self.read_temp()
                self.temp = t
            except Exception as e:
                self.temp = None
                self.last_error = "temperature unreadable: %s" % e
                level, reason = "max", "sensor"
            else:
                mode = self.config["mode"]
                if t >= self.config["safety_max"]:
                    level, reason = "max", "safety"
                elif mode == "max":
                    level, reason = "max", "mode"
                elif mode == "bios":
                    level, reason = "bios", "mode"
                else:
                    level, reason = self.curve_level(t), "curve"
            self.apply_fan(level)
            if level != self.level:
                log("level %s -> %s (%s, %s °C)" % (self.level, level, reason, self.temp))
            self.level, self.reason = level, reason
            self.watts = self.read_watts()
            self.load = self.read_load()
            rpm = read_int(os.path.join(self.fan, "fan1_input"))
            self.history.append([int(time.time()), None if self.temp is None else round(self.temp, 1),
                                 rpm, level, self.watts, self.load])

    def status(self, since=0):
        with self.lock:
            last = self.history[-1] if self.history else None
            return {"version": VERSION, "temp": self.temp, "rpm": last[2] if last else None,
                    "watts": self.watts, "load": self.load, "level": self.level, "reason": self.reason,
                    "config": self.config, "power_limits": self.power_limits(),
                    "power_foreign": self.power_foreign,
                    "power_profiles": POWER_PROFILES, "power_range": [POWER_MIN, POWER_MAX],
                    "safety_range": [SAFETY_FLOOR, SAFETY_CEILING], "error": self.last_error,
                    "fan_max_rpm": read_int(os.path.join(self.fan, "fan1_max")),
                    "web": WEBSERVER.state() if WEBSERVER else None,
                    "history": [h for h in self.history if h[0] > since]}

    def run(self):
        sd_notify("READY=1")
        while True:
            try:
                self.step()
                self._steps += 1
                if self._steps % 15 == 0:                 # every 30 s
                    try:
                        self.check_power()
                    except Exception:                     # never let a power check touch the fan
                        log("power check failed: " + traceback.format_exc(limit=2))
                if self.temp is not None and (self.last_error or "").startswith("temperature"):
                    self.last_error = None
            except Exception:
                self.last_error = traceback.format_exc(limit=2)
                log(self.last_error)
                try:
                    write(os.path.join(self.fan, "pwm1_enable"), 2)   # unknown state: BIOS
                except Exception:
                    pass
            sd_notify("WATCHDOG=1")
            time.sleep(INTERVAL)


CTRL = None


class Handler(BaseHTTPRequestHandler):
    server_version = "dellfan/" + VERSION
    from_web = False

    @property
    def readonly(self):
        return False

    def log_message(self, *a):
        pass

    def address_string(self):
        return "local"

    def send_body(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code, obj):
        self.send_body(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if self.path.startswith("/api/status"):
            since = 0
            if "since=" in self.path:
                try:
                    since = int(self.path.split("since=")[1].split("&")[0])
                except ValueError:
                    pass
            return self.send_json(200, CTRL.status(since))
        if self.path in ("/", "/index.html") and os.path.exists(os.path.join(WEB_DIR, "index.html")):
            with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
                return self.send_body(200, f.read(), "text/html; charset=utf-8")
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/config":
            return self.send_json(404, {"error": "not found"})
        if self.readonly:
            return self.send_json(403, {"ok": False, "error": "read-only"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > 10000:
                raise ValueError("request too large")
            patch = json.loads(self.rfile.read(n))
            if not isinstance(patch, dict):
                raise ValueError("config must be a JSON object")
            if self.from_web and "web" in patch:
                if patch["web"] != CTRL.config["web"]:
                    return self.send_json(403, {"ok": False, "error": "web page access can only be changed in the app"})
                del patch["web"]
            cfg = CTRL.update(patch)
            log("config saved:", json.dumps(cfg, ensure_ascii=False))
            return self.send_json(200, {"ok": True, "config": cfg})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            return self.send_json(400, {"ok": False, "error": str(e)})


class WebHandler(Handler):
    from_web = True

    @property
    def readonly(self):
        w = CTRL.config["web"]
        return w["access"] == "network" and w["network_readonly"]


def lan_addresses():
    """IPv4 addresses of this computer on its networks (for the "open from" hint)."""
    # container and VM bridges (docker0, br-…, veth…, virbr…) are skipped — no phone can reach them
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"],
                             capture_output=True, text=True, timeout=2).stdout
        route = subprocess.run(["ip", "-4", "route", "get", "1.1.1.1"],
                               capture_output=True, text=True, timeout=2).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return []
    skip = ("docker", "br-", "veth", "virbr", "lxcbr", "cni")
    ips = [f[3].split("/")[0] for f in (line.split() for line in out.splitlines())
           if len(f) > 3 and not f[1].startswith(skip)]
    main = route[route.index("src") + 1] if "src" in route else None
    return sorted(ips, key=lambda ip: ip != main)        # the address of the default route first


class WebServer:
    """The optional web page. Can be moved to another address/port while running."""

    def __init__(self):
        self.srv, self.web, self.error = None, None, None
        self.lock = threading.Lock()

    def _listen(self, web):
        if web["access"] == "off":
            return None
        srv = ThreadingHTTPServer((WEB_HOSTS[web["access"]], web["port"]), WebHandler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def _stop(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None

    def _describe(self, web):
        if web["access"] == "off":
            return "web page off"
        ro = " (read-only)" if web["access"] == "network" and web["network_readonly"] else ""
        return "web: %s:%d%s" % (WEB_HOSTS[web["access"]], web["port"], ro)

    def start(self, web):
        """At startup: a failure is reported, fan control goes on without the page."""
        with self.lock:
            self.web = web
            try:
                self.srv, self.error = self._listen(web), None
                log(self._describe(web))
            except OSError as e:
                self.error = "port %d: %s" % (web["port"], e.strerror or e)
                log("web page not started — %s (choose another port in the app)" % self.error)

    def apply(self, web):
        """From the app: if the new address cannot be used, the old one stays."""
        with self.lock:
            old = self.web
            self._stop()
            try:
                self.srv = self._listen(web)
            except OSError as e:
                try:
                    self.srv = self._listen(old)
                except OSError:
                    self.srv = None
                raise ValueError("port %d is busy or not allowed (%s) — kept the previous setting"
                                 % (web["port"], e.strerror or e))
            self.web, self.error = web, None
            log(self._describe(web))

    def state(self):
        w = self.web or {}
        running = self.srv is not None
        urls = []
        if running and w.get("access") == "local":
            urls = ["http://127.0.0.1:%d" % w["port"]]
        elif running and w.get("access") == "network":
            urls = ["http://%s:%d" % (ip, w["port"]) for ip in lan_addresses()] or ["http://127.0.0.1:%d" % w["port"]]
        return {"running": running, "error": self.error, "urls": urls}


WEBSERVER = None


class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):
        req, _ = super().get_request()
        return req, ("local", 0)


def start_unix_socket():
    os.makedirs(os.path.dirname(SOCKET_PATH), exist_ok=True)
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass
    srv = UnixHTTPServer(SOCKET_PATH, Handler)
    try:
        gid = grp.getgrnam(SOCKET_GROUP).gr_gid
        os.chown(SOCKET_PATH, 0, gid)
        os.chmod(SOCKET_PATH, 0o660)
        os.chmod(os.path.dirname(SOCKET_PATH), 0o755)
    except KeyError:
        os.chmod(SOCKET_PATH, 0o600)
        log("group %r not found — socket only for root" % SOCKET_GROUP)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("app socket: %s (group %s)" % (SOCKET_PATH, SOCKET_GROUP))


def main():
    global CTRL, WEBSERVER
    CTRL = Controller()
    log("dellfan %s: fan=%s cpu=%s rapl=%s" % (VERSION, CTRL.fan, CTRL.cpu, CTRL.rapl))
    WEBSERVER = WebServer()
    start_unix_socket()
    WEBSERVER.start(CTRL.config["web"])   # optional: if it cannot start, fan control goes on
    CTRL.run()


if __name__ == "__main__":
    main()
