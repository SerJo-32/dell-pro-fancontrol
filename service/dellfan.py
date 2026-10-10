#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""dellfan — fan curve + CPU power limit service for Dell desktops (dell-smm-hwmon).

Needs a dell-smm-hwmon build that exposes pwm1_enable (model in the fan-control
whitelist). Fan levels on Dell Pro Micro Plus QBM1250: pwm 128 ≈ 1000 rpm,
BIOS auto ≈ 1750, pwm 255 ≈ 4000. Python 3 standard library only.

API (HTTP+JSON):
  * unix socket  /run/dellfan/dellfan.sock  — for the desktop app, group "dellfan"
  * TCP (optional, DELLFAN_WEB=1)           — web page, default 127.0.0.1:8095
    GET /api/status, POST /api/config (partial update), GET / (web page)

Safety (not negotiable):
  * CPU at/above safety_max (60..85 °C, never above SAFETY_CEILING) -> fan at max;
  * temperature cannot be read                                    -> fan at max;
  * service stops / crashes / hangs -> systemd ExecStopPost hands the fan to BIOS.
"""
import grp, json, os, socket, socketserver, sys, threading, time, traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "2.0"
CONFIG_PATH = os.environ.get("DELLFAN_CONFIG", "/etc/dellfan/config.json")
SOCKET_PATH = os.environ.get("DELLFAN_SOCKET", "/run/dellfan/dellfan.sock")
SOCKET_GROUP = os.environ.get("DELLFAN_GROUP", "dellfan")
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
EXIT_NO_DRIVER = 3              # systemd: RestartPreventExitStatus=3 — no point restarting

DEFAULT_CONFIG = {
    "mode": "curve",
    "base_level": "quiet",
    "points": [{"temp": 50, "level": "bios"}, {"temp": 62, "level": "max"}],
    "hysteresis": 4,
    "safety_max": 75,
    "power": {"profile": "off"},          # off = leave CPU power limits alone
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
    return {"mode": mode, "base_level": base, "points": clean, "hysteresis": hyst,
            "safety_max": safety, "power": power}


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

    def apply_power(self):
        pw = self.config["power"]
        if pw["profile"] == "off" or not self.rapl:
            return
        pl1, pl2 = POWER_PROFILES.get(pw["profile"], (pw.get("pl1"), pw.get("pl2")))
        for z in self.rapl:
            try:
                write(z + "/constraint_0_power_limit_uw", pl1 * 1000000)
                write(z + "/constraint_1_power_limit_uw", pl2 * 1000000)
                write(z + "/enabled", 1)
            except OSError as e:
                self.last_error = "power limit: %s" % e
        log("power limit %s: %d/%d W" % (pw["profile"], pl1, pl2))

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
                    "power_profiles": POWER_PROFILES, "power_range": [POWER_MIN, POWER_MAX],
                    "safety_range": [SAFETY_FLOOR, SAFETY_CEILING], "error": self.last_error,
                    "fan_max_rpm": read_int(os.path.join(self.fan, "fan1_max")),
                    "history": [h for h in self.history if h[0] > since]}

    def run(self):
        sd_notify("READY=1")
        while True:
            try:
                self.step()
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
    readonly = False

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
            cfg = CTRL.update(json.loads(self.rfile.read(n)))
            log("config saved:", json.dumps(cfg, ensure_ascii=False))
            return self.send_json(200, {"ok": True, "config": cfg})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            return self.send_json(400, {"ok": False, "error": str(e)})


class WebHandler(Handler):
    readonly = WEB_READONLY


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
    global CTRL
    CTRL = Controller()
    log("dellfan %s: fan=%s cpu=%s rapl=%s" % (VERSION, CTRL.fan, CTRL.cpu, CTRL.rapl))
    start_unix_socket()
    if WEB:
        srv = ThreadingHTTPServer(WEB_LISTEN, WebHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        log("web: http://%s:%d%s" % (WEB_LISTEN + (" (read-only)" if WEB_READONLY else "",)))
    CTRL.run()


if __name__ == "__main__":
    main()
