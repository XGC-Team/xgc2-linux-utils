#!/usr/bin/env python3
"""xcli eval — robot overview TUI (English). q quits. --once prints a snapshot."""
from __future__ import print_function

import atexit
import curses
import locale
import os
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import xcli_eval_host as host_mod
except Exception:
    host_mod = None
try:
    import xcli_eval_ros as ros_mod
except Exception:
    ros_mod = None
try:
    import xcli_eval_mav as mav_mod
except Exception:
    mav_mod = None

OK, WARN, BAD, TITLE, DIM, HL, LINK, ACT = 1, 2, 3, 4, 5, 6, 7, 8
CARD, CARD_TITLE, CARD_DIM, CARD_BTN = 9, 10, 11, 12
BASE = 13
PANELS = ("iface", "wifi", "cpu", "disk", "tmux", "mem", "ros", "cmd")
KEY_BTAB = getattr(curses, "KEY_BTAB", 353)
BUTTON4 = getattr(curses, "BUTTON4_PRESSED", 0x00010000)
BUTTON5 = getattr(curses, "BUTTON5_PRESSED", 0x00200000)
ZONES = (
    ("shanghai", "Asia/Shanghai"),
    ("utc", "UTC"),
    ("tokyo", "Asia/Tokyo"),
    ("seoul", "Asia/Seoul"),
    ("singapore", "Asia/Singapore"),
    ("hongkong", "Asia/Hong_Kong"),
    ("taipei", "Asia/Taipei"),
)
IDLE_CHOICES = (("0", "never"), ("300", "5 min"), ("1800", "30 min"), ("3600", "60 min"))
SETTINGS = (
    ("time", "Time", (
        ("zone", "Timezone", "enum"),
        ("sync", "Enable NTP", "run"),
    )),
    ("net", "Network", (
        ("wifi_off", "Wi-Fi disconnect", "run"),
        ("wifi_on", "Wi-Fi restore", "run"),
    )),
    ("display", "Display", (
        ("idle", "Screen idle", "enum"),
    )),
    ("power", "Power", (
        ("sleep", "Inhibit sleep", "toggle"),
        ("cpu", "CPU governor", "enum"),
    )),
)
CMDS = (
    ("scan-lan", "Scan"),
    ("ping-gw", "Ping"),
    ("settings", "Settings"),
    ("help", "Help"),
)
ASSESS_SKIP = (
    "camera", "image", "extrinsic", "compressed", "theora", "parameter",
    "depth", "bond", "rgb_camera", "camera_info",
)
ASSESS_CORE = (
    "imu", "cmd_vel", "scan", "odom", "/tf", "joint", "status", "twist",
    "lidar", "points", "gnss", "gps",
)
ROS_SORTS = ("name", "hz", "jitter", "pubs", "subs")
# pane -> ((key, label, width), ...)
PANE_COLS = {
    "iface": (("name", "IFACE", 6), ("ipv4", "IPv4", 14), ("rx", "RX", 8), ("tx", "TX", 8)),
    "wifi": (("ssid", "SSID", 16), ("signal", "dBm", 5), ("security", "SEC", 8)),
    "cpu": (("cpu", "CPU%", 6), ("pid", "PID", 6), ("name", "NAME", 12)),
    "mem": (("rss", "RSS", 7), ("pid", "PID", 6), ("name", "NAME", 12)),
    "disk": (("path", "PATH", 14), ("used", "SIZE", 7), ("pct", "SHARE", 6)),
    "tmux": (("name", "SESSION", 14), ("windows", "WIN", 4), ("attached", "ATT", 4)),
    "ros": (
        ("name", "topic", 24),
        ("hz", "Hz", 7),
        ("jitter", "jitter ms", 10),
        ("pubs", "pubs", 5),
        ("subs", "subs", 5),
    ),
    "cmd": (("label", "COMMAND", 16),),
}
SORT_NUMERIC = frozenset((
    "rx", "tx", "signal", "cpu", "pid", "rss", "pct", "used", "free",
    "windows", "hz", "jitter", "pubs", "subs",
))
XCLI_ALLOWED = {
    "wifi": ("connect", "disconnect", "restore", "scan", "status"),
    "time": ("zone", "sync", "restore", "status"),
    "screen": ("idle", "restore", "status"),
    "sleep": ("off", "on", "restore", "status"),
    "cpu": ("performance", "balanced", "restore", "status"),
}


def fmt_bytes(n):
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return "none"
    for unit, div in (("G", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return "%.1f%s" % (n / div, unit)
    return "%dB" % int(n)


def fmt_rate(bps):
    if bps is None:
        return "none"
    return fmt_bytes(bps) + "/s"


def fmt_ms(v):
    if v is None:
        return "none"
    if abs(v) >= 100:
        return "%.0f" % v
    return "%.1f" % v


def spark(values):
    blocks = u"▁▂▃▄▅▆▇█"
    if not values:
        return ""
    lo, hi = min(values), max(values)
    out = []
    for v in values:
        if hi - lo < 1e-6:
            idx = 0
        else:
            idx = int(round((v - lo) / (hi - lo) * (len(blocks) - 1)))
            idx = max(0, min(len(blocks) - 1, idx))
        out.append(blocks[idx])
    return "".join(out)


def bar(pct, width=10):
    if pct is None:
        return "[" + ("-" * max(4, width)) + "]"
    width = max(4, int(width))
    pct = max(0.0, min(100.0, float(pct)))
    fill = min(width, int(round(width * pct / 100.0)))
    return "[" + ("#" * fill) + ("." * (width - fill)) + "]"


def short_nodes(nodes, limit=4):
    names = [n for n in (nodes or []) if n]
    if not names:
        return "none"
    shown = names[:limit]
    extra = len(names) - len(shown)
    text = ", ".join(shown)
    if extra > 0:
        text += " +%d" % extra
    return text


def topic_note(topic, warmup=False):
    if not topic:
        return "none"
    if topic.get("jump"):
        return "jump"
    std_ms = topic.get("std_ms")
    mean_ms = topic.get("mean_ms")
    if std_ms is not None and mean_ms is not None and std_ms > max(2.0, 0.25 * mean_ms):
        return "jitter"
    if warmup and topic.get("hz") in (0, 0.0, None):
        return "warmup"
    if (topic.get("n") or 0) == 0 and topic.get("hz") in (0, 0.0, None):
        if topic.get("n_pub"):
            return "silent"
        return "no pub"
    if topic.get("hz") is None:
        return "warmup"
    return "ok"


def note_pair(note):
    if note == "ok":
        return OK
    if note in ("jitter", "silent", "warmup"):
        return WARN
    if note in ("jump", "no pub"):
        return BAD
    return DIM


def empty_host():
    return {
        "host": "none",
        "load": (0.0, 0.0, 0.0),
        "nproc": 1,
        "mem": {"used": 0, "total": 0, "pct": None},
        "root": {"used": 0, "total": 0, "pct": None, "free": 0},
        "nics": [],
        "talker": None,
        "paths": [],
        "cpu_top": [],
        "mem_top": [],
        "route": {"gw": None, "iface": None},
        "governor": None,
        "clock": {"clock": "--:--:--", "date": "", "zone": "none"},
        "wifi_aps": [],
        "neigh": [],
        "tmux": [],
        "rtt_ms": None,
        "rtt_avg_ms": None,
        "rtts": [],
        "bags": None,
    }


def empty_ros():
    return {"ok": False, "distro": "none", "topics": [], "sampling": False, "warmup": True}


def _safe_token(value, loose=False):
    text = "%s" % (value or "")
    if not text or len(text) > (256 if loose else 128):
        return False
    if any(ch in text for ch in ("\n", "\r", "\x00")):
        return False
    if loose:
        return True
    if any(ch in text for ch in (";", "|", "&", "`", "$", ">", "<")):
        return False
    return True


def run_xcli(args, timeout=20):
    args = list(args or [])
    if len(args) < 2:
        return 1, "blocked"
    domain, verb = args[0], args[1]
    allowed = XCLI_ALLOWED.get(domain)
    if not allowed or verb not in allowed:
        return 1, "blocked command"
    for i, token in enumerate(args):
        loose = domain == "wifi" and verb == "connect" and i >= 2
        if not _safe_token(token, loose=loose):
            return 1, "blocked argument"
    if domain == "screen" and verb == "idle":
        if len(args) < 3 or not str(args[2]).isdigit():
            return 1, "blocked idle"
    if domain == "time" and verb == "zone":
        aliases = set(a for a, _z in ZONES) | set(("cn", "beijing", "shanghai"))
        if len(args) < 3 or str(args[2]).lower() not in aliases:
            return 1, "blocked zone"
    env = os.environ.copy()
    env["XGC2_LINUX_UTILS"] = HERE
    xcli = os.path.join(HERE, "xcli")
    try:
        out = subprocess.check_output(
            [xcli] + args,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            timeout=timeout,
            env=env,
        )
        return 0, (out or "").strip()
    except subprocess.CalledProcessError as exc:
        return exc.returncode, ((exc.output or "").strip() or "failed")
    except Exception as exc:
        return 1, str(exc)


def _read_zone():
    try:
        out = subprocess.check_output(
            ["timedatectl", "show", "-p", "Timezone", "--value"],
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=0.6,
        )
        return (out or "").strip() or "none"
    except Exception:
        pass
    try:
        with open("/etc/timezone", "r") as fh:
            return fh.read().strip() or "none"
    except Exception:
        return "none"


def _utf_ok():
    enc = sys.stdout.encoding or locale.getpreferredencoding(False) or "ascii"
    try:
        u"\u2500\u2502\u250c".encode(enc)
        return True
    except Exception:
        return False


def _which(name):
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return ""


def _same_slash24(ip, ipv4):
    a = (ip or "").split(".")
    b = (ipv4 or "").split(".")
    return len(a) == 4 and len(b) == 4 and a[:3] == b[:3]


def _subnet_prefix(ipv4):
    parts = (ipv4 or "").split(".")
    if len(parts) != 4:
        return ""
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return ""
    if any(n < 0 or n > 255 for n in nums):
        return ""
    return "%d.%d.%d." % (nums[0], nums[1], nums[2])


def _subnet_hosts(ipv4):
    prefix = _subnet_prefix(ipv4)
    if not prefix:
        return []
    mine = (ipv4 or "").split(".")[-1]
    return [prefix + str(i) for i in range(1, 255) if str(i) != mine]


def _arp_ips(ipv4, iface=None):
    found = []
    try:
        with open("/proc/net/arp", "r") as fh:
            lines = fh.read().splitlines()[1:]
    except (OSError, IOError):
        lines = []
    for line in lines:
        cols = line.split()
        if len(cols) < 6:
            continue
        ip, flags, dev = cols[0], cols[2], cols[5]
        if flags in ("0x0", "0"):
            continue
        if iface and dev != iface:
            continue
        if _same_slash24(ip, ipv4):
            found.append(ip)
    return found


def scan_lan(ipv4, iface, timeout=2.5):
    """Live hosts on the /24. ARP first, then fping if present, else ICMP ping.

    Not nmap. Only asks "is this IP up?"
    """
    found = set(_arp_ips(ipv4, iface))
    if ipv4:
        found.add(ipv4)
    prefix = _subnet_prefix(ipv4)
    cidr = prefix + "0/24" if prefix else ""
    fping = _which("fping")
    if fping and cidr:
        args = [fping, "-aq", "-t", "200", "-r", "0"]
        if iface:
            args.extend(["-I", iface])
        args.extend(["-g", cidr])
        try:
            out = subprocess.check_output(
                args, stderr=subprocess.STDOUT,
                universal_newlines=True, timeout=timeout,
            )
            for line in out.splitlines():
                ip = line.strip()
                if _same_slash24(ip, ipv4):
                    found.add(ip)
        except subprocess.CalledProcessError as exc:
            for line in (exc.output or "").splitlines():
                ip = line.strip()
                if _same_slash24(ip, ipv4):
                    found.add(ip)
        except Exception:
            pass
        return sorted(found, key=lambda s: [int(p) for p in s.split(".") if p.isdigit()])

    hosts = _subnet_hosts(ipv4)
    if not hosts:
        return sorted(found, key=lambda s: [int(p) for p in s.split(".") if p.isdigit()])
    stop_at = time.monotonic() + timeout
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
    except Exception:
        ThreadPoolExecutor = None

    def _one(ip):
        args = ["ping", "-c", "1", "-n", "-W", "1"]
        if iface:
            args.extend(["-I", iface])
        args.append(ip)
        try:
            subprocess.check_output(
                args, stderr=subprocess.DEVNULL, timeout=1.2,
            )
            return ip
        except Exception:
            return None

    if ThreadPoolExecutor is None:
        for ip in hosts[:48]:
            if time.monotonic() > stop_at:
                break
            hit = _one(ip)
            if hit:
                found.add(hit)
    else:
        with ThreadPoolExecutor(max_workers=64) as pool:
            futs = [pool.submit(_one, ip) for ip in hosts]
            for fut in as_completed(futs):
                if time.monotonic() > stop_at:
                    break
                try:
                    hit = fut.result()
                except Exception:
                    hit = None
                if hit:
                    found.add(hit)
    return sorted(found, key=lambda s: [int(p) for p in s.split(".") if p.isdigit()])


def sync_term_size(stdscr):
    """Apply real tty size only when it differs. Calling resizeterm every
    frame injects KEY_RESIZE and eats keys and clicks."""
    rows = cols = 0
    try:
        import fcntl
        import struct
        import termios
        raw = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b"\x00" * 8)
        rows, cols = struct.unpack("hhhh", raw)[:2]
    except Exception:
        rows = cols = 0
    try:
        cur_h, cur_w = stdscr.getmaxyx()
    except curses.error:
        cur_h, cur_w = 0, 0
    if rows < 10 or cols < 40:
        return False
    if int(rows) == cur_h and int(cols) == cur_w:
        return False
    try:
        curses.resizeterm(int(rows), int(cols))
        return True
    except curses.error:
        try:
            curses.resize_term(int(rows), int(cols))
            return True
        except curses.error:
            return False


class Pen(object):
    def __init__(self, stdscr):
        self.s = stdscr
        self.color = False
        self.utf = _utf_ok()
        self.clip = None
        if curses.has_colors():
            try:
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(BASE, curses.COLOR_WHITE, -1)
                curses.init_pair(OK, curses.COLOR_GREEN, -1)
                curses.init_pair(WARN, curses.COLOR_YELLOW, -1)
                curses.init_pair(BAD, curses.COLOR_RED, -1)
                curses.init_pair(TITLE, curses.COLOR_CYAN, -1)
                curses.init_pair(DIM, curses.COLOR_WHITE, -1)
                curses.init_pair(HL, curses.COLOR_BLACK, curses.COLOR_CYAN)
                curses.init_pair(LINK, curses.COLOR_CYAN, -1)
                curses.init_pair(ACT, curses.COLOR_BLUE, -1)
                curses.init_pair(CARD, curses.COLOR_WHITE, curses.COLOR_BLUE)
                curses.init_pair(CARD_TITLE, curses.COLOR_CYAN, curses.COLOR_BLUE)
                curses.init_pair(CARD_DIM, curses.COLOR_WHITE, curses.COLOR_BLUE)
                curses.init_pair(CARD_BTN, curses.COLOR_BLACK, curses.COLOR_WHITE)
                self.color = True
            except curses.error:
                self.color = False

    def a(self, pair, extra=0):
        if not self.color:
            return extra
        if not pair:
            pair = BASE
        return curses.color_pair(pair) | extra

    def paint(self):
        try:
            self.s.erase()
        except curses.error:
            pass

    def size(self):
        return self.s.getmaxyx()

    def region(self, x, w):
        self.clip = (int(x), int(x) + int(w))

    def unregion(self):
        self.clip = None

    def put(self, y, x, text, pair=0, extra=0):
        h, w = self.size()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        text = (text or "").replace("\n", " ").replace("\t", " ")
        room = w - x
        if self.clip is not None:
            room = min(room, self.clip[1] - x - 1)
        if room <= 1:
            return
        if len(text) > room - 1:
            text = text[: room - 1]
        try:
            self.s.addstr(y, x, text, self.a(pair, extra))
        except curses.error:
            pass

    def box(self, y, x, h, w, title, focus=False):
        if h < 2 or w < 4:
            return
        if self.utf:
            tl, tr, bl, br, hz, vt = u"┌", u"┐", u"└", u"┘", u"─", u"│"
        else:
            tl, tr, bl, br, hz, vt = "+", "+", "+", "+", "-", "|"
        pair = TITLE if focus else DIM
        extra = curses.A_BOLD if focus else 0
        inner = max(0, w - 2)
        self.put(y, x, tl + (hz * inner) + tr, pair, extra)
        for i in range(1, h - 1):
            self.put(y + i, x, vt, pair, extra)
            self.put(y + i, x + w - 1, vt, pair, extra)
        self.put(y + h - 1, x, bl + (hz * inner) + br, pair, extra)
        if title:
            self.put(y, x + 2, " %s " % title, TITLE, curses.A_BOLD)
        for i in range(1, h - 1):
            self.put(y + i, x + 1, " " * max(0, w - 2), BASE)

    def card(self, y, x, h, w, title):
        """Solid modal plate so the popup does not share the terminal background."""
        if h < 2 or w < 4:
            return
        if self.utf:
            tl, tr, bl, br, hz, vt = u"┌", u"┐", u"└", u"┘", u"─", u"│"
        else:
            tl, tr, bl, br, hz, vt = "+", "+", "+", "+", "-", "|"
        fill = CARD if self.color else 0
        edge = CARD_TITLE if self.color else TITLE
        extra = curses.A_BOLD
        if not self.color:
            extra |= curses.A_REVERSE
        for i in range(h):
            self.put(y + i, x, " " * w, fill, extra if not self.color else 0)
        inner = max(0, w - 2)
        self.put(y, x, tl + (hz * inner) + tr, edge, extra)
        for i in range(1, h - 1):
            self.put(y + i, x, vt, edge, extra)
            self.put(y + i, x + w - 1, vt, edge, extra)
        self.put(y + h - 1, x, bl + (hz * inner) + br, edge, extra)
        if title:
            self.put(y, x + 2, " %s " % title, edge, extra)

    def thumb(self, y, x, h, n, scroll, view):
        if n <= view or h < 2:
            return
        track = max(1, h)
        pos = int(round((scroll / float(max(1, n - view))) * (track - 1)))
        ch = u"█" if self.utf else "#"
        self.put(y + pos, x, ch, TITLE)


class App(object):
    def __init__(self):
        self.host = empty_host()
        self.ros = empty_ros()
        self.page = "home"
        self.view = "host"
        self.focus = 0
        self.sel = {name: 0 for name in PANELS}
        self.sel["mavp"] = 0
        self.scroll = {name: 0 for name in PANELS}
        self.menu_sel = 0
        self.dialog = None
        self.flash = ""
        self.host_s = None
        self.ros_s = None
        self.mav_s = None
        self.mav = {}
        self._dirty = True
        self.hits = []
        self.dlg_hits = []
        self.geom = {}
        self.menu_hint = {}
        self._click = None
        self._mouse_guard = 0.0
        self._quit_tmux = None
        self._scan_lock = threading.Lock()
        self._scan_busy = False
        self.query = {name: "" for name in PANELS}
        self.searching = False
        self.sort_key = {
            "iface": "name",
            "wifi": "signal",
            "cpu": "cpu",
            "mem": "rss",
            "disk": "pct",
            "tmux": "name",
            "ros": "name",
            "cmd": "label",
        }
        self.sort_rev = {
            "iface": False,
            "wifi": True,
            "cpu": True,
            "mem": True,
            "disk": True,
            "tmux": False,
            "ros": False,
            "cmd": False,
        }
        self.settings_open = set(["time", "power"])

    def pane(self):
        return PANELS[self.focus]

    def nics(self):
        return [n for n in (self.host.get("nics") or []) if n.get("name") != "lo"]

    def wifi_aps(self):
        return self.host.get("wifi_aps") or []

    def row_hay(self, name, row):
        if name == "iface":
            wifi = (row.get("wifi") or {}).get("ssid") or ""
            return "%s %s %s" % (row.get("name") or "", row.get("ipv4") or "", wifi)
        if name == "wifi":
            return "%s %s" % (row.get("ssid") or "", row.get("security") or "")
        if name in ("cpu", "mem"):
            return "%s %s" % (row.get("name") or "", row.get("pid") or "")
        if name == "disk":
            return row.get("path") or ""
        if name == "tmux":
            return row.get("name") or ""
        if name == "ros":
            bits = [row.get("name") or ""]
            bits.extend(row.get("pubs") or [])
            bits.extend(row.get("subs") or [])
            return " ".join(bits)
        if name == "cmd":
            return "%s %s" % (row[0], row[1])
        return ""

    def _filter_rows(self, name, rows):
        q = (self.query.get(name) or "").strip().lower()
        if not q:
            return list(rows)
        out = []
        for row in rows:
            if q in self.row_hay(name, row).lower():
                out.append(row)
        return out

    def _cell(self, name, key, row):
        if name == "cmd":
            return row[1] if key == "label" else row[0]
        if name == "iface":
            return {
                "name": row.get("name") or "",
                "ipv4": row.get("ipv4") or "",
                "rx": row.get("rx_bps") or 0,
                "tx": row.get("tx_bps") or 0,
            }.get(key)
        if name == "wifi":
            return {
                "ssid": row.get("ssid") or "",
                "signal": row.get("signal"),
                "security": row.get("security") or "",
            }.get(key)
        if name == "cpu":
            return {"cpu": row.get("cpu"), "pid": row.get("pid"), "name": row.get("name") or ""}.get(key)
        if name == "mem":
            return {"rss": row.get("rss"), "pid": row.get("pid"), "name": row.get("name") or ""}.get(key)
        if name == "disk":
            return {
                "path": row.get("path") or "",
                "pct": row.get("pct"),
                "used": row.get("used"),
                "free": row.get("free"),
            }.get(key)
        if name == "tmux":
            return {
                "name": row.get("name") or "",
                "windows": row.get("windows") or 0,
                "attached": 1 if row.get("attached") else 0,
            }.get(key)
        if name == "ros":
            return {
                "name": row.get("name") or "",
                "hz": row.get("hz"),
                "jitter": row.get("std_ms"),
                "pubs": row.get("n_pub") or 0,
                "subs": row.get("n_sub") or 0,
            }.get(key)
        return None

    def _sort_rows(self, name, rows):
        key = self.sort_key.get(name)
        rev = bool(self.sort_rev.get(name))
        numeric = key in SORT_NUMERIC

        def k(row):
            val = self._cell(name, key, row)
            if numeric:
                try:
                    return (0, float(val))
                except (TypeError, ValueError):
                    return (1, 0.0)
            return (0, ("%s" % (val or "")).lower())

        return sorted(rows, key=k, reverse=rev)

    def toggle_sort(self, name, key):
        if self.sort_key.get(name) == key:
            self.sort_rev[name] = not self.sort_rev.get(name)
        else:
            self.sort_key[name] = key
            self.sort_rev[name] = key in SORT_NUMERIC
        self.sel[name] = 0
        self.scroll[name] = 0

    def rows_of(self, name):
        if name == "iface":
            rows = self.nics()
        elif name == "wifi":
            rows = self.wifi_aps()
        elif name == "cpu":
            rows = self.host.get("cpu_top") or []
        elif name == "mem":
            rows = self.host.get("mem_top") or []
        elif name == "disk":
            rows = self.host.get("paths") or []
        elif name == "tmux":
            rows = self.host.get("tmux") or []
        elif name == "ros":
            rows = self.ros.get("topics") or []
        elif name == "cmd":
            rows = list(CMDS)
        else:
            rows = []
        return self._sort_rows(name, self._filter_rows(name, rows))

    def clamp_pane(self, name, view):
        rows = self.rows_of(name)
        n = len(rows)
        sel = self.sel.get(name, 0)
        scroll = self.scroll.get(name, 0)
        view = max(1, int(view or 1))
        if n <= 0:
            self.sel[name] = 0
            self.scroll[name] = 0
            return
        if sel >= n:
            sel = n - 1
        if sel < 0:
            sel = 0
        if sel < scroll:
            scroll = sel
        if sel >= scroll + view:
            scroll = sel - view + 1
        if scroll > max(0, n - view):
            scroll = max(0, n - view)
        if scroll < 0:
            scroll = 0
        self.sel[name] = sel
        self.scroll[name] = scroll

    def move(self, delta):
        name = self.pane()
        n = len(self.rows_of(name))
        if n <= 0:
            return
        self.sel[name] = max(0, min(n - 1, self.sel.get(name, 0) + delta))
        view = self.geom.get(name, {}).get("view", 4)
        self.clamp_pane(name, view)

    def warmup(self):
        return bool(self.ros.get("warmup"))

    def current_wifi(self):
        for ap in self.wifi_aps():
            if ap.get("in_use"):
                return ap
        for nic in self.nics():
            info = nic.get("wifi") or {}
            if info.get("ssid"):
                return {
                    "ssid": info.get("ssid"),
                    "signal": info.get("signal"),
                    "in_use": True,
                    "security": "",
                }
        return None

    def assess(self):
        bits = []
        wifi = self.current_wifi()
        if wifi and wifi.get("signal") is not None and wifi.get("signal") <= -75:
            bits.append("wifi weak %sdBm" % wifi.get("signal"))
        rtt = self.host.get("rtt_ms")
        if rtt is not None and rtt >= 80:
            bits.append("gw rtt %.0fms" % rtt)
        elif self.host.get("route", {}).get("gw") and rtt is None:
            bits.append("gw no reply")
        paths = self.host.get("paths") or []
        if paths:
            fat = max(paths, key=lambda p: p.get("pct") or 0)
            if (fat.get("pct") or 0) >= 80:
                bits.append("disk %s %.0f%%" % (fat.get("path"), fat.get("pct") or 0))
        bags = self.host.get("bags") or {}
        if bags.get("bags"):
            bits.append("%d bags %s" % (bags.get("bags"), fmt_bytes(bags.get("bytes"))))
        load = self.host.get("load") or (0.0, 0.0, 0.0)
        nproc = float(self.host.get("nproc") or 1)
        if load[0] >= nproc:
            bits.append("load %.2f/%d" % (load[0], int(nproc)))
        if self.ros.get("ok") and not self.warmup():
            for t in self.ros.get("topics") or []:
                name = (t.get("name") or "").lower()
                if any(s in name for s in ASSESS_SKIP):
                    continue
                if not any(s in name for s in ASSESS_CORE):
                    continue
                note = topic_note(t)
                if note in ("jump", "jitter", "silent"):
                    extra = ""
                    if t.get("std_ms") is not None:
                        extra = " ±%sms" % fmt_ms(t.get("std_ms"))
                    bits.append("%s %s%s" % (note, t.get("name"), extra))
                    break
        return bits[:3] or ["nominal"]

    def refresh_menu_hint(self):
        wifi = self.current_wifi() or {}
        route = self.host.get("route") or {}
        clock = self.host.get("clock") or {}
        self.menu_hint = {
            "zone": clock.get("zone") or _read_zone(),
            "governor": self.host.get("governor") or "none",
            "gw": route.get("gw") or "none",
            "wifi": wifi.get("ssid") or "none",
            "sleep": (
                "off"
                if os.path.isfile("/etc/systemd/logind.conf.d/xgc2-no-suspend.conf")
                else "host-default"
            ),
        }

    def selected_row(self, name=None):
        name = name or self.pane()
        rows = self.rows_of(name)
        if not rows:
            return None
        idx = self.sel.get(name, 0)
        if idx < 0 or idx >= len(rows):
            return None
        return rows[idx]

    def ask_kill(self):
        if self.pane() not in ("cpu", "mem"):
            self.flash = "select a process in CPU or MEM"
            return
        proc = self.selected_row()
        if not proc:
            self.flash = "no process"
            return
        pid = proc.get("pid")
        name = proc.get("name") or "?"
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            self.flash = "bad pid"
            return
        if pid <= 1 or pid == os.getpid() or name in ("init", "systemd", "kthreadd"):
            self.flash = "refused to signal pid %s" % pid
            return
        self.dialog = {
            "kind": "confirm",
            "title": "Terminate process",
            "body": "Send SIGTERM to pid %s (%s)?" % (pid, name),
            "action": "kill",
            "pid": pid,
            "name": name,
        }

    def ask_ping(self, target=None):
        route = self.host.get("route") or {}
        if not target:
            target = route.get("gw")
        if self.pane() == "iface":
            nic = self.selected_row()
            if nic and not target:
                target = route.get("gw")
        if not target or target in ("none", "-", "127.0.0.1"):
            self.flash = "no gateway to ping"
            return
        if not _safe_token(target) or any(ch in str(target) for ch in ("/", "\\", " ")):
            self.flash = "blocked ping target"
            return
        self.dialog = {
            "kind": "confirm",
            "title": "Probe host",
            "body": "Send 2 ICMP echoes to %s?" % target,
            "action": "ping",
            "target": target,
        }

    def ask_wifi(self, ap=None):
        ap = ap or self.selected_row("wifi")
        if not ap:
            self.flash = "select a Wi-Fi network"
            return
        ssid = ap.get("ssid") or ""
        if ap.get("in_use"):
            self.flash = "already on %s" % ssid
            return
        security = (ap.get("security") or "open").lower()
        open_net = security in ("open", "", "--", "none")
        if open_net:
            self.dialog = {
                "kind": "confirm",
                "title": "Join Wi-Fi",
                "body": "Connect to open network %s?" % ssid,
                "action": "wifi_open",
                "ssid": ssid,
            }
            return
        self.dialog = {
            "kind": "form",
            "title": "Join %s" % ssid,
            "fields": [
                {"name": "ssid", "label": "SSID", "value": ssid, "secret": False},
                {"name": "password", "label": "Password", "value": "", "secret": True},
            ],
            "cur": 1,
            "submit": "wifi_connect",
        }

    def show_topic(self):
        topic = self.selected_row("ros")
        if not topic:
            self.flash = "select a ROS topic"
            return
        pubs = topic.get("pubs") or []
        subs = topic.get("subs") or []
        lines = [
            topic.get("name") or "?",
            "Hz %s   mean dt %s ms   jitter %s ms"
            % (
                "%.1f" % topic["hz"] if topic.get("hz") is not None else "none",
                fmt_ms(topic.get("mean_ms")),
                fmt_ms(topic.get("std_ms")),
            ),
            "",
            "publishers (%d)" % len(pubs),
        ]
        if pubs:
            for name in pubs:
                lines.append("  %s" % name)
        else:
            lines.append("  none")
        lines.append("subscribers (%d)" % len(subs))
        if subs:
            for name in subs:
                lines.append("  %s" % name)
        else:
            lines.append("  none")
        self.dialog = {"kind": "info", "title": "Topic topology", "lines": lines}

    def tmux_here(self):
        if not os.environ.get("TMUX"):
            return None
        try:
            out = subprocess.check_output(
                ["tmux", "display-message", "-p", "#S"],
                stderr=subprocess.DEVNULL,
                universal_newlines=True,
                timeout=0.4,
            )
            return (out or "").strip() or None
        except Exception:
            return None

    def ask_tmux(self):
        sess = self.selected_row("tmux")
        if not sess:
            self.flash = "no tmux session"
            return
        name = sess.get("name")
        if not name or not _safe_token(name):
            self.flash = "blocked tmux name"
            return
        here = self.tmux_here()
        if here and here == name:
            self.flash = "already in tmux session %s" % name
            return
        if os.environ.get("TMUX"):
            body = (
                "Already inside tmux. Switch this client to '%s'? "
                "xcli will exit (no nested attach)."
            ) % name
        else:
            body = "Attach session '%s'? This panel will exit." % name
        self.dialog = {
            "kind": "confirm",
            "title": "Switch tmux" if os.environ.get("TMUX") else "Attach tmux",
            "body": body,
            "action": "tmux",
            "name": name,
        }

    def open_menu(self):
        self.page = "menu"
        self.refresh_menu_hint()

    def open_help(self):
        self.dialog = {
            "kind": "info",
            "title": "Keys",
            "lines": [
                "Tab / arrows   move between panes",
                "j k            move inside the focused pane",
                "Enter          act (join / kill / topology / attach)",
                "click          select, or press a dialog button",
                "double-click   same as Enter on that row",
                "K              terminate selected process",
                "P              ping default gateway",
                "Scan           ARP + ICMP ping /24, not nmap",
                "/              filter the focused pane",
                "click header   sort that column   s cycle sort",
                "y / n          confirm or cancel a dialog",
                "bottom bar     click a command",
                "q              quit    Esc  back / cancel",
            ],
        }

    def activate(self):
        if self.page == "menu":
            self.menu_activate()
            return
        name = self.pane()
        if name == "wifi":
            self.ask_wifi()
        elif name in ("cpu", "mem"):
            self.ask_kill()
        elif name == "iface":
            self.ask_ping()
        elif name == "ros":
            self.show_topic()
        elif name == "tmux":
            self.ask_tmux()
        elif name == "cmd":
            self.run_cmd(CMDS[self.sel.get("cmd", 0)][0])
        elif name == "disk":
            self.flash = "disk is observe-only"

    def run_cmd(self, key):
        if key == "scan-lan":
            self.start_lan_scan()
        elif key == "ping-gw":
            self.ask_ping()
        elif key == "settings":
            self.open_menu()
        elif key == "help":
            self.open_help()

    def start_lan_scan(self):
        with self._scan_lock:
            if self._scan_busy:
                self.flash = "scan already running"
                return
            self._scan_busy = True
        nic = None
        if self.pane() == "iface":
            nic = self.selected_row("iface")
        if not nic:
            nics = self.nics()
            route = self.host.get("route") or {}
            for item in nics:
                if item.get("name") == route.get("iface"):
                    nic = item
                    break
            if not nic and nics:
                nic = nics[0]
        if not nic or not nic.get("ipv4"):
            self._scan_busy = False
            self.flash = "no iface to scan"
            return
        ipv4 = nic.get("ipv4")
        iface = nic.get("name")
        self.flash = "scanning %s/24 on %s (ARP+ICMP, not nmap) ..." % (
            ".".join((ipv4 or "").split(".")[:3]), iface,
        )
        known = list(self.host.get("neigh") or [])

        def _job():
            try:
                hits = scan_lan(ipv4, iface, timeout=2.5)
            except Exception:
                hits = []
            lines = ["subnet of %s  iface %s" % (ipv4, iface)]
            seen = set()
            for ip in hits:
                seen.add(ip)
                tag = "live"
                if ip == ipv4:
                    tag = "this host"
                lines.append("  %-16s  %s" % (ip, tag))
            for row in known:
                ip = row.get("ip")
                if not ip or ip in seen:
                    continue
                lines.append("  %-16s  arp %s" % (ip, row.get("state") or ""))
            if len(lines) == 1:
                lines.append("  none")
            self.dialog = {
                "kind": "info",
                "title": "LAN scan",
                "lines": lines[:28],
            }
            self.flash = "scan done  %d live" % len(hits)
            with self._scan_lock:
                self._scan_busy = False

        th = threading.Thread(target=_job, name="xcli-lan-scan")
        th.daemon = True
        th.start()

    def apply_confirm(self, yes):
        dialog = self.dialog
        self.dialog = None
        if not yes or not dialog:
            self.flash = "cancelled"
            return
        action = dialog.get("action")
        if action == "kill":
            try:
                pid = int(dialog["pid"])
            except (TypeError, ValueError):
                self.flash = "bad pid"
                return
            if pid <= 1 or pid == os.getpid():
                self.flash = "refused to signal pid %s" % pid
                return
            try:
                os.kill(pid, signal.SIGTERM)
                self.flash = "SIGTERM %s (%s)" % (pid, dialog["name"])
            except Exception as exc:
                self.flash = "kill failed: %s" % exc
        elif action == "ping":
            target = dialog.get("target")
            if not _safe_token(target):
                self.flash = "blocked ping target"
                return
            self._do_ping(target)
        elif action == "wifi_open":
            code, out = run_xcli(["wifi", "connect", dialog.get("ssid")], timeout=30)
            self.flash = (out.splitlines() or [""])[-1] or ("ok" if code == 0 else "failed")
        elif action == "xcli":
            code, out = run_xcli(dialog.get("args") or [])
            self.flash = (out.splitlines() or [""])[-1] or ("ok" if code == 0 else "failed")
            self.refresh_menu_hint()
        elif action == "tmux":
            self._quit_tmux = dialog.get("name")
        elif action == "reboot_fcu":
            if self.mav_s is None:
                self.flash = "MAVROS sampler none"
            else:
                try:
                    ok, msg = self.mav_s.reboot_fcu()
                    self.flash = msg
                except Exception as exc:
                    self.flash = str(exc)
        elif action == "reboot_pc":
            try:
                subprocess.check_call(["sudo", "-n", "reboot"], timeout=5)
                self.flash = "reboot issued"
            except Exception as exc:
                self.flash = "reboot failed: %s" % exc

    def submit_form(self):
        dialog = self.dialog or {}
        fields = {f["name"]: f.get("value") or "" for f in dialog.get("fields") or []}
        self.dialog = None
        if dialog.get("submit") == "wifi_connect":
            ssid = fields.get("ssid") or ""
            password = fields.get("password") or ""
            if not ssid:
                self.flash = "ssid required"
                return
            args = ["wifi", "connect", ssid]
            if password:
                args.append(password)
            code, out = run_xcli(args, timeout=30)
            self.flash = (out.splitlines() or [""])[-1] or ("ok" if code == 0 else "failed")
        elif dialog.get("submit") == "mav_param":
            if self.mav_s is None:
                self.flash = "MAVROS sampler none"
                return
            ok, msg = self.mav_s.param_set(fields.get("id"), fields.get("value"))
            self.flash = msg
            try:
                self.mav = self.mav_s.snapshot() or {}
            except Exception:
                pass

    def _do_ping(self, target):
        try:
            out = subprocess.check_output(
                ["ping", "-c", "2", "-W", "1", str(target)],
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                timeout=5,
            )
            loss = "ok"
            rtt = ""
            for line in out.splitlines():
                if "packet loss" in line:
                    loss = line.strip()
                if "min/avg/max" in line or "rtt" in line.lower():
                    rtt = line.strip()
            self.flash = "ping %s: %s %s" % (target, loss, rtt)
        except Exception as exc:
            self.flash = "ping %s failed: %s" % (target, exc)

    def settings_rows(self):
        rows = []
        for sid, title, items in SETTINGS:
            rows.append({"kind": "sec", "sid": sid, "title": title})
            if sid in self.settings_open:
                for iid, label, typ in items:
                    rows.append({
                        "kind": "row",
                        "sid": sid,
                        "iid": iid,
                        "title": label,
                        "typ": typ,
                    })
        return rows

    def setting_value(self, iid):
        hint = self.menu_hint or {}
        if iid == "zone":
            return hint.get("zone") or "none"
        if iid == "idle":
            return "set"
        if iid == "cpu":
            return hint.get("governor") or "none"
        if iid == "sleep":
            return hint.get("sleep") or "none"
        if iid == "sync":
            return "run"
        if iid == "wifi_off":
            return hint.get("wifi") or "none"
        if iid == "wifi_on":
            return "restore"
        return ""

    def menu_activate(self):
        rows = self.settings_rows()
        if not rows or self.menu_sel >= len(rows):
            return
        row = rows[self.menu_sel]
        if row["kind"] == "sec":
            sid = row["sid"]
            if sid in self.settings_open:
                self.settings_open.discard(sid)
            else:
                self.settings_open.add(sid)
            return
        iid = row.get("iid")
        if iid == "zone":
            self.dialog = {
                "kind": "pick",
                "title": "Time zone",
                "items": [
                    {"label": "%-10s  %s" % (alias, zone), "value": alias}
                    for alias, zone in ZONES
                ],
                "sel": 0,
                "submit": "timezone",
            }
        elif iid == "sync":
            self.dialog = {
                "kind": "confirm",
                "title": "Time sync",
                "body": "Enable NTP on this host?",
                "action": "xcli",
                "args": ["time", "sync"],
            }
        elif iid == "wifi_off":
            self.dialog = {
                "kind": "confirm",
                "title": "Wi-Fi disconnect",
                "body": "Disconnect the current Wi-Fi device?",
                "action": "xcli",
                "args": ["wifi", "disconnect"],
            }
        elif iid == "wifi_on":
            self.dialog = {
                "kind": "confirm",
                "title": "Wi-Fi restore",
                "body": "Restore the previous Wi-Fi connection?",
                "action": "xcli",
                "args": ["wifi", "restore"],
            }
        elif iid == "idle":
            self.dialog = {
                "kind": "pick",
                "title": "Screen idle",
                "items": [
                    {"label": "%s  (%s)" % (sec, label), "value": sec}
                    for sec, label in IDLE_CHOICES
                ],
                "sel": 2,
                "submit": "screen",
            }
        elif iid == "sleep":
            sleeping = (self.menu_hint or {}).get("sleep") == "off"
            if sleeping:
                self.dialog = {
                    "kind": "confirm",
                    "title": "Sleep restore",
                    "body": "Restore the previous sleep policy?",
                    "action": "xcli",
                    "args": ["sleep", "on"],
                }
            else:
                self.dialog = {
                    "kind": "confirm",
                    "title": "Sleep off",
                    "body": "Inhibit suspend, hibernate and lid sleep?",
                    "action": "xcli",
                    "args": ["sleep", "off"],
                }
        elif iid == "cpu":
            self.dialog = {
                "kind": "pick",
                "title": "CPU governor",
                "items": [
                    {"label": "performance", "value": "performance"},
                    {"label": "balanced (restore)", "value": "balanced"},
                ],
                "sel": 0,
                "submit": "cpu",
            }

    def pick_submit(self):
        dialog = self.dialog or {}
        items = dialog.get("items") or []
        if not items:
            self.dialog = None
            return
        item = items[min(dialog.get("sel") or 0, len(items) - 1)]
        submit = dialog.get("submit")
        self.dialog = None
        if submit == "timezone":
            code, out = run_xcli(["time", "zone", item.get("value")])
            self.flash = (out.splitlines() or [""])[-1] or ("ok" if code == 0 else "failed")
            self.refresh_menu_hint()
        elif submit == "screen":
            code, out = run_xcli(["screen", "idle", item.get("value")])
            self.flash = (out.splitlines() or [""])[-1] or ("ok" if code == 0 else "failed")
        elif submit == "cpu":
            verb = "performance" if item.get("value") == "performance" else "balanced"
            code, out = run_xcli(["cpu", verb])
            self.flash = (out.splitlines() or [""])[-1] or ("ok" if code == 0 else "failed")
            self.refresh_menu_hint()
        elif submit == "mav_ns":
            if self.mav_s is not None:
                self.mav_s.set_ns(item.get("value") or "")
                self.flash = "namespace %s" % (item.get("value") or "/")
                try:
                    self.mav = self.mav_s.snapshot() or {}
                except Exception:
                    pass

    def set_view(self, view):
        if view not in ("host", "mav"):
            return
        if view == self.view:
            return
        self.view = view
        self.dialog = None
        if view == "mav":
            if self.ros_s is not None:
                try:
                    self.ros_s.stop()
                except Exception:
                    pass
            if mav_mod is not None and hasattr(mav_mod, "MavSampler"):
                if self.mav_s is None:
                    try:
                        self.mav_s = mav_mod.MavSampler()
                    except Exception:
                        self.mav_s = None
                if self.mav_s is not None:
                    try:
                        self.mav_s.start()
                    except Exception:
                        pass
            self.flash = "MAVROS page  (Host stats paused)"
        else:
            if self.mav_s is not None:
                try:
                    self.mav_s.stop()
                except Exception:
                    pass
            if self.ros_s is not None:
                try:
                    self.ros_s.start()
                except Exception:
                    pass
            self.flash = "Host page  (MAVROS probes paused)"
        self._dirty = True

    def mav_edit_param(self):
        params = (self.mav or {}).get("params") or []
        if not params:
            self.flash = "no PX4 params (is MAVROS up?)"
            return
        i = self.sel.get("mavp", 0)
        if i < 0 or i >= len(params):
            return
        p = params[i]
        cur = p.get("value")
        self.dialog = {
            "kind": "form",
            "title": p.get("label") or p.get("id"),
            "fields": [
                {"name": "id", "label": "Param", "value": p.get("id") or "", "secret": False},
                {
                    "name": "value",
                    "label": "Value",
                    "value": "" if cur is None else ("%s" % cur),
                    "secret": False,
                },
            ],
            "cur": 1,
            "submit": "mav_param",
        }

    def mav_cmd(self, key):
        if key == "host":
            self.set_view("host")
        elif key == "mav-set":
            self.mav_edit_param()
        elif key == "mav-ns":
            nss = (self.mav or {}).get("nss") or []
            if not nss:
                self.flash = "no MAVROS namespace"
                return
            self.dialog = {
                "kind": "pick",
                "title": "MAVROS namespace",
                "items": [{"label": n or "/", "value": n} for n in nss],
                "sel": 0,
                "submit": "mav_ns",
            }
        elif key == "reboot-fcu":
            self.dialog = {
                "kind": "confirm",
                "title": "Reboot FCU",
                "body": "Reboot the flight controller now?",
                "action": "reboot_fcu",
            }
        elif key == "reboot-pc":
            self.dialog = {
                "kind": "confirm",
                "title": "Reboot this computer",
                "body": "sudo reboot this host? Confirm twice in your head.",
                "action": "reboot_pc",
            }

    def refresh(self):
        try:
            if self.host_s is not None:
                self.host = self.host_s.snapshot() or empty_host()
        except Exception:
            self.host = empty_host()
        if self.view == "host":
            try:
                if self.ros_s is not None:
                    self.ros = self.ros_s.snapshot() or empty_ros()
            except Exception:
                self.ros = empty_ros()
        else:
            try:
                if self.mav_s is not None:
                    self.mav = self.mav_s.snapshot() or {}
            except Exception:
                self.mav = {}
        for name in PANELS:
            view = self.geom.get(name, {}).get("view", 4)
            self.clamp_pane(name, view)
        self._dirty = True

    def draw(self, ui):
        ui.paint()
        self.hits = []
        self.dlg_hits = []
        if self.page == "menu":
            self.draw_menu(ui)
        elif self.view == "mav":
            self.draw_mav(ui)
        else:
            self.draw_home(ui)
        if self.dialog:
            self.draw_dialog(ui)
        try:
            ui.s.refresh()
        except curses.error:
            pass
        self._dirty = False

    def draw_tabs(self, ui, y, w):
        host = "[Host]"
        mav = "[MAVROS]"
        x = max(8, w - len(host) - len(mav) - 3)
        ui.put(y, x, host, HL if self.view == "host" else DIM)
        ui.put(y, x + len(host) + 1, mav, HL if self.view == "mav" else TITLE)
        self.hits.append((y, "tab", 0, "host", x, x + len(host)))
        self.hits.append(
            (y, "tab", 1, "mav", x + len(host) + 1, x + len(host) + 1 + len(mav))
        )

    def _pane_box(self, ui, name, y, x, h, w, title):
        q = self.query.get(name) or ""
        if self.searching and self.pane() == name:
            title = "%s  /%s_" % (title, q)
        elif q:
            title = "%s  /%s" % (title, q)
        ui.unregion()
        ui.box(y, x, h, w, title, self.pane() == name)
        ui.region(x, w)
        view = max(1, h - 2)
        self.geom[name] = {"y": y, "x": x, "h": h, "w": w, "view": max(1, view - 1)}
        self.clamp_pane(name, max(1, view - 1))
        return view

    def _draw_cols(self, ui, name, y, x):
        hx = x + 2
        for key, label, width in (PANE_COLS.get(name) or ()):
            mark = label
            pair = DIM
            if self.sort_key.get(name) == key:
                mark = "%s%s" % (label, "v" if self.sort_rev.get(name) else "^")
                pair = TITLE
            ui.put(y, hx, "%-*s" % (width, mark[:width]), pair)
            self.hits.append((y, "sort", key, name, hx, hx + width))
            hx += width + 1
        return 1

    def draw_home(self, ui):
        h, w = ui.size()
        header_h = 4
        footer_h = 4
        body_h = max(10, h - header_h - footer_h)
        c1 = max(22, w // 3)
        c2 = max(24, w // 3)
        c3 = max(20, w - c1 - c2)
        top_h = max(6, body_h * 5 // 12)
        mid_h = max(5, body_h * 3 // 12)
        ros_h = max(6, body_h - top_h - mid_h)

        clock = self.host.get("clock") or {}
        mem = self.host.get("mem") or {}
        root = self.host.get("root") or {}
        load = self.host.get("load") or (0.0, 0.0, 0.0)
        wifi = self.current_wifi()
        rtt = self.host.get("rtt_ms")
        rtt_avg = self.host.get("rtt_avg_ms")
        ros_ok = self.ros.get("ok")
        ui.unregion()
        ui.box(0, 0, header_h, w, "XCLI")
        self.draw_tabs(ui, 0, w)
        ui.region(0, w)
        wifi_txt = ""
        wifi_pair = LINK
        if wifi:
            sig = wifi.get("signal")
            wifi_txt = "wifi %s%s" % (
                wifi.get("ssid") or "",
                (" %sdBm" % sig) if sig is not None else "",
            )
            if sig is not None and sig <= -75:
                wifi_pair = WARN
        rtts = self.host.get("rtts") or []
        rtt_txt = ""
        rtt_pair = DIM
        if rtt is not None:
            rtt_txt = "rtt %sms" % fmt_ms(rtt)
            if rtts:
                rtt_txt += " %s" % spark(rtts)
            if rtt_avg is not None:
                rtt_txt += " avg %s" % fmt_ms(rtt_avg)
            rtt_pair = OK if rtt < 40 else (WARN if rtt < 80 else BAD)
        ui.put(
            1,
            2,
            "%s   %s   %s   cpu %s"
            % (
                clock.get("clock") or time.strftime("%H:%M:%S"),
                clock.get("zone") or "none",
                self.host.get("host") or "none",
                self.host.get("governor") or "none",
            ),
            TITLE,
            curses.A_BOLD,
        )
        load_txt = "load %.2f/%s  mem %s %.0f%%  disk %s %.0f%%" % (
            load[0],
            self.host.get("nproc") or 1,
            bar(mem.get("pct"), 8),
            mem.get("pct") or 0,
            bar(root.get("pct"), 8),
            root.get("pct") or 0,
        )
        ui.put(2, 2, load_txt, OK if (self.ros.get("ok") and (self.ros.get("topics") or [])) else DIM)
        x = 2 + len(load_txt) + 3
        if wifi_txt:
            ui.put(2, x, wifi_txt, wifi_pair, curses.A_BOLD)
            x += len(wifi_txt) + 3
        if rtt_txt:
            ui.put(2, x, rtt_txt, rtt_pair)
            x += len(rtt_txt) + 3
        n_topics = len(self.ros.get("topics") or [])
        if self.ros.get("ok") and n_topics:
            ui.put(2, x, "topics %d" % n_topics, OK)

        y0 = header_h
        self.draw_iface(ui, y0, 0, top_h, c1)
        self.draw_wifi(ui, y0, c1, top_h, c2)
        self.draw_cpu(ui, y0, c1 + c2, top_h, c3)

        y1 = header_h + top_h
        self.draw_disk(ui, y1, 0, mid_h, c1)
        self.draw_tmux(ui, y1, c1, mid_h, c2)
        self.draw_mem(ui, y1, c1 + c2, mid_h, c3)

        self.draw_ros(ui, header_h + top_h + mid_h, 0, ros_h, w)

        fy = h - footer_h
        ui.unregion()
        ui.box(fy, 0, footer_h, w, "COMMANDS", self.pane() == "cmd")
        ui.region(0, w)
        assess = self.assess()
        ui.put(fy + 1, 2, " | ".join(assess), WARN if assess != ["nominal"] else OK)
        x = 2
        row = fy + 2
        for i, (_key, label) in enumerate(CMDS):
            mark = "[%s]" % label
            if x + len(mark) >= w - 2:
                row += 1
                x = 2
            if row >= fy + footer_h - 1:
                break
            pair = HL if self.pane() == "cmd" and self.sel.get("cmd", 0) == i else ACT
            ui.put(row, x, mark, pair)
            self.hits.append((row, "cmd", i, None, x, x + len(mark)))
            x += len(mark) + 2
        hint = self.flash or "/ filter   click a command"
        ui.put(fy + footer_h - 2, max(2, w - len(hint) - 2), hint, DIM)
        self.geom["cmd"] = {"y": fy, "x": 0, "h": footer_h, "w": w, "view": len(CMDS)}

    def draw_iface(self, ui, y, x, h, w):
        view = self._pane_box(ui, "iface", y, x, h, w, "IFACE")
        self._draw_cols(ui, "iface", y + 1, x)
        rows = self.rows_of("iface")
        body = max(1, view - 1)
        if not rows:
            ui.put(y + 2, x + 2, "no match" if self.query.get("iface") else "none", DIM)
            return
        scroll = self.scroll["iface"]
        for i, nic in enumerate(rows[scroll:scroll + body]):
            idx = scroll + i
            line = "%-6s %-14s %-8s %-8s" % (
                nic.get("name") or "?",
                nic.get("ipv4") or "none",
                fmt_rate(nic.get("rx_bps")),
                fmt_rate(nic.get("tx_bps")),
            )
            pair = HL if self.pane() == "iface" and idx == self.sel["iface"] else 0
            ui.put(y + 2 + i, x + 2, line, pair)
            self.hits.append((y + 2 + i, "iface", idx, None, x, x + w))
        ui.thumb(y + 2, x + w - 2, body, len(rows), scroll, body)

    def draw_wifi(self, ui, y, x, h, w):
        view = self._pane_box(ui, "wifi", y, x, h, w, "WIFI")
        self._draw_cols(ui, "wifi", y + 1, x)
        rows = self.rows_of("wifi")
        body = max(1, view - 1)
        if not rows:
            ui.put(y + 2, x + 2, "no match" if self.query.get("wifi") else "none", DIM)
            return
        scroll = self.scroll["wifi"]
        for i, ap in enumerate(rows[scroll:scroll + body]):
            idx = scroll + i
            sig = ap.get("signal")
            mark = "*" if ap.get("in_use") else " "
            line = "%s%-16s %4s  %-8s" % (
                mark,
                (ap.get("ssid") or "?")[:16],
                ("%d" % sig) if sig is not None else "none",
                (ap.get("security") or "open").split()[0][:8],
            )
            pair = 0
            extra = 0
            if self.pane() == "wifi" and idx == self.sel["wifi"]:
                pair = HL
            elif ap.get("in_use"):
                pair = LINK
                extra = curses.A_BOLD
            elif sig is not None and sig <= -80:
                pair = DIM
            ui.put(y + 2 + i, x + 2, line, pair, extra)
            ax = x + w - 8
            if ax > x + 8 and not ap.get("in_use"):
                ui.put(y + 2 + i, ax, "join", ACT if pair != HL else HL)
                self.hits.append((y + 2 + i, "wifi", idx, "join", ax, ax + 4))
            self.hits.append((y + 2 + i, "wifi", idx, None, x, x + w))
        ui.thumb(y + 2, x + w - 2, body, len(rows), scroll, body)

    def draw_cpu(self, ui, y, x, h, w):
        view = self._pane_box(ui, "cpu", y, x, h, w, "CPU")
        self._draw_cols(ui, "cpu", y + 1, x)
        rows = self.rows_of("cpu")
        body = max(1, view - 1)
        if not rows:
            ui.put(y + 2, x + 2, "no match" if self.query.get("cpu") else "none", DIM)
            return
        scroll = self.scroll["cpu"]
        for i, p in enumerate(rows[scroll:scroll + body]):
            idx = scroll + i
            line = "%5.1f  %-6s  %s" % (
                float(p.get("cpu") or 0),
                p.get("pid") or "?",
                p.get("name") or "?",
            )
            pair = HL if self.pane() == "cpu" and idx == self.sel["cpu"] else 0
            ui.put(y + 2 + i, x + 2, line, pair)
            self.hits.append((y + 2 + i, "cpu", idx, None, x, x + w))
        ui.thumb(y + 2, x + w - 2, body, len(rows), scroll, body)

    def draw_mem(self, ui, y, x, h, w):
        view = self._pane_box(ui, "mem", y, x, h, w, "MEM")
        self._draw_cols(ui, "mem", y + 1, x)
        rows = self.rows_of("mem")
        body = max(1, view - 1)
        if not rows:
            ui.put(y + 2, x + 2, "no match" if self.query.get("mem") else "none", DIM)
            return
        scroll = self.scroll["mem"]
        for i, p in enumerate(rows[scroll:scroll + body]):
            idx = scroll + i
            line = "%-7s %-6s  %s" % (
                fmt_bytes(p.get("rss") or 0),
                p.get("pid") or "?",
                p.get("name") or "?",
            )
            pair = HL if self.pane() == "mem" and idx == self.sel["mem"] else 0
            ui.put(y + 2 + i, x + 2, line, pair)
            self.hits.append((y + 2 + i, "mem", idx, None, x, x + w))
        ui.thumb(y + 2, x + w - 2, body, len(rows), scroll, body)

    def draw_disk(self, ui, y, x, h, w):
        view = self._pane_box(ui, "disk", y, x, h, w, "DISK")
        self._draw_cols(ui, "disk", y + 1, x)
        rows = self.rows_of("disk")
        bags = self.host.get("bags")
        body = max(1, view - 1)
        if not rows:
            ui.put(y + 2, x + 2, "no match" if self.query.get("disk") else "none", DIM)
            return
        scroll = self.scroll["disk"]
        shown = 0
        for i, p in enumerate(rows[scroll:]):
            if shown >= body:
                break
            idx = scroll + i
            pct = p.get("pct")
            pair = HL if self.pane() == "disk" and idx == self.sel["disk"] else 0
            if pair != HL:
                pair = BAD if (pct or 0) >= 90 else (WARN if (pct or 0) >= 80 else 0)
            line = "%-14s %-7s %5s" % (
                (p.get("path") or "?")[:14],
                fmt_bytes(p.get("used")),
                ("%.0f%%" % pct) if pct is not None else "",
            )
            ui.put(y + 2 + shown, x + 2, line, pair)
            self.hits.append((y + 2 + shown, "disk", idx, None, x, x + w))
            shown += 1
        if bags and shown < body:
            ui.put(
                y + 2 + shown,
                x + 2,
                "bags %s  %d  %s"
                % (
                    bags.get("path") or "~/.ros",
                    bags.get("bags") or 0,
                    fmt_bytes(bags.get("bytes")),
                ),
                TITLE,
            )
        ui.thumb(y + 2, x + w - 2, body, len(rows), scroll, body)

    def draw_tmux(self, ui, y, x, h, w):
        view = self._pane_box(ui, "tmux", y, x, h, w, "TMUX")
        self._draw_cols(ui, "tmux", y + 1, x)
        rows = self.rows_of("tmux")
        body = max(1, view - 1)
        if not rows:
            ui.put(y + 2, x + 2, "no match" if self.query.get("tmux") else "none", DIM)
            return
        scroll = self.scroll["tmux"]
        for i, sess in enumerate(rows[scroll:scroll + body]):
            idx = scroll + i
            mark = "*" if sess.get("attached") else " "
            line = "%s%-14s  %3s  %-3s" % (
                mark,
                (sess.get("name") or "?")[:14],
                sess.get("windows") or 0,
                "yes" if sess.get("attached") else "no",
            )
            pair = HL if self.pane() == "tmux" and idx == self.sel["tmux"] else (
                LINK if sess.get("attached") else 0
            )
            ui.put(y + 2 + i, x + 2, line, pair)
            self.hits.append((y + 2 + i, "tmux", idx, "attach", x, x + w))
        ui.thumb(y + 2, x + w - 2, body, len(rows), scroll, body)

    def draw_ros(self, ui, y, x, h, w):
        view = self._pane_box(ui, "ros", y, x, h, w, "ROS")
        topics = self.rows_of("ros")
        self._draw_cols(ui, "ros", y + 1, x)
        ui.put(y + 1, x + 2 + 24 + 1 + 7 + 1 + 10 + 1 + 5 + 1 + 5 + 1, "note", DIM)
        if not topics:
            ui.put(y + 2, x + 2, "no match" if self.query.get("ros") else "none", DIM)
            return
        body = max(1, view - 1)
        scroll = self.scroll["ros"]
        warmup = self.warmup()
        for i, t in enumerate(topics[scroll:scroll + body]):
            idx = scroll + i
            note = topic_note(t, warmup)
            pair = HL if self.pane() == "ros" and idx == self.sel["ros"] else note_pair(note)
            ui.put(
                y + 2 + i,
                x + 2,
                "%-24s %7s %10s %5s %5s  %s"
                % (
                    (t.get("name") or "?")[:24],
                    "%.1f" % t["hz"] if t.get("hz") is not None else "none",
                    fmt_ms(t.get("std_ms")),
                    t.get("n_pub") if t.get("n_pub") is not None else 0,
                    t.get("n_sub") if t.get("n_sub") is not None else 0,
                    note,
                ),
                pair,
            )
            self.hits.append((y + 2 + i, "ros", idx, "topic", x, x + w))
        ui.thumb(y + 2, x + w - 2, body, len(topics), scroll, body)

    def _fmt_pose(self, tap):
        xyz = (tap or {}).get("xyz")
        rpy = (tap or {}).get("rpy") or (None, None, None)
        if not xyz:
            return "silent"
        return "xyz %6.2f %6.2f %6.2f   rpy %5.1f %5.1f %5.1f" % (
            xyz[0], xyz[1], xyz[2],
            rpy[0] if rpy[0] is not None else 0.0,
            rpy[1] if rpy[1] is not None else 0.0,
            rpy[2] if rpy[2] is not None else 0.0,
        )

    def draw_mav(self, ui):
        h, w = ui.size()
        mav = self.mav or {}
        ui.unregion()
        ui.box(0, 0, 4, w, "XCLI  MAVROS")
        self.draw_tabs(ui, 0, w)
        ui.region(0, w)
        ns = mav.get("ns") if mav.get("ns") is not None else ""
        nss = mav.get("nss") or []
        st = mav.get("state") or {}
        ui.put(
            1, 2,
            "ns %s   %s   %s"
            % (
                ns or "(none)",
                mav.get("fw") or "PX4 ?",
                mav.get("backend") or "none",
            ),
            TITLE,
        )
        mode = st.get("mode") or "-"
        landed = st.get("landed") or "-"
        ui.put(
            2, 2,
            "FCU %s  %s  %s  %s  RC %s  %s"
            % (
                "up" if st.get("connected") else "down",
                "ARMED" if st.get("armed") else "disarmed",
                mode,
                landed,
                "yes" if st.get("rc") else "no",
                "offboard-ok" if st.get("guided") else "",
            ),
            OK if st.get("connected") else WARN,
        )

        fly = mav.get("fly") or ""
        why = mav.get("why") or ""
        fused = mav.get("fused")
        gap = mav.get("gap_cm")
        yawg = mav.get("yaw_gap_deg")
        ui.unregion()
        ui.box(4, 0, 7, w, "DIAGNOSIS")
        ui.region(0, w)
        gap_s = ("%.0f cm" % gap) if gap is not None else "n/a"
        yaw_s = ("%.1f deg" % yawg) if yawg is not None else "n/a"
        fuse_s = "FUSED" if fused else "NOT fused"
        ui.put(5, 2, "fusion  %s   |local-vision| %s   yaw gap %s" % (fuse_s, gap_s, yaw_s),
               OK if fused else BAD)
        ui.put(6, 2, "reason  %s" % (why or "none"), WARN if why else DIM)
        ui.put(7, 2, "takeoff %s" % (fly or "no blocker seen"), BAD if fly else OK)
        vrpn, vis, loc, sp = mav.get("vrpn") or {}, mav.get("vision") or {}, mav.get("local") or {}, mav.get("setpoint") or {}
        ui.put(8, 2, "VRPN     %4.1f Hz  %s" % (vrpn.get("hz") or 0.0, self._fmt_pose(vrpn)),
               OK if vrpn.get("live") else WARN)
        ui.put(9, 2, "vision   %4.1f Hz  %s" % (vis.get("hz") or 0.0, self._fmt_pose(vis)),
               OK if vis.get("live") else WARN)

        mid = 11
        ui.unregion()
        left_w = max(40, w * 3 // 5)
        ui.box(mid, 0, h - mid - 4, left_w, "POSES / SETPOINT")
        ui.region(0, left_w)
        ui.put(mid + 1, 2, "local    %4.1f Hz  %s" % (loc.get("hz") or 0.0, self._fmt_pose(loc)),
               OK if loc.get("live") else WARN)
        used = (sp.get("extra") or {}).get("used") or []
        ui.put(
            mid + 2, 2,
            "setpoint %4.1f Hz  uses %s"
            % (sp.get("hz") or 0.0, ",".join(used) if used else "none"),
            OK if sp.get("live") else WARN,
        )
        if sp.get("xyz"):
            ui.put(mid + 3, 2, "         cmd  %s" % self._fmt_pose(sp), DIM)
        if not nss:
            ui.put(mid + 5, 2, "No /mavros/* on this master. Page still works.", DIM)
            ui.put(mid + 6, 2, "Start MAVROS or pick another ROS_MASTER.", DIM)

        ui.unregion()
        ui.box(mid, left_w, h - mid - 4, w - left_w, "PX4 PARAMS")
        ui.region(left_w, w - left_w)
        params = mav.get("params") or []
        ui.put(mid + 1, left_w + 2, "click a row to edit (this firmware only)", DIM)
        body = max(1, h - mid - 7)
        for i, p in enumerate(params[:body]):
            val = p.get("value")
            if isinstance(val, float) and abs(val - int(val)) < 1e-6:
                vs = str(int(val))
            else:
                vs = ("%.3f" % val) if isinstance(val, float) else ("%s" % (val if val is not None else "-"))
            pair = HL if self.sel.get("mavp", 0) == i else 0
            ui.put(
                mid + 2 + i, left_w + 2,
                "%-16s %-8s %s" % ((p.get("label") or "")[:16], vs, (p.get("note") or "")[:18]),
                pair if pair else (WARN if p.get("note") and "OFF" in str(p.get("note")) else 0),
            )
            self.hits.append((mid + 2 + i, "mavp", i, "edit", left_w, w))

        fy = h - 4
        ui.unregion()
        ui.box(fy, 0, 4, w, "COMMANDS")
        ui.region(0, w)
        acts = (
            ("mav-set", "Set param"),
            ("mav-ns", "Namespace"),
            ("reboot-fcu", "Reboot FCU"),
            ("reboot-pc", "Reboot PC"),
            ("host", "Host page"),
        )
        x = 2
        for i, (key, label) in enumerate(acts):
            mark = "[%s]" % label
            ui.put(fy + 1, x, mark, ACT)
            self.hits.append((fy + 1, "mavcmd", i, key, x, x + len(mark)))
            x += len(mark) + 2
        ui.put(fy + 2, 2, self.flash or "1 Host  2 MAVROS  Enter edit  q quit", DIM)

    def draw_menu(self, ui):
        h, w = ui.size()
        ui.unregion()
        ui.box(0, 0, h, w, "SETTINGS")
        ui.region(0, w)
        hint = self.menu_hint or {}
        ui.put(
            1,
            2,
            "zone %s   wifi %s   cpu %s   sleep %s   gw %s"
            % (
                hint.get("zone") or "-",
                hint.get("wifi") or "-",
                hint.get("governor") or "-",
                hint.get("sleep") or "-",
                hint.get("gw") or "-",
            ),
            DIM,
        )
        ui.put(3, 2, "Fold a section with Enter. Toggle or cycle a row. Esc home.", DIM)
        rows = self.settings_rows()
        for i, row in enumerate(rows):
            pair = HL if i == self.menu_sel else BASE
            if row["kind"] == "sec":
                mark = "v" if row["sid"] in self.settings_open else ">"
                ui.put(5 + i, 2, "%s %s" % (mark, row["title"]), pair, curses.A_BOLD)
            else:
                val = self.setting_value(row.get("iid"))
                typ = row.get("typ")
                hint_r = "< >" if typ == "enum" else ("on/off" if typ == "toggle" else "run")
                ui.put(
                    5 + i,
                    4,
                    "%-20s  %-18s  %s" % (row["title"], val, hint_r),
                    pair,
                )
            self.hits.append((5 + i, "menu", i, None, 0, w))
        ui.put(h - 2, 2, self.flash or "j/k  Enter  Esc", DIM)

    def _dlg_hit(self, y, action, idx, x0, x1):
        self.dlg_hits.append((y, action, idx, x0, x1))

    def _draw_btn(self, ui, y, x, label, focused):
        text = "[ %s ]" % label
        pair = HL if focused else CARD_BTN
        extra = curses.A_BOLD
        if not ui.color:
            extra |= curses.A_REVERSE if focused else 0
        ui.put(y, x, text, pair, extra)
        return x + len(text)

    def draw_dialog(self, ui):
        h, w = ui.size()
        dialog = self.dialog or {}
        kind = dialog.get("kind")
        ui.unregion()
        if kind == "pick":
            items = dialog.get("items") or []
            mh = min(h - 4, max(9, len(items) + 6))
            mw = min(w - 4, 62)
            y, x = max(1, (h - mh) // 2), max(1, (w - mw) // 2)
            ui.card(y, x, mh, mw, dialog.get("title") or "Select")
            ui.region(x, mw)
            view = max(1, mh - 5)
            sel = dialog.get("sel") or 0
            top = 0
            if sel >= view:
                top = sel - view + 1
            for i, item in enumerate(items[top:top + view]):
                idx = top + i
                pair = HL if idx == sel else CARD
                ui.put(y + 2 + i, x + 2, "%s %s" % (">" if idx == sel else " ", item.get("label") or ""), pair)
                self._dlg_hit(y + 2 + i, "pick", idx, x + 1, x + mw - 2)
            by = y + mh - 2
            end = self._draw_btn(ui, by, x + 2, "Choose  Enter", True)
            self._dlg_hit(by, "choose", 0, x + 2, end)
            end2 = self._draw_btn(ui, by, end + 2, "Cancel  Esc", False)
            self._dlg_hit(by, "cancel", 0, end + 2, end2)
            return
        if kind == "form":
            fields = dialog.get("fields") or []
            mh, mw = 9 + len(fields), min(w - 4, 56)
            y, x = max(1, (h - mh) // 2), max(1, (w - mw) // 2)
            ui.card(y, x, mh, mw, dialog.get("title") or "Input")
            ui.region(x, mw)
            for i, field in enumerate(fields):
                value = field.get("value") or ""
                shown = ("*" * len(value)) if field.get("secret") else value
                pair = HL if i == (dialog.get("cur") or 0) else CARD
                ui.put(
                    y + 2 + i,
                    x + 2,
                    "%-10s %s" % (field.get("label") or field.get("name"), shown or "_"),
                    pair,
                )
                self._dlg_hit(y + 2 + i, "field", i, x + 1, x + mw - 2)
            by = y + mh - 2
            end = self._draw_btn(ui, by, x + 2, "Connect  Enter", True)
            self._dlg_hit(by, "submit", 0, x + 2, end)
            end2 = self._draw_btn(ui, by, end + 2, "Cancel  Esc", False)
            self._dlg_hit(by, "cancel", 0, end + 2, end2)
            return
        lines = dialog.get("lines") or [dialog.get("body") or ""]
        mh = min(h - 2, max(8, len(lines) + 6))
        mw = min(w - 4, max(48, min(76, max(len(s) for s in lines) + 6)))
        y, x = max(1, (h - mh) // 2), max(1, (w - mw) // 2)
        ui.card(y, x, mh, mw, dialog.get("title") or "Confirm")
        ui.region(x, mw)
        for i, line in enumerate(lines[: mh - 5]):
            ui.put(y + 2 + i, x + 2, line, CARD)
        by = y + mh - 2
        if kind == "info":
            end = self._draw_btn(ui, by, x + 2, "Close  Esc", True)
            self._dlg_hit(by, "close", 0, x + 2, end)
            return
        focus = dialog.get("btn") or 0
        end = self._draw_btn(ui, by, x + 2, "Confirm  y", focus == 0)
        self._dlg_hit(by, "yes", 0, x + 2, end)
        end2 = self._draw_btn(ui, by, end + 2, "Cancel  n", focus == 1)
        self._dlg_hit(by, "no", 0, end + 2, end2)

    def key(self, ch):
        if ch == curses.KEY_RESIZE:
            return "resize"
        if ch == curses.KEY_MOUSE:
            return self.mouse()
        if self.dialog:
            return self.key_dialog(ch)
        if self.page == "menu":
            return self.key_menu(ch)
        if self.searching:
            name = self.pane()
            if ch == 27:
                self.query[name] = ""
                self.searching = False
                return None
            if ch in (10, 13):
                self.searching = False
                return None
            if ch in (curses.KEY_BACKSPACE, 127, 8):
                self.query[name] = (self.query.get(name) or "")[:-1]
                self.sel[name] = 0
                return None
            if 32 <= ch <= 126:
                self.query[name] = (self.query.get(name) or "") + chr(ch)
                self.sel[name] = 0
                return None
        if ch in (ord("1"), ord("[")):
            self.set_view("host")
            return None
        if ch in (ord("2"), ord("]")):
            self.set_view("mav")
            return None
        if self.view == "mav":
            if ch in (curses.KEY_DOWN, ord("j")):
                n = len((self.mav or {}).get("params") or [])
                if n:
                    self.sel["mavp"] = min(n - 1, self.sel.get("mavp", 0) + 1)
                return None
            if ch in (curses.KEY_UP, ord("k")):
                self.sel["mavp"] = max(0, self.sel.get("mavp", 0) - 1)
                return None
            if ch in (curses.KEY_ENTER, 10, 13):
                self.mav_edit_param()
                return None
        if ch == ord("/"):
            self.searching = True
            return None
        if ch in (ord("s"),):
            cols = PANE_COLS.get(self.pane()) or ()
            keys = [c[0] for c in cols]
            if keys:
                cur = self.sort_key.get(self.pane())
                if cur in keys:
                    nxt = keys[(keys.index(cur) + 1) % len(keys)]
                else:
                    nxt = keys[0]
                self.toggle_sort(self.pane(), nxt)
            return None
        if ch in (ord("q"), ord("Q")):
            return "quit"
        if ch == 27:
            return None
        if ch in (ord("?"),):
            self.open_help()
        elif ch in (ord("m"), ord("M")):
            self.open_menu()
        elif ch in (9,):
            self.focus = (self.focus + 1) % len(PANELS)
        elif ch in (KEY_BTAB,):
            self.focus = (self.focus - 1) % len(PANELS)
        elif ch in (curses.KEY_DOWN, ord("j")):
            self.move(1)
        elif ch in (curses.KEY_UP, ord("k")):
            self.move(-1)
        elif ch in (curses.KEY_NPAGE,):
            self.move(5)
        elif ch in (curses.KEY_PPAGE,):
            self.move(-5)
        elif ch in (curses.KEY_LEFT, ord("h")):
            if self.pane() == "cmd":
                self.move(-1)
            else:
                self.focus = (self.focus - 1) % len(PANELS)
        elif ch in (curses.KEY_RIGHT, ord("l")):
            if self.pane() == "cmd":
                self.move(1)
            else:
                self.focus = (self.focus + 1) % len(PANELS)
        elif ch in (curses.KEY_ENTER, 10, 13):
            self.activate()
        elif ch in (ord("K"),):
            self.ask_kill()
        elif ch in (ord("p"), ord("P")):
            self.ask_ping()
        if self._quit_tmux:
            return "tmux"
        return None

    def key_menu(self, ch):
        rows = self.settings_rows()
        n = len(rows) or 1
        if ch in (ord("q"), ord("Q")):
            return "quit"
        if ch == 27:
            self.page = "home"
            return None
        if ch in (curses.KEY_DOWN, ord("j")):
            self.menu_sel = (self.menu_sel + 1) % n
        elif ch in (curses.KEY_UP, ord("k")):
            self.menu_sel = (self.menu_sel - 1) % n
        elif ch in (curses.KEY_ENTER, 10, 13, curses.KEY_LEFT, curses.KEY_RIGHT, ord("h"), ord("l")):
            self.menu_activate()
        elif ch in (ord("?"),):
            self.open_help()
        return None

    def key_dialog(self, ch):
        dialog = self.dialog or {}
        kind = dialog.get("kind")
        if kind == "pick":
            items = dialog.get("items") or []
            n = len(items)
            if ch in (27, ord("q")):
                self.dialog = None
                self.flash = "cancelled"
            elif ch in (curses.KEY_DOWN, ord("j")) and n:
                dialog["sel"] = min(n - 1, (dialog.get("sel") or 0) + 1)
            elif ch in (curses.KEY_UP, ord("k")) and n:
                dialog["sel"] = max(0, (dialog.get("sel") or 0) - 1)
            elif ch in (curses.KEY_ENTER, 10, 13):
                self.pick_submit()
            return None
        if kind == "form":
            fields = dialog.get("fields") or []
            cur = dialog.get("cur") or 0
            if ch in (27,):
                self.dialog = None
                self.flash = "cancelled"
            elif ch in (9, curses.KEY_DOWN):
                if fields:
                    dialog["cur"] = (cur + 1) % len(fields)
            elif ch in (KEY_BTAB, curses.KEY_UP):
                if fields:
                    dialog["cur"] = (cur - 1) % len(fields)
            elif ch in (curses.KEY_ENTER, 10, 13):
                self.submit_form()
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                if fields and 0 <= cur < len(fields):
                    fields[cur]["value"] = (fields[cur].get("value") or "")[:-1]
            elif 32 <= ch <= 126:
                if fields and 0 <= cur < len(fields):
                    fields[cur]["value"] = (fields[cur].get("value") or "") + chr(ch)
            return None
        if kind == "info":
            if ch in (27, 10, 13, ord("q"), ord(" "), ord("y"), ord("n")):
                self.dialog = None
            return None
        if ch in (9, curses.KEY_LEFT, curses.KEY_RIGHT, ord("h"), ord("l")):
            dialog["btn"] = 1 - (dialog.get("btn") or 0)
            return None
        if ch in (curses.KEY_ENTER, 10, 13):
            self.apply_confirm((dialog.get("btn") or 0) == 0)
        elif ch in (ord("y"), ord("Y")):
            self.apply_confirm(True)
        elif ch in (ord("n"), ord("N"), 27):
            self.apply_confirm(False)
        if self._quit_tmux:
            return "tmux"
        return None

    def _mouse_clicked(self, bstate):
        released = getattr(curses, "BUTTON1_RELEASED", 0)
        return bool(
            (bstate & curses.BUTTON1_CLICKED)
            or (bstate & curses.BUTTON1_DOUBLE_CLICKED)
            or (released and (bstate & released))
            or (bstate & curses.BUTTON1_PRESSED)
        )

    def mouse_dialog(self, mx, my, bstate):
        now = time.monotonic()
        if getattr(self, "_mouse_guard", 0) and now - self._mouse_guard < 0.18:
            return None
        if not self._mouse_clicked(bstate):
            return None
        hit = None
        for y, action, idx, x0, x1 in self.dlg_hits:
            if y != my or mx < x0 or mx > x1:
                continue
            hit = (action, idx)
            if action in ("yes", "no", "choose", "cancel", "submit", "close"):
                break
        if not hit:
            return None
        self._mouse_guard = now
        action, idx = hit
        dialog = self.dialog or {}
        kind = dialog.get("kind")
        if action == "yes":
            self.apply_confirm(True)
        elif action == "no":
            self.apply_confirm(False)
        elif action == "cancel":
            self.dialog = None
            self.flash = "cancelled"
        elif action == "close":
            self.dialog = None
        elif action == "choose":
            self.pick_submit()
        elif action == "submit":
            self.submit_form()
        elif action == "field" and kind == "form":
            dialog["cur"] = idx
        elif action == "pick" and kind == "pick":
            prev = dialog.get("sel")
            dialog["sel"] = idx
            if prev == idx:
                self.pick_submit()
        if self._quit_tmux:
            return "tmux"
        return None

    def mouse(self):
        try:
            _id, mx, my, _z, bstate = curses.getmouse()
        except curses.error:
            return None
        wheel_up = BUTTON4 and (bstate & BUTTON4)
        wheel_down = BUTTON5 and (bstate & BUTTON5)
        if wheel_up or wheel_down:
            if self.dialog and self.dialog.get("kind") == "pick":
                items = self.dialog.get("items") or []
                if items:
                    delta = -1 if wheel_up else 1
                    self.dialog["sel"] = max(
                        0, min(len(items) - 1, (self.dialog.get("sel") or 0) + delta)
                    )
            elif not self.dialog:
                self.move(-1 if wheel_up else 1)
            return None
        if self.dialog:
            return self.mouse_dialog(mx, my, bstate)
        if not self._mouse_clicked(bstate):
            return None
        now = time.monotonic()
        if getattr(self, "_mouse_guard", 0) and now - self._mouse_guard < 0.18:
            return None
        hit = None
        for y, pane, idx, action, x0, x1 in self.hits:
            if y != my:
                continue
            if mx < x0 or mx > x1:
                continue
            hit = (pane, idx, action)
            if action:
                break
        if not hit:
            return None
        self._mouse_guard = now
        pane, idx, action = hit
        if pane == "tab":
            self.set_view(action)
            return None
        if pane == "mavp":
            self.sel["mavp"] = idx
            if action == "edit":
                self.mav_edit_param()
            return None
        if pane == "mavcmd":
            self.mav_cmd(action)
            return None
        if pane == "sort":
            self.toggle_sort(action, idx)
            return None
        if pane == "ros_sort":
            self.toggle_sort("ros", idx)
            return None
        if pane == "menu":
            if self.menu_sel == idx:
                self.menu_activate()
            else:
                self.menu_sel = idx
            return None
        if pane in PANELS:
            self.focus = PANELS.index(pane)
            self.sel[pane] = idx
            self.clamp_pane(pane, self.geom.get(pane, {}).get("view", 4))
        now = time.monotonic()
        dbl = False
        if self._click and self._click[0] == pane and self._click[1] == idx and now - self._click[2] < 0.45:
            dbl = True
        self._click = (pane, idx, now)
        if action == "join" or (pane == "wifi" and (dbl or action == "join")):
            self.ask_wifi()
            return None
        if pane == "ros" and (dbl or action == "topic"):
            if dbl or bstate & curses.BUTTON1_DOUBLE_CLICKED:
                self.show_topic()
            return None
        if pane == "tmux" and (dbl or action == "attach"):
            if dbl or bstate & curses.BUTTON1_DOUBLE_CLICKED:
                self.ask_tmux()
            return None
        if pane == "cmd":
            self.run_cmd(CMDS[idx][0])
            return None
        if dbl or bstate & curses.BUTTON1_DOUBLE_CLICKED:
            self.activate()
        if self._quit_tmux:
            return "tmux"
        return None


def loop(stdscr, app):
    curses.curs_set(0)
    try:
        stdscr.nodelay(True)
    except curses.error:
        pass
    stdscr.timeout(200)
    try:
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(120)
    except curses.error:
        pass
    sync_term_size(stdscr)
    ui = Pen(stdscr)
    last_data = 0.0
    started = time.monotonic()
    try:
        app.refresh()
    except Exception:
        pass
    try:
        app.draw(ui)
    except Exception:
        pass
    while True:
        try:
            ch = stdscr.getch()
        except curses.error:
            ch = -1
        now = time.monotonic()
        need_draw = False
        if ch == curses.KEY_RESIZE or (now - started < 2.5 and sync_term_size(stdscr)):
            need_draw = True
            ch = -1 if ch == curses.KEY_RESIZE else ch
        if ch != -1:
            try:
                result = app.key(ch)
                if result == "quit":
                    break
                if result == "tmux":
                    return "tmux"
                if result == "resize":
                    sync_term_size(stdscr)
                need_draw = True
            except Exception:
                app.flash = "input none"
                need_draw = True
        interval = 1.0 if (now - started) < 3.0 else 2.0
        if not last_data or now - last_data >= interval:
            try:
                app.refresh()
            except Exception:
                pass
            last_data = now
            need_draw = True
        if need_draw or getattr(app, "_dirty", False):
            try:
                app.draw(ui)
            except Exception:
                try:
                    stdscr.erase()
                    stdscr.addnstr(0, 0, "xcli eval  none  q quit", 40)
                    stdscr.refresh()
                except curses.error:
                    pass
    return None


def print_snapshot(host, ros):
    host = host or empty_host()
    ros = ros or empty_ros()
    load = host.get("load") or (0.0, 0.0, 0.0)
    mem = host.get("mem") or {}
    root = host.get("root") or {}
    route = host.get("route") or {}
    clock = host.get("clock") or {}
    lines = [
        "host=%s" % (host.get("host") or "none"),
        "clock=%s zone=%s" % (clock.get("clock") or "none", clock.get("zone") or "none"),
        "nproc=%s" % (host.get("nproc") or "none"),
        "load=%.2f %.2f %.2f" % (load[0], load[1], load[2]),
        "governor=%s" % (host.get("governor") or "none"),
        "mem_used=%s mem_total=%s mem_pct=%s"
        % (
            fmt_bytes(mem.get("used")),
            fmt_bytes(mem.get("total")),
            ("%.1f" % mem["pct"]) if mem.get("pct") is not None else "none",
        ),
        "disk_root_used=%s disk_root_free=%s disk_root_pct=%s"
        % (
            fmt_bytes(root.get("used")),
            fmt_bytes(root.get("free")),
            ("%.1f" % root["pct"]) if root.get("pct") is not None else "none",
        ),
        "gw=%s gw_iface=%s rtt_ms=%s rtt_avg_ms=%s"
        % (
            route.get("gw") or "none",
            route.get("iface") or "none",
            fmt_ms(host.get("rtt_ms")) if host.get("rtt_ms") is not None else "none",
            fmt_ms(host.get("rtt_avg_ms")) if host.get("rtt_avg_ms") is not None else "none",
        ),
        "ros=%s distro=%s"
        % ("ok" if ros.get("ok") else "none", (ros.get("distro") or "none") if ros.get("ok") else "none"),
    ]
    for nic in host.get("nics") or []:
        wifi = nic.get("wifi") or {}
        lines.append(
            "iface name=%s ipv4=%s rx=%s tx=%s wifi=%s signal=%s"
            % (
                nic.get("name") or "?",
                nic.get("ipv4") or "none",
                fmt_rate(nic.get("rx_bps")),
                fmt_rate(nic.get("tx_bps")),
                wifi.get("ssid") or "none",
                wifi.get("signal") if wifi.get("signal") is not None else "none",
            )
        )
    for ap in host.get("wifi_aps") or []:
        lines.append(
            "wifi ssid=%s signal=%s security=%s in_use=%s"
            % (
                ap.get("ssid") or "?",
                ap.get("signal") if ap.get("signal") is not None else "none",
                ap.get("security") or "open",
                "yes" if ap.get("in_use") else "no",
            )
        )
    for p in host.get("paths") or []:
        lines.append(
            "disk path=%s used=%s free=%s pct=%s"
            % (
                p.get("path") or "?",
                fmt_bytes(p.get("used")),
                fmt_bytes(p.get("free")),
                ("%.1f" % p["pct"]) if p.get("pct") is not None else "none",
            )
        )
    bags = host.get("bags") or {}
    if bags:
        lines.append(
            "bags path=%s count=%s bytes=%s"
            % (bags.get("path"), bags.get("bags"), fmt_bytes(bags.get("bytes")))
        )
    for p in host.get("cpu_top") or []:
        lines.append("cpu pct=%.1f pid=%s name=%s" % (float(p.get("cpu") or 0), p.get("pid") or "?", p.get("name") or "?"))
    for p in host.get("mem_top") or []:
        lines.append("mem rss=%s pid=%s name=%s" % (fmt_bytes(p.get("rss") or 0), p.get("pid") or "?", p.get("name") or "?"))
    for sess in host.get("tmux") or []:
        lines.append(
            "tmux name=%s windows=%s attached=%s"
            % (sess.get("name"), sess.get("windows"), "yes" if sess.get("attached") else "no")
        )
    for t in ros.get("topics") or []:
        lines.append(
            "topic name=%s hz=%s jitter_ms=%s mean_dt_ms=%s pubs=%s subs=%s note=%s"
            % (
                t.get("name") or "?",
                ("%.2f" % t["hz"]) if t.get("hz") is not None else "none",
                fmt_ms(t.get("std_ms")),
                fmt_ms(t.get("mean_ms")),
                t.get("n_pub") if t.get("n_pub") is not None else 0,
                t.get("n_sub") if t.get("n_sub") is not None else 0,
                topic_note(t, bool(ros.get("warmup"))),
            )
        )
    app = App()
    app.host = host
    app.ros = ros
    lines.append("assess=%s" % "|".join(app.assess()))
    sys.stdout.write("\n".join(lines) + "\n")


def once():
    host = empty_host()
    ros = empty_ros()
    host_s = None
    ros_s = None
    try:
        if host_mod is not None and hasattr(host_mod, "HostSampler"):
            host_s = host_mod.HostSampler()
            host_s.snapshot()
            time.sleep(1.6)
            host = host_s.snapshot() or empty_host()
    except Exception:
        host = empty_host()
    try:
        if ros_mod is not None and hasattr(ros_mod, "RosSampler"):
            ros_s = ros_mod.RosSampler()
            ros_s.start()
            time.sleep(1.0)
            ros = ros_s.snapshot() or empty_ros()
    except Exception:
        ros = empty_ros()
    finally:
        if ros_s is not None:
            try:
                ros_s.stop()
            except Exception:
                pass
        if host_s is not None:
            try:
                host_s.stop()
            except Exception:
                pass
    print_snapshot(host, ros)
    return 0


def attach_samplers(app):
    try:
        if host_mod is not None and hasattr(host_mod, "HostSampler"):
            app.host_s = host_mod.HostSampler()
            try:
                app.host_s.snapshot()
            except Exception:
                pass
    except Exception:
        app.host_s = None
    try:
        if ros_mod is not None and hasattr(ros_mod, "RosSampler"):
            app.ros_s = ros_mod.RosSampler()
            app.ros_s.start()
    except Exception:
        app.ros_s = None


_CLEANED = False
_APP_REF = []


def stop_samplers(app=None):
    global _CLEANED
    if _CLEANED:
        return
    _CLEANED = True
    if app is None and _APP_REF:
        app = _APP_REF[0]
    if app is None:
        return
    if getattr(app, "ros_s", None) is not None:
        try:
            app.ros_s.stop()
        except Exception:
            pass
        app.ros_s = None
    if getattr(app, "mav_s", None) is not None:
        try:
            app.mav_s.stop()
        except Exception:
            pass
        app.mav_s = None
    if getattr(app, "host_s", None) is not None:
        try:
            app.host_s.stop()
        except Exception:
            pass
        app.host_s = None
    try:
        import rospy.core
        atexit.unregister(rospy.core._ros_atexit)
    except Exception:
        pass


def _restore_tty():
    try:
        curses.endwin()
    except Exception:
        pass


def _die(code=0):
    stop_samplers()
    _restore_tty()
    os._exit(code)


def attach_tmux(name):
    name = str(name)
    if not _safe_token(name):
        raise RuntimeError("blocked tmux name")
    try:
        curses.endwin()
    except Exception:
        pass
    if os.environ.get("TMUX"):
        here = ""
        try:
            here = subprocess.check_output(
                ["tmux", "display-message", "-p", "#S"],
                stderr=subprocess.DEVNULL,
                universal_newlines=True,
                timeout=0.4,
            ).strip()
        except Exception:
            here = ""
        if here == name:
            sys.stderr.write("already in tmux session %s\n" % name)
            return "here"
        subprocess.check_call(["tmux", "switch-client", "-t", name])
        return "switched"
    os.execvp("tmux", ["tmux", "attach-session", "-t", name])
    return "attached"


def main():
    args = [a for a in sys.argv[1:] if a]
    if args in (["-h"], ["--help"], ["help"]):
        sys.stdout.write("usage: xcli eval [--once]\n")
        return 0
    if args == ["--once"]:
        return once()
    if args:
        sys.stderr.write("usage: xcli eval [--once]\n")
        return 2
    if not sys.stdout.isatty():
        sys.stderr.write("xcli eval requires a tty (use --once for a snapshot)\n")
        return 1
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    app = App()
    _APP_REF[:] = [app]
    atexit.register(stop_samplers)

    def _on_signal(signum, _frame):
        _die(128 + int(signum))

    try:
        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)
    except Exception:
        pass
    attach_samplers(app)
    result = None
    try:
        result = curses.wrapper(loop, app)
    except KeyboardInterrupt:
        _die(0)
    except Exception as exc:
        sys.stderr.write("xcli eval: display backend failed: %s\n" % exc)
        _die(1)
    stop_samplers(app)
    if result == "tmux" and app._quit_tmux:
        try:
            attach_tmux(app._quit_tmux)
        except Exception as exc:
            sys.stderr.write("tmux attach failed: %s\n" % exc)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
