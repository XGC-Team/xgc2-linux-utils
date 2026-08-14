#!/usr/bin/env python3
"""Read-only host snapshot for xcli eval. Stdlib only."""
from __future__ import print_function

import fcntl
import os
import pwd
import socket
import struct
import subprocess
import threading
import time

SKIP_IFACE_PREFIX = (
    "docker", "br-", "veth", "virbr", "cni", "flannel", "lxc",
    "nfp", "tun", "tap", "wg",
)
SKIP_IFACE_NAMES = frozenset(("mihomo", "meta", "clash"))
SKIP_FS = frozenset((
    "proc", "sysfs", "devtmpfs", "devpts", "cgroup", "cgroup2", "pstore",
    "bpf", "tracefs", "debugfs", "securityfs", "fusectl", "configfs",
    "autofs", "mqueue", "hugetlbfs", "overlay", "squashfs", "nsfs",
    "ramfs", "rpc_pipefs", "binfmt_misc", "efivarfs", "fuse.gvfsd-fuse",
    "fuse.portal",
))
ROS_PATHS = (
    "/", "/tmp", "/var/log", "/var", "/opt/ros", "/data", "/media", "/mnt",
)


def _read(path, default=""):
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read()
    except (OSError, IOError):
        return default


def _read_first(path, default=""):
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.readline().strip()
    except (OSError, IOError):
        return default


def _cmd(args, timeout=0.35):
    try:
        out = subprocess.check_output(
            args, stderr=subprocess.DEVNULL, timeout=timeout,
            universal_newlines=True,
        )
        return out.strip()
    except Exception:
        return ""


def _nproc():
    n = os.cpu_count()
    return n if n and n > 0 else 1


def _mem():
    total = avail = 0
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            total = int(line.split()[1]) * 1024
        elif line.startswith("MemAvailable:"):
            avail = int(line.split()[1]) * 1024
    if total <= 0:
        return {"used": 0, "total": 0, "pct": 0.0}
    if avail <= 0:
        free = buffers = cached = 0
        for line in _read("/proc/meminfo").splitlines():
            if line.startswith("MemFree:"):
                free = int(line.split()[1]) * 1024
            elif line.startswith("Buffers:"):
                buffers = int(line.split()[1]) * 1024
            elif line.startswith("Cached:"):
                cached = int(line.split()[1]) * 1024
        avail = free + buffers + cached
    used = max(0, total - avail)
    return {"used": used, "total": total, "pct": 100.0 * used / total}


def _statvfs(path):
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    if total <= 0:
        return None
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    return {
        "path": path,
        "used": used,
        "total": total,
        "free": st.f_bavail * st.f_frsize,
        "pct": 100.0 * used / total,
        "dev": (st.f_fsid, st.f_blocks),
    }


def _short_path(path):
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def disk_hotspots(limit=6):
    """ROS-relevant mount paths, unique filesystems, fattest first."""
    mounts = []
    seen_mp = set()
    for line in _read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mp, fstype = parts[1], parts[2]
        if fstype in SKIP_FS:
            continue
        if mp.startswith("/snap") or "/docker/" in mp:
            continue
        if mp in seen_mp:
            continue
        seen_mp.add(mp)
        mounts.append(mp)

    home = os.path.expanduser("~")
    ros_home = os.path.join(home, ".ros")
    wanted = list(ROS_PATHS) + [home, ros_home]
    for mp in mounts:
        base = os.path.basename(mp).lower()
        if any(k in mp.lower() or k in base for k in (
            "ros", "bag", "log", "data", "map",
        )):
            wanted.append(mp)

    candidates = []
    seen_dev = set()
    for path in wanted:
        if not path or not os.path.exists(path):
            continue
        info = _statvfs(path)
        if not info:
            continue
        if info["dev"] in seen_dev:
            continue
        seen_dev.add(info["dev"])
        info["path"] = _short_path(path)
        candidates.append(info)

    root = _statvfs("/")
    if root and not any(c["path"] == "/" for c in candidates):
        root["path"] = "/"
        candidates.append(root)

    candidates.sort(key=lambda x: (-x["pct"], x["path"]))
    return candidates[:limit]


def disk_usage_once(limit=12):
    """One-shot directory sizes for common robot paths. Not a live walk."""
    root = _statvfs("/") or {}
    total = float(root.get("total") or 0)
    rows = []
    seen = set()
    if root:
        rows.append({
            "path": "/",
            "used": root["used"],
            "total": root["total"],
            "free": root.get("free", max(0, root["total"] - root["used"])),
            "pct": root.get("pct") or 0,
            "kind": "fs",
        })
        seen.add("/")
    for item in disk_hotspots(8):
        path = item.get("path")
        if not path or path in seen:
            continue
        item = dict(item)
        item["kind"] = "fs"
        rows.append(item)
        seen.add(path)

    home = os.path.expanduser("~")
    wanted = [
        "/tmp", "/var", "/var/log", "/var/tmp", "/opt", "/opt/ros",
        "/usr", "/home", "/root", "/data", "/media", "/mnt",
        home, os.path.join(home, ".ros"),
        os.path.join(home, ".ros", "log"),
        os.path.join(home, ".cache"),
    ]
    existing = []
    for path in wanted:
        if not path or not os.path.isdir(path):
            continue
        short = _short_path(path)
        if short in seen or path in seen:
            continue
        existing.append(path)
    if existing:
        out = _cmd(["du", "-xsk"] + existing, timeout=12.0)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                size = int(parts[0]) * 1024
            except ValueError:
                continue
            path = parts[-1]
            short = _short_path(path)
            if short in seen:
                continue
            seen.add(short)
            pct = (100.0 * size / total) if total > 0 else None
            rows.append({
                "path": short,
                "used": size,
                "total": int(total) if total else 0,
                "free": None,
                "pct": pct,
                "kind": "dir",
            })

    # Keep filesystem root first, then fattest directories.
    root_rows = [r for r in rows if r.get("kind") == "fs"]
    dir_rows = [r for r in rows if r.get("kind") != "fs"]
    dir_rows.sort(key=lambda r: -(r.get("used") or 0))
    out_rows = root_rows + dir_rows
    return out_rows[:limit]


def _ipv4(ifname):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            packed = struct.pack("256s", ifname[:15].encode("ascii", "replace"))
            addr = fcntl.ioctl(sock.fileno(), 0x8915, packed)[20:24]
            ip = socket.inet_ntoa(addr)
            if ip and not ip.startswith("127."):
                return ip
            return ip or "-"
        finally:
            sock.close()
    except (OSError, IOError, ValueError):
        pass
    out = _cmd(["ip", "-o", "-4", "addr", "show", "dev", ifname], timeout=0.3)
    for token in out.split():
        if "/" in token and token[0].isdigit():
            return token.split("/", 1)[0]
    return "-"


def _is_up(ifname):
    state = _read_first("/sys/class/net/%s/operstate" % ifname).lower()
    if state == "up":
        return True
    flags = _read_first("/sys/class/net/%s/flags" % ifname)
    try:
        val = int(flags, 16)
    except ValueError:
        return state in ("unknown", "dormant")
    return bool(val & 0x1) and state != "down"


def _is_wifi(ifname):
    return os.path.isdir("/sys/class/net/%s/wireless" % ifname)


def _wifi_info(ifname):
    ssid = _cmd(["iwgetid", ifname, "--raw"]) or ""
    if not ssid:
        link = _cmd(["iw", "dev", ifname, "link"])
        for line in link.splitlines():
            s = line.strip()
            if s.lower().startswith("ssid:"):
                ssid = s.split(":", 1)[1].strip()
    signal = None
    for line in _read("/proc/net/wireless").splitlines():
        if not line.lstrip().startswith(ifname + ":"):
            continue
        body = line.split(":", 1)[1].split()
        if len(body) >= 3:
            try:
                signal = int(float(body[2]))
            except ValueError:
                signal = None
        break
    if signal is None:
        link = _cmd(["iw", "dev", ifname, "link"])
        for line in link.splitlines():
            if "signal:" in line.lower():
                try:
                    signal = int(float(line.split("signal:", 1)[1].split()[0]))
                except (IndexError, ValueError):
                    pass
    if not ssid and signal is None:
        return None
    return {"ssid": ssid or "?", "signal": signal}


def _netdev():
    nics = {}
    lines = _read("/proc/net/dev").splitlines()
    for line in lines[2:]:
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name = name.strip()
        parts = rest.split()
        if len(parts) < 12:
            continue
        nics[name] = {
            "rx_bytes": int(parts[0]),
            "rx_drop": int(parts[3]),
            "tx_bytes": int(parts[8]),
            "tx_drop": int(parts[11]) if len(parts) > 11 else int(parts[9]),
        }
    return nics


def _skip_iface(name):
    if name == "lo":
        return False
    if name.lower() in SKIP_IFACE_NAMES:
        return True
    return any(name.startswith(p) for p in SKIP_IFACE_PREFIX)


def _clock():
    zone = _read_first("/etc/timezone") or None
    if not zone:
        link = ""
        try:
            link = os.path.realpath("/etc/localtime")
        except OSError:
            link = ""
        if "/zoneinfo/" in link:
            zone = link.split("/zoneinfo/", 1)[1]
    now = time.time()
    local = time.localtime(now)
    return {
        "epoch": now,
        "clock": time.strftime("%H:%M:%S", local),
        "date": time.strftime("%Y-%m-%d", local),
        "zone": zone or "none",
    }


def _wifi_aps():
    out = _cmd(
        ["nmcli", "-t", "-f", "IN-USE,SIGNAL,SECURITY,SSID", "dev", "wifi"],
        timeout=1.2,
    )
    if not out:
        return []
    rows = []
    seen = set()
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 4:
            continue
        in_use = parts[0].strip() in ("*", "yes", "yes: ")
        try:
            signal = int(float(parts[1])) if parts[1] else None
        except ValueError:
            signal = None
        security = parts[2] or "open"
        ssid = ":".join(parts[3:]).replace("\\:", ":").strip()
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        rows.append({
            "ssid": ssid,
            "signal": signal,
            "security": security if security not in ("", "--") else "open",
            "in_use": in_use,
        })
    rows.sort(key=lambda r: (
        0 if r.get("in_use") else 1,
        -(r.get("signal") if r.get("signal") is not None else -999),
        r.get("ssid") or "",
    ))
    return rows[:40]


def _neigh(ifname=None):
    args = ["ip", "-4", "neigh", "show"]
    if ifname:
        args.extend(["dev", ifname])
    out = _cmd(args, timeout=0.4)
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 1:
            continue
        ip = parts[0]
        mac = None
        dev = None
        state = ""
        if "lladdr" in parts:
            try:
                mac = parts[parts.index("lladdr") + 1]
            except IndexError:
                mac = None
        if "dev" in parts:
            try:
                dev = parts[parts.index("dev") + 1]
            except IndexError:
                dev = None
        if parts:
            state = parts[-1]
        if state.upper() in ("FAILED", "INCOMPLETE"):
            continue
        rows.append({"ip": ip, "mac": mac, "dev": dev, "state": state})
    return rows[:32]


def _tmux_sessions():
    out = _cmd(
        ["tmux", "list-sessions", "-F",
         "#{session_name}\t#{session_windows}\t#{session_attached}\t#{session_created}"],
        timeout=0.4,
    )
    if not out:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if not parts or not parts[0]:
            continue
        try:
            windows = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            windows = 0
        attached = False
        if len(parts) > 2:
            attached = parts[2] not in ("", "0")
        rows.append({
            "name": parts[0],
            "windows": windows,
            "attached": attached,
        })
    return rows[:16]


def _ping_rtt(target):
    if not target:
        return None
    out = _cmd(["ping", "-c", "1", "-W", "1", str(target)], timeout=1.6)
    if not out:
        return None
    # typical: time=3.21 ms
    for line in out.splitlines():
        if "time=" not in line and "time<" not in line:
            continue
        chunk = line.replace("time<", "time=")
        try:
            ms = float(chunk.split("time=", 1)[1].split()[0])
            return ms
        except (IndexError, ValueError):
            continue
    return None


def _bag_hint():
    root = os.path.join(os.path.expanduser("~"), ".ros")
    if not os.path.isdir(root):
        return None
    bags = 0
    size = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            rel = dirpath[len(root):].count(os.sep)
            if rel > 2:
                dirnames[:] = []
                continue
            for name in filenames:
                if not name.endswith(".bag"):
                    continue
                bags += 1
                try:
                    size += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
            if bags >= 80:
                break
    except OSError:
        return None
    return {"path": "~/.ros", "bags": bags, "bytes": size}


def _sock_inodes(tables):
    inodes = set()
    for path, ipv6 in tables:
        for line in _read(path).splitlines()[1:]:
            cols = line.split()
            if len(cols) < 10:
                continue
            try:
                inode = int(cols[9])
            except ValueError:
                continue
            if inode > 0:
                inodes.add(inode)
    return inodes


def top_talker():
    """Best-effort process with the most inet sockets. Skip if unreadable."""
    inodes = _sock_inodes((
        ("/proc/net/tcp", False),
        ("/proc/net/tcp6", True),
        ("/proc/net/udp", False),
        ("/proc/net/udp6", True),
    ))
    if not inodes:
        return None
    counts = {}
    mapped = 0
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return None
    for pid in pids:
        fd_dir = "/proc/%s/fd" % pid
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        n = 0
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if not target.startswith("socket:["):
                continue
            try:
                inode = int(target[8:-1])
            except ValueError:
                continue
            if inode in inodes:
                n += 1
        if n:
            mapped += n
            counts[int(pid)] = n
    if not counts or mapped < 1:
        return None
    pid, n = max(counts.items(), key=lambda kv: kv[1])
    comm = _read_first("/proc/%d/comm" % pid) or "?"
    return {"pid": pid, "name": comm, "socks": n}


def _parse_stat(data):
    lpar = data.find("(")
    rpar = data.rfind(")")
    if lpar < 0 or rpar < lpar:
        return None
    try:
        pid = int(data[:lpar])
    except ValueError:
        return None
    comm = data[lpar + 1:rpar]
    rest = data[rpar + 2:].split()
    if len(rest) < 22:
        return None
    utime = int(rest[11])
    stime = int(rest[12])
    rss_pages = int(rest[21])
    return pid, comm, utime + stime, rss_pages


def _clk_tck():
    try:
        return float(os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, TypeError):
        return 100.0


def _page_size():
    try:
        return int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, TypeError):
        return 4096


def _default_route():
    """Best-effort default gateway. Never raises."""
    for line in _read("/proc/net/route").splitlines()[1:]:
        cols = line.split()
        if len(cols) < 3:
            continue
        iface, dest, gw_hex = cols[0], cols[1], cols[2]
        if dest != "00000000":
            continue
        try:
            gw = socket.inet_ntoa(struct.pack("<L", int(gw_hex, 16)))
        except (ValueError, OSError, struct.error):
            continue
        if gw and gw != "0.0.0.0":
            return {"gw": gw, "iface": iface}
    out = _cmd(["ip", "route", "show", "default"], timeout=0.3)
    toks = out.split()
    gw = iface = None
    if "via" in toks:
        try:
            gw = toks[toks.index("via") + 1]
        except IndexError:
            gw = None
    if "dev" in toks:
        try:
            iface = toks[toks.index("dev") + 1]
        except IndexError:
            iface = None
    if gw or iface:
        return {"gw": gw, "iface": iface}
    return {"gw": None, "iface": None}


def _governor():
    return _read_first(
        "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"
    ) or None


def _uid_name(pid):
    try:
        st = os.stat("/proc/%d" % pid)
        return pwd.getpwuid(st.st_uid).pw_name
    except (OSError, KeyError):
        return "?"


def iter_procs():
    page = _page_size()
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return
    for spid in pids:
        raw = _read("/proc/%s/stat" % spid)
        if not raw:
            continue
        parsed = _parse_stat(raw)
        if not parsed:
            continue
        pid, comm, cputime, rss_pages = parsed
        rss = rss_pages * page
        yield {
            "pid": pid,
            "name": comm,
            "cputime": cputime,
            "rss": rss,
            "user": "",
        }


class HostSampler(object):
    def __init__(self):
        self._net = {}
        self._cpu = {}
        self._ts = 0.0
        self._talker = None
        self._talker_ts = 0.0
        self._aux_lock = threading.Lock()
        self._aux_stop = threading.Event()
        self._aux_thread = None
        self._rtt_thread = None
        self._wifi_aps = []
        self._neigh = []
        self._tmux = []
        self._rtt = None
        self._rtts = []
        self._bags = None
        self._paths = []
        self._disk_done = False
        self._clock = _clock()
        self._route = {"gw": None, "iface": None}

    def snapshot(self):
        now = time.monotonic()
        dt = now - self._ts if self._ts else 0.0
        mem = _mem()
        root = _statvfs("/") or {"used": 0, "total": 0, "pct": 0.0}
        load = (0.0, 0.0, 0.0)
        try:
            load = os.getloadavg()
        except OSError:
            pass

        raw_nics = _netdev()
        nics = []
        for name, cur in sorted(raw_nics.items()):
            if _skip_iface(name):
                continue
            if name != "lo" and not _is_up(name):
                continue
            prev = self._net.get(name)
            rx_bps = tx_bps = 0.0
            if prev and dt > 0.05:
                rx_bps = max(0.0, (cur["rx_bytes"] - prev["rx_bytes"]) / dt)
                tx_bps = max(0.0, (cur["tx_bytes"] - prev["tx_bytes"]) / dt)
            nics.append({
                "name": name,
                "ipv4": _ipv4(name) if name != "lo" else "127.0.0.1",
                "wifi": _wifi_info(name) if _is_wifi(name) else None,
                "rx_bps": rx_bps,
                "tx_bps": tx_bps,
                "rx_drop": cur["rx_drop"],
                "tx_drop": cur["tx_drop"],
                "up": True,
            })
        self._net = raw_nics

        lo_heavy = False
        for nic in nics:
            if nic["name"] == "lo" and (nic["rx_bps"] + nic["tx_bps"]) >= 100000:
                lo_heavy = True
        nics = [n for n in nics if n["name"] != "lo" or lo_heavy]

        clk = _clk_tck()
        procs = list(iter_procs())
        cpu_rows = []
        for p in procs:
            prev = self._cpu.get(p["pid"])
            cpu_pct = 0.0
            if prev and dt > 0.05:
                dj = p["cputime"] - prev
                if dj >= 0:
                    cpu_pct = (dj / clk) / dt * 100.0
            p["cpu"] = cpu_pct
            cpu_rows.append(p)
        self._cpu = {p["pid"]: p["cputime"] for p in procs}

        cpu_top = sorted(cpu_rows, key=lambda p: p["cpu"], reverse=True)[:5]
        mem_top = sorted(
            (p for p in cpu_rows if p["rss"] > 0),
            key=lambda p: p["rss"], reverse=True,
        )[:5]
        for row in cpu_top + mem_top:
            if not row["user"]:
                row["user"] = _uid_name(row["pid"])

        if self._talker_ts == 0.0:
            self._talker_ts = now
        elif now - self._talker_ts >= 3.0:
            self._talker = top_talker()
            self._talker_ts = now

        self._ts = now
        self._route = _default_route()
        self._clock = _clock()
        self._ensure_aux()
        with self._aux_lock:
            wifi_aps = list(self._wifi_aps)
            neigh = list(self._neigh)
            tmux = list(self._tmux)
            rtt = self._rtt
            rtts = list(self._rtts)
            bags = self._bags
            paths = list(self._paths)
        if not wifi_aps:
            for nic in nics:
                info = nic.get("wifi") or {}
                if info.get("ssid"):
                    wifi_aps = [{
                        "ssid": info.get("ssid"),
                        "signal": info.get("signal"),
                        "security": "",
                        "in_use": True,
                    }]
                    break
        rtt_avg = None
        if rtts:
            rtt_avg = sum(rtts) / float(len(rtts))
        return {
            "ts": now,
            "host": socket.gethostname(),
            "load": load,
            "nproc": _nproc(),
            "mem": mem,
            "root": {
                "used": root["used"],
                "total": root["total"],
                "pct": root["pct"],
                "free": max(0, root["total"] - root["used"]),
            },
            "nics": nics,
            "talker": self._talker,
            "paths": paths if paths else disk_hotspots(),
            "cpu_top": cpu_top,
            "mem_top": mem_top,
            "route": self._route,
            "governor": _governor(),
            "clock": self._clock,
            "wifi_aps": wifi_aps,
            "neigh": neigh,
            "tmux": tmux,
            "rtt_ms": rtt,
            "rtt_avg_ms": rtt_avg,
            "rtts": rtts,
            "bags": bags,
            "ready": dt > 0.05,
        }

    def _ensure_aux(self):
        if not (self._aux_thread and self._aux_thread.is_alive()):
            self._aux_stop.clear()
            self._aux_thread = threading.Thread(
                target=self._aux_loop, name="xcli-eval-host-aux",
            )
            self._aux_thread.daemon = True
            self._aux_thread.start()
        if not (self._rtt_thread and self._rtt_thread.is_alive()):
            self._rtt_thread = threading.Thread(
                target=self._rtt_loop, name="xcli-eval-rtt",
            )
            self._rtt_thread.daemon = True
            self._rtt_thread.start()

    def stop(self):
        self._aux_stop.set()
        for thread in (self._aux_thread, self._rtt_thread):
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=1.2)

    def _rtt_loop(self):
        while not self._aux_stop.is_set():
            gw = (self._route or {}).get("gw")
            try:
                rtt = _ping_rtt(gw)
            except Exception:
                rtt = None
            with self._aux_lock:
                self._rtt = rtt
                if rtt is not None:
                    self._rtts.append(rtt)
                    self._rtts = self._rtts[-10:]
            self._aux_stop.wait(2.0)

    def _aux_loop(self):
        first = True
        while not self._aux_stop.is_set():
            try:
                wifi = _wifi_aps()
            except Exception:
                wifi = []
            try:
                neigh = _neigh((self._route or {}).get("iface"))
            except Exception:
                neigh = []
            try:
                tmux = _tmux_sessions()
            except Exception:
                tmux = []
            try:
                bags = _bag_hint()
            except Exception:
                bags = None
            if not self._disk_done:
                try:
                    paths = disk_usage_once()
                except Exception:
                    paths = disk_hotspots()
                self._disk_done = True
            else:
                paths = None
            with self._aux_lock:
                self._wifi_aps = wifi
                self._neigh = neigh
                self._tmux = tmux
                self._bags = bags
                if paths is not None:
                    self._paths = paths
            self._aux_stop.wait(4.0 if first else 8.0)
            first = False


def snapshot(sampler=None):
    if sampler is None:
        sampler = HostSampler()
        time.sleep(0.15)
    return sampler.snapshot()
