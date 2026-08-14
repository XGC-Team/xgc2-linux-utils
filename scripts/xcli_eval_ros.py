#!/usr/bin/env python3
"""ROS snapshot for xcli eval. Host-only if ROS is absent."""
from __future__ import print_function

import atexit
import os
import re
import socket
import subprocess
import threading
import time
from xmlrpc.client import ServerProxy

IMPORTANT = re.compile(
    r"(scan|imu|odom|cmd_vel|/tf|joint_states|image|camera|lidar|gnss|gps|"
    r"pose|cloud|point|diag|status|battery|map|path|goal|/clock|laser|twist)",
    re.I,
)
SKIP_TOPIC = re.compile(
    r"(parameter_descriptions|parameter_updates|/bond$|/theora|"
    r"compressedDepth|/camera_info$)",
    re.I,
)
MAX_SUBS = 12
MAX_LIST = 48
WINDOW_S = 4.0


def _cmd(args, timeout=0.8):
    try:
        env = os.environ.copy()
        out = subprocess.check_output(
            args, stderr=subprocess.DEVNULL, timeout=timeout,
            universal_newlines=True, env=env,
        )
        return out.strip()
    except Exception:
        return ""


def detect_distro():
    distro = (os.environ.get("ROS_DISTRO") or "").strip()
    if distro:
        return distro
    opt = "/opt/ros"
    try:
        names = sorted(
            n for n in os.listdir(opt)
            if os.path.isdir(os.path.join(opt, n))
        )
    except OSError:
        names = []
    return names[-1] if names else ""


def _master_uri():
    return os.environ.get("ROS_MASTER_URI", "http://localhost:11311")


def _xmlrpc_call(method, *args):
    old = socket.getdefaulttimeout()
    socket.setdefaulttimeout(0.4)
    try:
        master = ServerProxy(_master_uri())
        return getattr(master, method)(*args)
    finally:
        socket.setdefaulttimeout(old)


def _short_node(name):
    name = (name or "").strip()
    if name.startswith("/"):
        name = name[1:]
    if not name:
        return "?"
    return name.split("/")[-1]


def ros1_state():
    """Return (ok, [{name, pubs, subs}, ...]) or (False, [])."""
    try:
        code, _msg, state = _xmlrpc_call("getSystemState", "/")
        if code != 1 or not state:
            return False, []
        publishers = state[0] if state else []
        subscribers = state[1] if len(state) > 1 else []
        by_name = {}
        for item in publishers or []:
            if not item:
                continue
            name = item[0]
            nodes = list(item[1] or []) if len(item) > 1 else []
            row = by_name.setdefault(name, {"name": name, "pubs": [], "subs": []})
            row["pubs"] = nodes
        for item in subscribers or []:
            if not item:
                continue
            name = item[0]
            nodes = list(item[1] or []) if len(item) > 1 else []
            row = by_name.setdefault(name, {"name": name, "pubs": [], "subs": []})
            row["subs"] = nodes
        return True, list(by_name.values())
    except Exception:
        return False, []


def ros2_topics():
    text = _cmd(["ros2", "topic", "list"], timeout=1.2)
    if not text:
        return False, []
    topics = []
    for line in text.splitlines():
        name = line.strip()
        if name.startswith("/"):
            topics.append({"name": name, "pubs": ["?"], "subs": []})
    return True, topics


def _own_node(name):
    short = _short_node(name)
    return short.startswith("xcli_eval")


def _clean_nodes(nodes):
    return [n for n in (nodes or []) if n and not _own_node(n)]


def _pick_topics(listed):
    listed = [
        t for t in listed
        if t.get("name")
        and t.get("name") != "/rosout"
        and not SKIP_TOPIC.search(t.get("name") or "")
    ]
    listed.sort(
        key=lambda t: (
            0 if IMPORTANT.search(t.get("name") or "") else 1,
            -(len(t.get("pubs") or []) + len(t.get("subs") or [])),
            t.get("name") or "",
        )
    )
    return listed[:MAX_LIST]


class _HzBucket(object):
    def __init__(self):
        self.times = []
        self.lock = threading.Lock()

    def hit(self):
        now = time.monotonic()
        with self.lock:
            self.times.append(now)
            cut = now - WINDOW_S
            if len(self.times) > 400:
                self.times = [t for t in self.times if t >= cut]

    def stats(self):
        now = time.monotonic()
        with self.lock:
            ts = [t for t in self.times if t >= now - WINDOW_S]
        n = len(ts)
        empty = {
            "hz": 0.0 if n == 0 else None,
            "cv": None,
            "n": n,
            "mean_ms": None,
            "std_ms": None,
        }
        if n <= 1:
            return empty
        dts = [ts[i + 1] - ts[i] for i in range(n - 1)]
        dts = [d for d in dts if d > 1e-6]
        if not dts:
            empty["hz"] = 0.0
            return empty
        mean = sum(dts) / float(len(dts))
        hz = 1.0 / mean if mean > 0 else 0.0
        var = sum((d - mean) ** 2 for d in dts) / float(len(dts))
        std = var ** 0.5
        cv = (std / mean) if mean > 0 else 0.0
        return {
            "hz": hz,
            "cv": cv,
            "n": n,
            "mean_ms": mean * 1000.0,
            "std_ms": std * 1000.0,
        }


class RosSampler(object):
    def __init__(self):
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.ok = False
        self.distro = detect_distro()
        self.topics = []
        self.sampling = False
        self.started_mono = 0.0
        self._buckets = {}
        self._subs = []
        self._rospy_tried = False
        self._prev_hz = {}
        self._stopped = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stopped = False
        self._rospy_tried = False
        self._stop.clear()
        self.started_mono = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="xcli-eval-ros")
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._stop.set()
        for sub in list(self._subs):
            try:
                sub.unregister()
            except Exception:
                pass
        self._subs = []
        self.sampling = False
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        try:
            import rospy
            import rospy.core
            if rospy.core.is_initialized() and not rospy.core.is_shutdown():
                try:
                    rospy.signal_shutdown("xcli eval stop")
                except Exception:
                    pass
            try:
                atexit.unregister(rospy.core._ros_atexit)
            except Exception:
                pass
        except Exception:
            pass

    def snapshot(self):
        with self._lock:
            topics = list(self.topics)
            return {
                "ok": self.ok,
                "distro": self.distro or "--",
                "topics": topics,
                "sampling": self.sampling,
                "warmup": (time.monotonic() - self.started_mono) < 2.0
                if self.started_mono else True,
            }

    def _publish(self, ok, listed):
        rows = []
        for item in _pick_topics(listed):
            name = item.get("name")
            pubs = _clean_nodes(item.get("pubs") or [])
            subs = _clean_nodes(item.get("subs") or [])
            bucket = self._buckets.get(name)
            st = bucket.stats() if bucket else {
                "hz": None, "cv": None, "n": 0, "mean_ms": None, "std_ms": None,
            }
            hz = st.get("hz")
            cv = st.get("cv")
            mean_ms = st.get("mean_ms")
            std_ms = st.get("std_ms")
            prev = self._prev_hz.get(name)
            jump = False
            if (
                std_ms is not None
                and mean_ms is not None
                and std_ms > max(2.0, 0.25 * mean_ms)
            ):
                jump = True
            if (
                prev is not None
                and hz is not None
                and prev > 1.0
                and hz > 0
                and abs(hz - prev) / prev > 0.35
            ):
                jump = True
            if hz is not None:
                self._prev_hz[name] = hz
            rows.append({
                "name": name,
                "hz": hz,
                "cv": cv,
                "mean_ms": mean_ms,
                "std_ms": std_ms,
                "n": st.get("n") or 0,
                "advertised": len(pubs) > 0,
                "n_pub": len(pubs),
                "n_sub": len(subs),
                "pubs": [_short_node(n) for n in pubs[:8]],
                "subs": [_short_node(n) for n in subs[:12]],
                "jump": jump,
            })
        with self._lock:
            self.ok = ok
            self.topics = rows
            if not self.distro:
                self.distro = detect_distro()

    def _run(self):
        while not self._stop.is_set():
            ok1, listed1 = ros1_state()
            if ok1:
                self._publish(True, listed1)
                if not self._rospy_tried:
                    self._rospy_tried = True
                    self._try_rospy([t.get("name") for t in _pick_topics(listed1)])
                self._stop.wait(1.0)
                continue
            ok2, listed2 = ros2_topics()
            self._publish(ok2, listed2)
            self._stop.wait(2.0 if ok2 else 1.5)

    def _try_rospy(self, names):
        try:
            import rospy
            from rospy.msg import AnyMsg
        except Exception:
            return
        if self.sampling:
            return
        os.environ.setdefault("ROSCONSOLE_MIN_SEVERITY", "ERROR")
        devnull = None
        old_err = old_out = None
        try:
            devnull = open(os.devnull, "w")
            old_err = os.dup(2)
            old_out = os.dup(1)
            os.dup2(devnull.fileno(), 2)
            os.dup2(devnull.fileno(), 1)
            try:
                rospy.init_node(
                    "xcli_eval", anonymous=True, disable_signals=True,
                    disable_rosout=True,
                )
            except Exception:
                try:
                    if not rospy.core.is_initialized():
                        return
                except Exception:
                    return
            for name in names[:MAX_SUBS]:
                if name not in self._buckets:
                    self._buckets[name] = _HzBucket()
                bucket = self._buckets[name]

                def _cb(_msg, b=bucket):
                    b.hit()

                try:
                    self._subs.append(
                        rospy.Subscriber(name, AnyMsg, _cb, queue_size=2)
                    )
                except Exception:
                    continue
            self.sampling = True
        finally:
            if old_out is not None:
                os.dup2(old_out, 1)
                os.close(old_out)
            if old_err is not None:
                os.dup2(old_err, 2)
                os.close(old_err)
            if devnull is not None:
                devnull.close()


def snapshot(sampler=None):
    if sampler is None:
        ok, listed = ros1_state()
        if not ok:
            ok, listed = ros2_topics()
        rows = []
        for item in _pick_topics(listed):
            pubs = _clean_nodes(item.get("pubs") or [])
            subs = _clean_nodes(item.get("subs") or [])
            rows.append({
                "name": item.get("name"),
                "hz": None,
                "cv": None,
                "mean_ms": None,
                "std_ms": None,
                "n": 0,
                "advertised": len(pubs) > 0,
                "n_pub": len(pubs),
                "n_sub": len(subs),
                "pubs": [_short_node(n) for n in pubs[:8]],
                "subs": [_short_node(n) for n in subs[:12]],
                "jump": False,
            })
        return {
            "ok": ok,
            "distro": detect_distro() or "--",
            "topics": rows,
            "sampling": False,
            "warmup": True,
        }
    return sampler.snapshot()
