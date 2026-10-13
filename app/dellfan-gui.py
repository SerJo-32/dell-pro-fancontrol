#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dell Pro Fan Control — desktop app (GTK 4 + libadwaita) for the dellfan service.

Talks to the service over /run/dellfan/dellfan.sock (needs membership in the
"dellfan" group) or, as a fallback, over the web API on 127.0.0.1:8095.
The app never touches the fan itself: all safety logic lives in the service.
"""
import http.client, json, locale, math, os, socket, sys, threading, time
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Gdk   # noqa: E402

APP_ID = "io.github.dellfan.DellFan"
SOCKET = os.environ.get("DELLFAN_SOCKET", "/run/dellfan/dellfan.sock")
WEB = ("127.0.0.1", int(os.environ.get("DELLFAN_PORT", "8095")))
T0, T1 = 30, 90

# ---------------------------------------------------------------- texts
RU = (locale.getlocale()[0] or os.environ.get("LANG", "")).lower().startswith("ru")
TXT = {
    "title": ("Вентилятор Dell", "Dell Pro Fan Control"),
    "conn_ok": ("служба dellfan %s", "dellfan service %s"),
    "conn_bad": ("нет связи со службой", "service not reachable"),
    "no_access": ("Нет доступа к службе", "No access to the service"),
    "no_access_d": ("Добавьте себя в группу «dellfan» и войдите в систему заново:\nsudo usermod -aG dellfan $USER",
                    "Add yourself to the “dellfan” group and log in again:\nsudo usermod -aG dellfan $USER"),
    "no_service": ("Служба dellfan не запущена", "dellfan service is not running"),
    "no_service_d": ("sudo systemctl start dellfan", "sudo systemctl start dellfan"),
    "quiet": ("тихо", "quiet"), "bios": ("BIOS", "BIOS"), "max": ("максимум", "max"), "safety": ("защита", "safety"),
    "ok": ("Всё в норме", "All good"), "warm": ("Тепло — под контролем", "Warm — under control"),
    "hot": ("Горячо — защита держит максимум", "Hot — safety keeps the fan at max"),
    "nosensor": ("Датчик не отвечает — вентилятор на максимуме", "Sensor not responding — fan at max"),
    "fan": ("вентилятор", "fan"), "rpm": ("об/мин", "rpm"), "speed": ("обороты", "fan speed"), "cpu": ("процессор", "CPU"),
    "limit": ("лимит мощности", "power limit"), "w": ("Вт", "W"),
    "load": ("загрузка процессора", "CPU load"), "draw": ("потребляет сейчас", "drawing now"),
    "of": ("из", "of"), "boost": ("разгон, лимит", "boost, limit"), "capped": ("упёрся в лимит", "at the limit"),
    "react": ("Как реагирует вентилятор", "How the fan responds"),
    "react_d": ("Перетащите границу между зонами. Красная зона — защита: там вентилятор всегда на максимуме.",
                "Drag a boundary between zones. The red zone is safety: the fan is always at max there."),
    "now": ("сейчас", "now"),
    "history": ("История", "History"), "m5": ("5 мин", "5 min"), "m30": ("30 мин", "30 min"), "h1": ("1 час", "1 hour"),
    "settings": ("Настройки", "Settings"),
    "mode": ("Режим", "Mode"), "mode_d": ("Кривая — по зонам выше. BIOS — как задумал Dell.", "Curve follows the zones above. BIOS is Dell's default."),
    "curve": ("Кривая", "Curve"),
    "zones": ("Зоны", "Zones"), "zones_d": ("Точные значения границ", "Exact boundary values"),
    "below": ("До первой границы", "Below the first boundary"), "from": ("От %d °C", "From %d °C"),
    "add": ("Добавить границу", "Add boundary"), "remove": ("Убрать", "Remove"),
    "hyst": ("Обратный ход, °C", "Hysteresis, °C"),
    "hyst_d": ("Ступень снижается, когда стало на столько градусов холоднее границы", "A step goes down once it is this much cooler than the boundary"),
    "safety_t": ("Защита", "Safety"), "safety_d": ("С этой температуры — всегда максимум (60–85 °C)", "Always max from this temperature (60–85 °C)"),
    "power": ("Мощность процессора", "CPU power"), "power_d": ("Меньше ватт — холоднее и тише, больше — быстрее", "Fewer watts is cooler and quieter, more is faster"),
    "eco": ("Бережно", "Eco"), "stock": ("Быстрее", "Faster"), "custom": ("Свой", "Custom"), "off": ("Не управлять", "Don't manage"),
    "pl1": ("Длительно, Вт", "Long term, W"), "pl2": ("Кратко, Вт", "Short term, W"),
    "cur": ("сейчас в процессоре: %s Вт", "now set in CPU: %s W"),
    "eco_s": ("25 / 35 Вт", "25 / 35 W"), "stock_s": ("35 / 45 Вт", "35 / 45 W"), "custom_s": ("вписать", "enter"), "off_s": ("заводские Dell", "Dell defaults"),
    "off_note": ("Мощностью управляет не эта программа: сразу возвращены значения, с которыми включился компьютер, — заводские Dell (у Dell Pro Micro Plus — 65/121 Вт).",
                 "Power is not managed by this app: the values the computer booted with are back right away — Dell defaults (65/121 W on the Dell Pro Micro Plus)."),
    "foreign": ("Сейчас лимит поменяла другая программа (обычно это защита Intel thermald при сильном нагреве): %s. Когда процессор остынет, она обычно возвращает лимит сама; если нет — эта программа вернёт свой через 5 минут.",
                "Another program changed the limit (usually Intel thermald protecting a hot CPU): %s. It normally puts it back once the CPU cools down; if not, this app sets its own again after 5 minutes."),
    "w_unit": ("Вт", "W"),
    "off_same": (" Сейчас стоят значения как у «%s».", " Current values match “%s”."),
    "pw_help": ("«Длительно» — сколько процессор может брать постоянно, «кратко» — рывок на несколько секунд. От 15 до %d Вт длительно и до %d Вт кратко (заводские значения этого компьютера).",
                "Long term is what the CPU may draw continuously, short term is a burst of a few seconds. 15 to %d W long term and up to %d W short term (this computer's factory values)."),
    "changes": ("Есть изменения", "Unsaved changes"), "cancel": ("Отменить", "Cancel"), "apply": ("Применить", "Apply"),
    "applied": ("Применено — служба уже работает по новым настройкам", "Applied — the service already uses the new settings"),
    "rejected": ("Не применено: %s", "Not applied: %s"),
    "web": ("Открыть веб-страницу", "Open web page"),
    "web_off_tip": ("Веб-страница выключена (Настройки → Веб-страница)", "Web page is off (Settings → Web page)"),
    "webpage": ("Веб-страница", "Web page"),
    "webpage_d": ("Смотреть и менять настройки из браузера. Доступ меняется только здесь, в приложении.",
                  "View and change settings from a browser. Access can only be changed here, in the app."),
    "w_access": ("Доступ", "Access"),
    "w_local": ("Этот компьютер", "This computer"), "w_local_s": ("только отсюда", "only from here"),
    "w_network": ("Вся сеть", "Whole network"), "w_network_s": ("телефон, ноутбук", "phone, laptop"),
    "w_off": ("Выключена", "Off"), "w_off_s": ("нет страницы", "no page"),
    "w_port": ("Порт", "Port"), "w_port_d": ("Номер «двери» страницы. Меняйте, только если 8095 занят другой программой.",
                                             "The page's \"door\" number. Change it only if 8095 is taken by another program."),
    "w_ro": ("Из сети — только просмотр", "From the network — view only"),
    "w_ro_d": ("Смотреть можно с любого устройства, менять — только в этом приложении",
               "Anyone on the network can look; changes only in this app"),
    "w_open": ("Адрес страницы", "Page address"),
    "w_open_btn": ("Открыть", "Open"),
    "w_none": ("Страница выключена", "The page is off"),
    "w_warn": ("⚠ У страницы нет пароля: любой в вашей сети сможет менять настройки вентилятора и мощности.",
               "⚠ The page has no password: anyone on your network will be able to change fan and power settings."),
    "w_pending": ("Применится после «Применить»", "Takes effect after Apply"),
    "w_error": ("Не запустилась: %s", "Not running: %s"),
    "nospace": ("Места для новой границы нет", "No room for another boundary"),
}
def T(k):
    return TXT[k][0 if RU else 1]

PALETTE = {  # light, dark
    "quiet": ("#2f9e6e", "#3fbf86"), "bios": ("#7b8494", "#8d96a6"), "max": ("#e8890c", "#f2a033"), "warn": ("#c99a06", "#e3bb3a"),
    "safety": ("#d64040", "#ef5b5b"), "accent": ("#3b6fd8", "#6b95ef"),
    "quiet_bg": ("#e3f4ec", "#173327"), "bios_bg": ("#eceef2", "#252a32"), "max_bg": ("#fdf0dc", "#3a2a12"),
    "safety_bg": ("#fbe4e4", "#3d1e1e"), "text": ("#16191f", "#eceef2"), "muted": ("#68707f", "#9aa1ad"),
    "line": ("#e4e7ec", "#2a2f37"),
}

# ---------------------------------------------------------------- API client
class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=4)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(4)
        self.sock.connect(self.path)


class Api:
    def __init__(self):
        self.problem = None

    def _conn(self, web=False):
        if os.path.exists(SOCKET) and not web:
            return UnixHTTPConnection(SOCKET)
        return http.client.HTTPConnection(*WEB, timeout=4)

    def call(self, method, path, body=None):
        try:
            return self._call(method, path, body, web=False)
        except PermissionError:
            # not in the "dellfan" group yet (or not re-logged in): try the web API if enabled
            try:
                return self._call(method, path, body, web=True)
            except OSError:
                self.problem = "access"
                raise PermissionError("no access")

    def _call(self, method, path, body, web):
        c = self._conn(web)
        try:
            data = json.dumps(body).encode() if body is not None else None
            c.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
            r = c.getresponse()
            self.problem = None
            return r.status, json.loads(r.read() or b"{}")
        except PermissionError:
            raise
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError):
            self.problem = "service"
            raise
        finally:
            c.close()


# ---------------------------------------------------------------- helpers
def zones(c):
    z, frm, lv = [], T0, c["base_level"]
    for p in c["points"]:
        z.append((frm, p["temp"], lv)); frm, lv = p["temp"], p["level"]
    z.append((frm, c["safety_max"], lv)); z.append((c["safety_max"], T1, "safety"))
    return z


def zone_of(c, t):
    if t >= c["safety_max"]:
        return "safety"
    lv = c["base_level"]
    for p in c["points"]:
        if t >= p["temp"]:
            lv = p["level"]
    return lv


def rgb(hexs, a=1.0):
    h = hexs.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


CSS = b"""
.hero-temp { font-size: 56px; font-weight: 800; }
.hero-sum { font-size: 18px; font-weight: 700; }
.fact-val { font-size: 16px; font-weight: 700; font-feature-settings: "tnum"; }
.card { padding: 18px; }
.section-title { font-weight: 700; opacity: .65; font-size: 12px; letter-spacing: .04em; }
.safety-scale highlight { background-color: #d64040; }
"""


# ---------------------------------------------------------------- window
class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        w, h = (int(v) for v in os.environ.get("DELLFAN_GUI_SIZE", "920x980").split("x"))
        super().__init__(application=app, title=T("title"), default_width=w, default_height=h)
        self.api = Api()
        self.st, self.draft, self.dirty, self.hist, self.last_ts, self.range = None, None, False, [], 0, 1800
        self.drag = None
        self.dark = Adw.StyleManager.get_default().get_dark()
        Adw.StyleManager.get_default().connect("notify::dark", self._on_dark)

        prov = Gtk.CssProvider(); prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.toasts = Adw.ToastOverlay()
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.subtitle = Adw.WindowTitle(title=T("title"), subtitle="…")
        hb.set_title_widget(self.subtitle)
        self.web_btn = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text=T("web"))
        self.web_btn.connect("clicked", lambda *_: self.open_web())
        hb.pack_end(self.web_btn)
        tv.add_top_bar(hb)

        self.stack = Gtk.Stack()
        self.status_page = Adw.StatusPage(icon_name="dialog-warning-symbolic")
        self.stack.add_named(self.status_page, "problem")
        self.stack.add_named(self._build_main(), "main")
        tv.set_content(self.stack)

        # apply bar
        self.bar = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        bb = Gtk.Box(spacing=10, margin_top=8, margin_bottom=8, margin_start=16, margin_end=16, halign=Gtk.Align.CENTER)
        bb.append(Gtk.Label(label=T("changes")))
        cancel = Gtk.Button(label=T("cancel")); cancel.connect("clicked", self.on_cancel)
        apply = Gtk.Button(label=T("apply")); apply.add_css_class("suggested-action"); apply.connect("clicked", self.on_apply)
        bb.append(cancel); bb.append(apply)
        self.bar.set_child(bb)
        tv.add_bottom_bar(self.bar)

        self.toasts.set_child(tv)
        self.set_content(self.toasts)
        self.poll()
        GLib.timeout_add_seconds(2, self.poll)

    # ----- layout
    def _card(self, title=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.add_css_class("card")
        if title:
            l = Gtk.Label(label=title.upper(), xalign=0); l.add_css_class("section-title"); box.append(l)
        return box

    def _build_main(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, hexpand=True,
                      margin_top=16, margin_bottom=24, margin_start=20, margin_end=20)

        # hero
        hero = self._card()
        row = Gtk.Box(spacing=24)
        self.l_temp = Gtk.Label(label="—", xalign=0); self.l_temp.add_css_class("hero-temp")
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        self.l_sum = Gtk.Label(label="—", xalign=0, wrap=True); self.l_sum.add_css_class("hero-sum")
        facts = Gtk.Box(spacing=26)
        self.m_load = self._meter(facts, T("load"))
        self.m_w = self._meter(facts, T("draw"))
        self.f_rpm = self._fact(facts, T("speed"))
        right.append(self.l_sum); right.append(facts)
        row.append(self.l_temp); row.append(right); hero.append(row)
        col.append(hero)

        # ruler
        rc = self._card(T("react"))
        self.ruler = Gtk.DrawingArea(content_height=96, hexpand=True)
        self.ruler.set_draw_func(self.draw_ruler)
        drag = Gtk.GestureDrag(); drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update); drag.connect("drag-end", lambda *a: setattr(self, "drag", None))
        self.ruler.add_controller(drag)
        rc.append(self.ruler)
        h = Gtk.Label(label=T("react_d"), xalign=0, wrap=True); h.add_css_class("dim-label"); rc.append(h)
        col.append(rc)

        # history
        hc = self._card()
        hh = Gtk.Box()
        t = Gtk.Label(label=T("history").upper(), xalign=0, hexpand=True); t.add_css_class("section-title"); hh.append(t)
        seg = Gtk.Box(); seg.add_css_class("linked")
        first = None
        for key, secs in (("m5", 300), ("m30", 1800), ("h1", 3600)):
            b = Gtk.ToggleButton(label=T(key), group=first); first = first or b
            b.set_active(secs == 1800)
            b.connect("toggled", self.on_range, secs); seg.append(b)
        hh.append(seg); hc.append(hh)
        self.tchart = Gtk.DrawingArea(content_height=220, hexpand=True); self.tchart.set_draw_func(self.draw_temp)
        self.rchart = Gtk.DrawingArea(content_height=64, hexpand=True); self.rchart.set_draw_func(self.draw_rpm)
        hc.append(self.tchart); hc.append(self.rchart)
        col.append(hc)

        # settings
        self.prefs = Adw.PreferencesGroup(title=T("settings"))
        col.append(self.prefs)
        self.mode_row = Adw.ActionRow(title=T("mode"), subtitle=T("mode_d"))
        self.mode_btns = self._segmented(self.mode_row, [("curve", T("curve")), ("bios", T("bios")), ("max", T("max").capitalize())], self.on_mode)
        self.prefs.add(self.mode_row)
        self.zones_row = Adw.ExpanderRow(title=T("zones"), subtitle=T("zones_d"))
        self.prefs.add(self.zones_row); self.zone_children = []
        self.hyst_row = Adw.SpinRow.new_with_range(1, 10, 1); self.hyst_row.set_title(T("hyst")); self.hyst_row.set_subtitle(T("hyst_d"))
        self.hyst_row.connect("notify::value", lambda r, *_: self._set("hysteresis", int(r.get_value())))
        self.prefs.add(self.hyst_row)
        self.safe_row = Adw.ActionRow(title=T("safety_t"), subtitle=T("safety_d"))
        self.safe_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 60, 85, 1)
        self.safe_scale.add_css_class("safety-scale"); self.safe_scale.set_size_request(220, -1); self.safe_scale.set_draw_value(True); self.safe_scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.safe_scale.set_format_value_func(lambda sc, v: "%d °C" % v)
        self.safe_scale.connect("value-changed", lambda s: self._set("safety_max", int(s.get_value())))
        self.safe_row.add_suffix(self.safe_scale); self.prefs.add(self.safe_row)
        self.power_row = Adw.ActionRow(title=T("power"), subtitle=T("power_d"))
        self.power_btns = self._segmented(self.power_row, [(k, (T(k), T(k + "_s"))) for k in ("eco", "stock", "custom", "off")], self.on_power)
        self.prefs.add(self.power_row)
        self.pl1_row = Adw.SpinRow.new_with_range(15, 45, 1); self.pl1_row.set_title(T("pl1"))
        self.pl2_row = Adw.SpinRow.new_with_range(15, 45, 1); self.pl2_row.set_title(T("pl2"))
        for r in (self.pl1_row, self.pl2_row):
            r.connect("notify::value", self.on_custom_power); self.prefs.add(r)
        help_row = Adw.ActionRow(subtitle=T("pw_help") % (45, 45)); help_row.set_activatable(False)
        self.pw_help_row = help_row
        self.prefs.add(help_row)

        # web page access — only the app can change it (the service refuses it from the page)
        self.webg = Adw.PreferencesGroup(title=T("webpage"), description=T("webpage_d"))
        col.append(self.webg)
        self.w_access_row = Adw.ActionRow(title=T("w_access"))
        self.w_btns = self._segmented(self.w_access_row, [(k, (T("w_" + k), T("w_" + k + "_s"))) for k in ("local", "network", "off")],
                                      lambda k: self._set_web("access", k))
        self.webg.add(self.w_access_row)
        self.w_port_row = Adw.SpinRow.new_with_range(1024, 65535, 1); self.w_port_row.set_title(T("w_port")); self.w_port_row.set_subtitle(T("w_port_d"))
        self.w_port_row.connect("notify::value", lambda r, *_: self._set_web("port", int(r.get_value())))
        self.webg.add(self.w_port_row)
        self.w_ro_row = Adw.SwitchRow(title=T("w_ro"), subtitle=T("w_ro_d"))
        self.w_ro_row.connect("notify::active", lambda r, *_: self._set_web("network_readonly", r.get_active()))
        self.webg.add(self.w_ro_row)
        self.w_warn_row = Adw.ActionRow(subtitle=T("w_warn")); self.w_warn_row.set_activatable(False)
        self.w_warn_row.add_css_class("warning")
        self.webg.add(self.w_warn_row)
        self.w_url_row = Adw.ActionRow(title=T("w_open"), subtitle_selectable=True)
        self.w_url_btn = Gtk.Button(label=T("w_open_btn"), valign=Gtk.Align.CENTER)
        self.w_url_btn.connect("clicked", lambda *_: self.open_web())
        self.w_url_row.add_suffix(self.w_url_btn)
        self.webg.add(self.w_url_row)

        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        sw.set_child(col)
        return sw

    def _meter(self, parent, name):
        """Number + thin coloured bar + caption. The bar is drawn from m["frac"], m["zone"], m["tick"]."""
        m = {"frac": 0.0, "zone": "quiet", "tick": None}
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        m["val"] = Gtk.Label(label="—", xalign=0); m["val"].add_css_class("fact-val")
        m["bar"] = Gtk.DrawingArea(); m["bar"].set_size_request(128, 5)
        m["bar"].set_draw_func(lambda a, cr, w, h, m=m: self._draw_meter(cr, w, h, m))
        m["sub"] = Gtk.Label(label=name, xalign=0); m["sub"].add_css_class("dim-label"); m["sub"].add_css_class("caption")
        for x in (m["val"], m["bar"], m["sub"]):
            b.append(x)
        parent.append(b)
        return m

    def _draw_meter(self, cr, w, h, m):
        r = h / 2
        def pill(x0, x1):
            if x1 - x0 < 1:
                return
            cr.new_sub_path()
            cr.arc(x0 + r, r, r, math.pi / 2, 3 * math.pi / 2)
            cr.arc(max(x0 + r, x1 - r), r, r, -math.pi / 2, math.pi / 2)
            cr.close_path(); cr.fill()
        cr.set_source_rgba(*self.P("line")); pill(0, w)
        cr.set_source_rgba(*self.P(m["zone"])); pill(0, w * max(0.0, min(1.0, m["frac"])))
        if m["tick"] is not None:
            cr.set_source_rgba(*rgb("#1b1e24" if self.dark else "#ffffff"))
            cr.rectangle(w * m["tick"] - 1, 0, 2, h); cr.fill()

    def _set_meter(self, m, text, frac, zone, tick=None, sub=None, show_bar=True):
        m["frac"], m["zone"], m["tick"] = frac, zone, tick
        color = None if zone == "quiet" else PALETTE[zone][self.dark]   # colour the number only when it matters
        m["val"].set_markup(text if color is None else '<span foreground="%s">%s</span>' % (color, text))
        m["bar"].set_visible(show_bar); m["bar"].queue_draw()
        if sub is not None:
            m["sub"].set_label(sub)

    def _fact(self, parent, name):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        v = Gtk.Label(label="—", xalign=0); v.add_css_class("fact-val")
        n = Gtk.Label(label=name, xalign=0); n.add_css_class("dim-label"); n.add_css_class("caption")
        b.append(v); b.append(n); parent.append(b)
        return v

    def _segmented(self, row, items, cb):
        box = Gtk.Box(valign=Gtk.Align.CENTER); box.add_css_class("linked")
        btns, first = {}, None
        for key, label in items:
            if isinstance(label, tuple):      # two lines: name + small hint
                b = Gtk.ToggleButton(group=first)
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                l1 = Gtk.Label(label=label[0]); l1.add_css_class("heading")
                l2 = Gtk.Label(label=label[1]); l2.add_css_class("caption"); l2.add_css_class("dim-label")
                inner.append(l1); inner.append(l2); b.set_child(inner)
            else:
                b = Gtk.ToggleButton(label=label, group=first)
            first = first or b
            b.connect("toggled", lambda btn, k=key: btn.get_active() and not self._loading and cb(k))
            box.append(b); btns[key] = b
        row.add_suffix(box)
        return btns

    # ----- data
    _loading = False

    def poll(self):
        def work():
            try:
                code, s = self.api.call("GET", "/api/status?since=%d" % self.last_ts)
                GLib.idle_add(self.on_status, s)
            except Exception:
                GLib.idle_add(self.on_problem)
        threading.Thread(target=work, daemon=True).start()
        return True

    def on_problem(self):
        if self.api.problem == "access":
            self.status_page.set_title(T("no_access")); self.status_page.set_description(T("no_access_d"))
        else:
            self.status_page.set_title(T("no_service")); self.status_page.set_description(T("no_service_d"))
        self.subtitle.set_subtitle(T("conn_bad"))
        self.stack.set_visible_child_name("problem")

    def on_status(self, s):
        self.st = s
        if s["history"]:
            cut = time.time() - 3700
            self.hist = [h for h in self.hist + s["history"] if h[0] > cut]
            self.last_ts = self.hist[-1][0]
        if not self.dirty:
            self.draft = json.loads(json.dumps(s["config"]))
            self.load_settings()
        self.subtitle.set_subtitle(T("conn_ok") % s.get("version", "?"))
        if self.dirty and self.draft:
            self.update_web_rows()
        self.stack.set_visible_child_name("main")
        self.update_hero()
        for w in (self.ruler, self.tchart, self.rchart):
            w.queue_draw()

    def update_hero(self):
        s, c = self.st, self.st["config"]
        t = s["temp"]
        self.l_temp.set_label("—" if t is None else "%d°" % round(t))
        z = "safety" if t is None else zone_of(c, t)
        color = PALETTE[z][self.dark] if z != "bios" else PALETTE["text"][self.dark]
        self.l_temp.set_markup('<span foreground="%s">%s</span>' % (color, self.l_temp.get_label()))
        if t is None:
            msg = T("nosensor")
        elif s["reason"] == "safety":
            msg = T("hot")
        elif t >= c["safety_max"] - 8:
            msg = T("warm")
        else:
            msg = T("ok")
        self.l_sum.set_label("%s · %s %s" % (msg, T("fan"), T(s["level"]) if s["level"] else "—"))
        self.f_rpm.set_label("—" if s["rpm"] is None else "{:,}".format(s["rpm"]).replace(",", " ") + " " + T("rpm"))
        # CPU load: under 70 % fine, 70–90 watch, 90+ flat out
        ld = s.get("load")
        lz = "quiet" if ld is None or ld < 70 else "warn" if ld < 90 else "safety"
        self._set_meter(self.m_load, "—" if ld is None else "%d %%" % round(ld), (ld or 0) / 100, lz)
        # power vs limits: under PL1 fine, above it short boost, near PL2 capped
        w, pl = s.get("watts"), s.get("power_limits")
        wt = "—" if w is None else ("%s %s" % (w, T("w"))).replace(".", "," if RU else ".")
        if pl and c.get("power", {}).get("profile") != "off":
            wz = "quiet" if w is None or w <= pl["pl1"] else "max" if w >= pl["pl2"] * 0.95 else "warn"
            lim = "%d / %d %s" % (pl["pl1"], pl["pl2"], T("w"))
            sub = {"quiet": "%s %s" % (T("of"), lim), "warn": "%s %s" % (T("boost"), lim),
                   "max": "%s %d %s" % (T("capped"), pl["pl2"], T("w"))}[wz]
            self._set_meter(self.m_w, wt, (w or 0) / pl["pl2"], wz, pl["pl1"] / pl["pl2"], sub)
        else:
            self._set_meter(self.m_w, wt, 0, "quiet", sub=T("draw"), show_bar=False)

    def load_settings(self):
        self._loading = True
        c = self.draft
        self.mode_btns[c["mode"]].set_active(True)
        self.hyst_row.set_value(c["hysteresis"])
        self.safe_scale.set_value(c["safety_max"])
        pw = c["power"]
        self.power_btns[pw["profile"]].set_active(True)
        cur = (self.st or {}).get("power_limits") or {}
        prof = (self.st or {}).get("power_profiles") or {"eco": [25, 35], "stock": [35, 45]}
        will = [pw["pl1"], pw["pl2"]] if pw["profile"] == "custom" else prof.get(pw["profile"]) or [cur.get("pl1", 25), cur.get("pl2", 35)]
        caps = (self.st or {}).get("power_caps") or [45, 45]
        self.pl1_row.set_range(15, caps[0]); self.pl2_row.set_range(15, caps[1])
        self.pw_help_row.set_subtitle(T("pw_help") % tuple(caps))
        self.pl1_row.set_value(will[0]); self.pl2_row.set_value(will[1])
        self.pl1_row.set_subtitle(T("cur") % cur.get("pl1", "—")); self.pl2_row.set_subtitle(T("cur") % cur.get("pl2", "—"))
        if pw["profile"] == "off":
            same = next((k for k, v in prof.items() if list(v) == [cur.get("pl1"), cur.get("pl2")]), None)
            self.power_row.set_subtitle(T("off_note") + (T("off_same") % T(same) if same else ""))
        elif (self.st or {}).get("power_foreign"):
            self.power_row.set_subtitle(T("foreign") % ", ".join("%d/%d %s" % (v[0], v[1], T("w_unit")) for v in self.st["power_foreign"]))
        else:
            self.power_row.set_subtitle(T("power_d"))
        w = c.setdefault("web", {"access": "local", "port": 8095, "network_readonly": True})   # older service
        self.w_btns[w["access"]].set_active(True)
        self.w_port_row.set_value(w["port"])
        self.w_ro_row.set_active(w["network_readonly"])
        self.update_web_rows()
        self.build_zone_rows()
        self._loading = False

    def update_web_rows(self):
        """Port/read-only only make sense when the page is on; the warning only for an open network page."""
        w = self.draft["web"]
        self.w_port_row.set_sensitive(w["access"] != "off")
        self.w_ro_row.set_visible(w["access"] == "network")
        self.w_warn_row.set_visible(w["access"] == "network" and not w["network_readonly"])
        ws = (self.st or {}).get("web") or {}
        saved = (self.st or {}).get("config", {}).get("web")
        if saved is not None and w != saved:
            self.w_url_row.set_subtitle(T("w_pending")); self.w_url_btn.set_sensitive(False)
        elif ws.get("error"):
            self.w_url_row.set_subtitle(T("w_error") % ws["error"]); self.w_url_btn.set_sensitive(False)
        elif ws.get("urls"):
            self.w_url_row.set_subtitle("  ·  ".join(ws["urls"])); self.w_url_btn.set_sensitive(True)
        else:
            self.w_url_row.set_subtitle(T("w_none")); self.w_url_btn.set_sensitive(False)
        running = bool(ws.get("running") and ws.get("urls"))
        self.web_btn.set_sensitive(running)
        self.web_btn.set_tooltip_text(T("web") if running else T("web_off_tip"))

    def open_web(self):
        urls = ((self.st or {}).get("web") or {}).get("urls") or []
        if urls:
            Gtk.UriLauncher(uri=urls[0]).launch(self, None, None, None)

    def build_zone_rows(self):
        for r in self.zone_children:
            self.zones_row.remove(r)
        self.zone_children = []
        names = [T("quiet"), T("bios"), T("max")]
        keys = ["quiet", "bios", "max"]

        def dropdown(level, cb):
            dd = Gtk.DropDown.new_from_strings(names); dd.set_selected(keys.index(level)); dd.set_valign(Gtk.Align.CENTER)
            dd.connect("notify::selected", lambda d, *_: not self._loading and cb(keys[d.get_selected()]))
            return dd
        base = Adw.ActionRow(title=T("below"))
        base.add_suffix(dropdown(self.draft["base_level"], lambda v: self._set("base_level", v)))
        self.zones_row.add_row(base); self.zone_children.append(base)
        for i, p in enumerate(self.draft["points"]):
            r = Adw.ActionRow(title=T("from") % p["temp"])
            sp = Gtk.SpinButton.new_with_range(31, 84, 1); sp.set_value(p["temp"]); sp.set_valign(Gtk.Align.CENTER)
            sp.connect("value-changed", lambda s, i=i: not self._loading and self._set_point(i, "temp", int(s.get_value())))
            r.add_suffix(sp)
            r.add_suffix(dropdown(p["level"], lambda v, i=i: self._set_point(i, "level", v)))
            rm = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=T("remove"), valign=Gtk.Align.CENTER)
            rm.add_css_class("flat"); rm.connect("clicked", lambda *_, i=i: self._del_point(i))
            r.add_suffix(rm)
            self.zones_row.add_row(r); self.zone_children.append(r)
        add = Adw.ButtonRow(title=T("add"), start_icon_name="list-add-symbolic") if hasattr(Adw, "ButtonRow") else None
        if add is None:
            add = Adw.ActionRow(title=T("add"), activatable=True)
            add.add_prefix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
            add.connect("activated", self._add_point)
        else:
            add.connect("activated", self._add_point)
        self.zones_row.add_row(add); self.zone_children.append(add)

    # ----- editing
    def _changed(self):
        self.dirty = True
        self.bar.set_reveal_child(True)
        self.ruler.queue_draw(); self.tchart.queue_draw()

    def _set_web(self, key, val):
        if self._loading or not self.draft:
            return
        self.draft["web"] = {**self.draft["web"], key: val}
        self.update_web_rows()
        self._changed()

    def _set(self, key, val):
        if self._loading or not self.draft:
            return
        self.draft[key] = val
        self._changed()

    def _set_point(self, i, key, val):
        self.draft["points"][i][key] = val
        self._changed()

    def _del_point(self, i):
        del self.draft["points"][i]
        self._changed(); self._loading = True; self.build_zone_rows(); self._loading = False

    def _add_point(self, *_):
        pts = self.draft["points"]
        t = min((pts[-1]["temp"] if pts else 45) + 5, self.draft["safety_max"] - 1)
        if len(pts) >= 6 or any(p["temp"] == t for p in pts):
            return self.toasts.add_toast(Adw.Toast(title=T("nospace")))
        pts.append({"temp": t, "level": "max"})
        self._changed(); self._loading = True; self.build_zone_rows(); self._loading = False

    def on_mode(self, m):
        self._set("mode", m)

    def on_power(self, p):
        if p == "custom":
            self.draft["power"] = {"profile": "custom", "pl1": int(self.pl1_row.get_value()), "pl2": int(self.pl2_row.get_value())}
        else:
            self.draft["power"] = {"profile": p}
        if p in ("eco", "stock") and self.st:
            a, b = self.st["power_profiles"][p]
            self._loading = True; self.pl1_row.set_value(a); self.pl2_row.set_value(b); self._loading = False
        self._changed()

    def on_custom_power(self, *_):
        if self._loading or not self.draft:
            return
        # typing a value means "custom"
        self.draft["power"] = {"profile": "custom", "pl1": int(self.pl1_row.get_value()), "pl2": int(self.pl2_row.get_value())}
        self._loading = True; self.power_btns["custom"].set_active(True); self._loading = False
        self._changed()

    def on_cancel(self, *_):
        self.dirty = False; self.bar.set_reveal_child(False)
        self.draft = json.loads(json.dumps(self.st["config"])); self.load_settings()
        self.ruler.queue_draw(); self.tchart.queue_draw()

    def on_apply(self, *_):
        self.draft["points"].sort(key=lambda p: p["temp"])
        body = self.draft

        def work():
            try:
                code, r = self.api.call("POST", "/api/config", body)
            except Exception as e:
                code, r = 0, {"error": str(e)}
            GLib.idle_add(self.after_apply, code, r)
        threading.Thread(target=work, daemon=True).start()

    def after_apply(self, code, r):
        if code == 200 and r.get("ok"):
            self.dirty = False; self.bar.set_reveal_child(False)
            self.toasts.add_toast(Adw.Toast(title=T("applied")))
            self.poll()
        else:
            self.toasts.add_toast(Adw.Toast(title=T("rejected") % r.get("error", code), timeout=6))

    def on_range(self, btn, secs):
        if btn.get_active():
            self.range = secs; self.tchart.queue_draw(); self.rchart.queue_draw()

    def _on_dark(self, sm, *_):
        self.dark = sm.get_dark()
        if self.st:
            self.update_hero()
        for w in (self.ruler, self.tchart, self.rchart):
            w.queue_draw()

    # ----- drawing
    def P(self, k, a=1.0):
        return rgb(PALETTE[k][self.dark], a)

    def draw_ruler(self, area, cr, w, h):
        if not self.st:
            return
        c = self.draft or self.st["config"]
        L, R, y0, bh = 8, w - 8, 30, 26
        x = lambda t: L + (t - T0) / (T1 - T0) * (R - L)
        cr.select_font_face("Sans"); cr.set_font_size(12)
        for a, b, lv in zones(c):
            if b <= a:
                continue
            cr.set_source_rgba(*self.P(lv + "_bg")); cr.rectangle(x(a), y0, x(b) - x(a), bh); cr.fill()
            cr.set_source_rgba(*self.P(lv)); cr.rectangle(x(a), y0 + bh - 4, x(b) - x(a), 4); cr.fill()
            if x(b) - x(a) > 60:
                label = T(lv); ext = cr.text_extents(label)
                cr.move_to((x(a) + x(b)) / 2 - ext.width / 2, y0 + 17); cr.show_text(label)
        for p in c["points"]:      # hysteresis ghosts
            cr.set_source_rgba(*self.P("text", .07)); cr.rectangle(x(p["temp"] - c["hysteresis"]), y0, x(p["temp"]) - x(p["temp"] - c["hysteresis"]), bh - 4); cr.fill()
        self._bounds = [(x(p["temp"]), "p%d" % i) for i, p in enumerate(c["points"])] + [(x(c["safety_max"]), "s")]
        for (xx, k), t in zip(self._bounds, [p["temp"] for p in c["points"]] + [c["safety_max"]]):
            cr.set_source_rgba(*self.P("text")); cr.set_line_width(2); cr.move_to(xx, y0 - 4); cr.line_to(xx, y0 + bh + 4); cr.stroke()
            lab = "%d°" % t; ext = cr.text_extents(lab)
            cr.set_source_rgba(*self.P("muted")); cr.move_to(xx - ext.width / 2, y0 + bh + 18); cr.show_text(lab)
        t = self.st["temp"]
        if t is not None:
            xx = x(max(T0, min(T1, t)))
            cr.set_source_rgba(*self.P("accent"))
            cr.move_to(xx - 7, 4); cr.line_to(xx + 7, 4); cr.line_to(xx, 13); cr.close_path(); cr.fill()
            cr.set_dash([3, 3]); cr.set_line_width(2); cr.move_to(xx, 14); cr.line_to(xx, y0 + bh); cr.stroke(); cr.set_dash([])
            cr.select_font_face("Sans", 0, 1); cr.move_to(xx + 10, 12); cr.show_text("%s %d°" % (T("now"), round(t)))
        cr.select_font_face("Sans"); cr.set_font_size(11); cr.set_source_rgba(*self.P("muted"))
        cr.move_to(L, h - 2); cr.show_text("%d°" % T0)
        ext = cr.text_extents("%d°" % T1); cr.move_to(R - ext.width, h - 2); cr.show_text("%d°" % T1)
        self._xinv = lambda px: T0 + (px - L) / (R - L) * (T1 - T0)

    def on_drag_begin(self, g, sx, sy):
        self.drag, self._drag_x = None, sx
        for xx, k in getattr(self, "_bounds", []):
            if abs(xx - sx) < 10:
                self.drag = k
                break

    def on_drag_update(self, g, dx, dy):
        if not self.drag or not self.draft:
            return
        t = round(self._xinv(self._drag_x + dx)); c = self.draft
        if self.drag == "s":
            lo = max(60, (c["points"][-1]["temp"] + 1) if c["points"] else 60)
            c["safety_max"] = max(lo, min(85, t))
            self._loading = True; self.safe_scale.set_value(c["safety_max"]); self._loading = False
        else:
            i = int(self.drag[1:]); pts = c["points"]
            lo = pts[i - 1]["temp"] + 1 if i else 31
            hi = (pts[i + 1]["temp"] if i + 1 < len(pts) else c["safety_max"]) - 1
            pts[i]["temp"] = max(lo, min(hi, t))
            self._loading = True; self.build_zone_rows(); self._loading = False
        self._changed()

    def _visible(self):
        now = time.time()
        return now, [h for h in self.hist if h[0] >= now - self.range]

    def draw_temp(self, area, cr, w, h):
        if not self.st:
            return
        c = self.draft or self.st["config"]
        now, H = self._visible()
        L, R, Tp, B = 34, w - 8, 8, h - 20
        temps = [x[1] for x in H if x[1] is not None]
        lo = (min([40] + temps) - 2) // 5 * 5
        hi = -(-(max([c["safety_max"] + 3] + temps) + 2) // 5) * 5
        y = lambda t: B - (t - lo) / (hi - lo) * (B - Tp)
        x = lambda s: L + (s - (now - self.range)) / self.range * (R - L)
        for a, b, lv in zones(c):
            a, b = max(a, lo), min(b, hi)
            if b <= a:
                continue
            cr.set_source_rgba(*self.P(lv + "_bg", .6)); cr.rectangle(L, y(b), R - L, y(a) - y(b)); cr.fill()
        cr.set_font_size(11); cr.set_line_width(1)
        t = lo
        while t <= hi:
            cr.set_source_rgba(*self.P("line")); cr.move_to(L, y(t)); cr.line_to(R, y(t)); cr.stroke()
            cr.set_source_rgba(*self.P("muted")); cr.move_to(2, y(t) + 4); cr.show_text("%d°" % t); t += 10
        for k in range(5):
            s = now - self.range + self.range * k / 4
            cr.move_to(min(x(s), R - 30), h - 4); cr.show_text(time.strftime("%H:%M", time.localtime(s)))
        cr.set_line_width(2.5); cr.set_line_join(1)
        for a, b in zip(H, H[1:]):
            if a[1] is None or b[1] is None or b[0] - a[0] > 10:
                continue
            cr.set_source_rgba(*self.P(zone_of(c, b[1])))
            cr.move_to(x(a[0]), y(a[1])); cr.line_to(x(b[0]), y(b[1])); cr.stroke()

    def draw_rpm(self, area, cr, w, h):
        if not self.st:
            return
        now, H = self._visible()
        L, R, B = 34, w - 8, h - 4
        mx = max([self.st.get("fan_max_rpm") or 4000] + [x[2] for x in H])
        x = lambda s: L + (s - (now - self.range)) / self.range * (R - L)
        y = lambda v: B - v / mx * (B - 6)
        cr.set_font_size(11); cr.set_source_rgba(*self.P("muted")); cr.move_to(0, 12); cr.show_text(T("rpm"))
        for a, b in zip(H, H[1:]):
            if b[0] - a[0] > 10:
                continue
            cr.set_source_rgba(*self.P(a[3] if a[3] in PALETTE else "bios", .85))
            cr.move_to(x(a[0]), B); cr.line_to(x(a[0]), y(a[2])); cr.line_to(x(b[0]), y(b[2])); cr.line_to(x(b[0]), B)
            cr.close_path(); cr.fill()


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        win = self.props.active_window or Window(self)
        win.present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
