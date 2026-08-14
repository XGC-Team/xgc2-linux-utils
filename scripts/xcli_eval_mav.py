#!/usr/bin/env python3
"""MAVROS / PX4 snapshot for xcli eval. Started only on the MAVROS page."""
from __future__ import print_function

import math
import os
import subprocess
import threading
import time

try:
    import xcli_eval_ros as ros_mod
except Exception:
    ros_mod = None

# Adaptive PX4 1.12–1.16. First id that the FCU accepts is used.
# 1.12–1.13: EKF2_AID_MASK + EKF2_HGT_MODE + EKF2_RNG_AID
# 1.14–1.16: EKF2_EV_CTRL + EKF2_HGT_REF + EKF2_GPS_CTRL + EKF2_RNG_CTRL
PX4_PARAMS = (
    {
        "ids": ("EKF2_EV_CTRL",),
        "label": "Vision fusion",
        "era": "1.14-1.16",
        "indoor": "hpos+vpos+yaw: 11 or 15",
        "kind": "ev_ctrl",
    },
    {
        "ids": ("EKF2_AID_MASK",),
        "label": "Aid mask (legacy)",
        "era": "1.12-1.13",
        "indoor": "vis pos+yaw = 24; drop GPS bit0",
        "kind": "aid_mask",
    },
    {
        "ids": ("EKF2_HGT_REF", "EKF2_HGT_MODE"),
        "label": "Height source",
        "era": "1.14+ / 1.12-13",
        "indoor": "3 = vision   2 = range",
        "kind": "hgt",
    },
    {
        "ids": ("EKF2_RNG_CTRL", "EKF2_RNG_AID"),
        "label": "Range finder",
        "era": "1.14+ / 1.12-13",
        "indoor": "1 fuse   0 off",
        "kind": "rng",
    },
    {
        "ids": ("EKF2_GPS_CTRL",),
        "label": "GPS fusion",
        "era": "1.14-1.16",
        "indoor": "0 = off indoors",
        "kind": "gps",
    },
    {
        "ids": ("EKF2_EV_DELAY",),
        "label": "Vision delay",
        "era": "all",
        "indoor": "0-50 ms typical",
        "kind": "delay",
    },
    {
        "ids": ("COM_ARM_WO_GPS",),
        "label": "Arm without GPS",
        "era": "all",
        "indoor": "1 = allow indoor arm",
        "kind": "arm_gps",
    },
    {
        "ids": ("COM_DISARM_LAND",),
        "label": "Disarm after land",
        "era": "all",
        "indoor": "seconds, often 2",
        "kind": "disarm_land",
    },
    {
        "ids": ("COM_DISARM_PRFLT",),
        "label": "Disarm if no takeoff",
        "era": "1.13+",
        "indoor": "seconds, often 10",
        "kind": "disarm_pre",
    },
    {
        "ids": ("COM_RC_IN_MODE",),
        "label": "RC input mode",
        "era": "all",
        "indoor": "1 = stick/offboard ok",
        "kind": "rc",
    },
    {
        "ids": ("NAV_RCL_ACT",),
        "label": "RC-loss action",
        "era": "all",
        "indoor": "know this before fly",
        "kind": "rcl",
    },
    {
        "ids": ("EKF2_MAG_TYPE",),
        "label": "Magnetometer",
        "era": "all",
        "indoor": "5 = none (indoor mocap)",
        "kind": "mag",
    },
    {
        "ids": ("EKF2_OF_CTRL",),
        "label": "Optical flow",
        "era": "1.14-1.16",
        "indoor": "0 off unless you have flow",
        "kind": "flow",
    },
    {
        "ids": ("MPC_THR_HOVER",),
        "label": "Hover throttle",
        "era": "all",
        "indoor": "0.3-0.6 typical",
        "kind": "hover",
    },
    {
        "ids": ("MPC_TILTMAX_AIR",),
        "label": "Max tilt in air",
        "era": "all",
        "indoor": "deg, often 20-35",
        "kind": "tilt",
    },
    {
        "ids": ("MPC_TKO_SPEED",),
        "label": "Takeoff climb speed",
        "era": "all",
        "indoor": "m/s, often 1-1.5",
        "kind": "tko_spd",
    },
    {
        "ids": ("MIS_TAKEOFF_ALT",),
        "label": "Takeoff altitude",
        "era": "all",
        "indoor": "m, mission takeoff",
        "kind": "tko_alt",
    },
    {
        "ids": ("MPC_Z_P",),
        "label": "Altitude P gain",
        "era": "all",
        "indoor": "if takeoff is sluggish, check",
        "kind": "z_p",
    },
    {
        "ids": ("MPC_XY_P",),
        "label": "Horizontal P gain",
        "era": "all",
        "indoor": "position hold stiffness",
        "kind": "xy_p",
    },
)

GAP_OK_CM = 15.0
GAP_BAD_CM = 40.0


def _cmd(args, timeout=2.0):
    try:
        out = subprocess.check_output(
            args, stderr=subprocess.DEVNULL, timeout=timeout,
            universal_newlines=True,
        )
        return out.strip()
    except Exception:
        return ""


def _quat_rpy_deg(x, y, z, w):
    try:
        sinr = 2.0 * (w * x + y * z)
        cosr = 1.0 - 2.0 * (x * x + y * y)
        roll = math.degrees(math.atan2(sinr, cosr))
        sinp = 2.0 * (w * y - z * x)
        if abs(sinp) >= 1:
            pitch = math.copysign(90.0, sinp)
        else:
            pitch = math.degrees(math.asin(sinp))
        siny = 2.0 * (w * z + x * y)
        cosy = 1.0 - 2.0 * (y * y + z * z)
        yaw = math.degrees(math.atan2(siny, cosy))
        return (roll, pitch, yaw)
    except Exception:
        return (None, None, None)


MASK_BITS = (
    (1, "px"),
    (2, "py"),
    (4, "pz"),
    (8, "vx"),
    (16, "vy"),
    (32, "vz"),
    (64, "ax"),
    (128, "ay"),
    (256, "az"),
    (1024, "yaw"),
    (2048, "yawrate"),
)


def decode_type_mask(mask):
    """Return fields that ARE used (bit not set = used)."""
    try:
        mask = int(mask)
    except (TypeError, ValueError):
        return []
    used = []
    for bit, name in MASK_BITS:
        if (mask & bit) == 0:
            used.append(name)
    return used


def discover_namespaces():
    nss = []
    listed = []
    if ros_mod is not None:
        try:
            ok, rows = ros_mod.ros1_state()
            if ok:
                listed = [r.get("name") for r in rows if r.get("name")]
        except Exception:
            listed = []
    if not listed:
        text = _cmd(["rostopic", "list"], timeout=1.2)
        listed = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("/")]
    seen = set()
    for name in listed:
        if "/mavros/" not in name:
            continue
        ns = name.split("/mavros/", 1)[0]
        if ns in seen:
            continue
        seen.add(ns)
        nss.append(ns or "")
    nss.sort()
    return nss


class _PoseTap(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.t = 0.0
        self.xyz = None
        self.rpy = (None, None, None)
        self.n = 0
        self.hits = []
        self.extra = {}

    def hit(self, xyz, rpy=None, extra=None):
        now = time.monotonic()
        with self.lock:
            self.t = now
            self.xyz = xyz
            if rpy is not None:
                self.rpy = rpy
            self.n += 1
            self.hits.append(now)
            if extra:
                self.extra = extra
            cut = now - 2.0
            if len(self.hits) > 80:
                self.hits = [t for t in self.hits if t >= cut]

    def view(self, now):
        with self.lock:
            age = (now - self.t) if self.t else None
            live = age is not None and age < 1.0
            recent = [t for t in self.hits if t >= now - 2.0]
            hz = (len(recent) / 2.0) if recent else 0.0
            return {
                "live": live,
                "age": age,
                "xyz": self.xyz,
                "rpy": self.rpy,
                "yaw": self.rpy[2] if self.rpy else None,
                "n": self.n,
                "hz": hz,
                "extra": dict(self.extra),
            }


class MavSampler(object):
    def __init__(self):
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._stopped = False
        self._subs = []
        self._rospy = False
        self.ns = ""
        self.nss = []
        self.params = []
        self.backend = "none"
        self.fw = "unknown"
        self.state = {}
        self.taps = {
            "local": _PoseTap(),
            "vision": _PoseTap(),
            "vrpn": _PoseTap(),
            "setpoint": _PoseTap(),
        }

    def start(self):
        self._stopped = False
        self._stop.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="xcli-eval-mav")
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
        self._rospy = False
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)

    def set_ns(self, ns):
        if ns == self.ns:
            return
        self.ns = ns or ""
        self._resub()

    def snapshot(self):
        now = time.monotonic()
        local = self.taps["local"].view(now)
        vision = self.taps["vision"].view(now)
        vrpn = self.taps["vrpn"].view(now)
        sp = self.taps["setpoint"].view(now)
        gap_cm = None
        yaw_gap = None
        if local.get("xyz") and vision.get("xyz") and local.get("live") and vision.get("live"):
            dx = local["xyz"][0] - vision["xyz"][0]
            dy = local["xyz"][1] - vision["xyz"][1]
            dz = local["xyz"][2] - vision["xyz"][2]
            gap_cm = math.sqrt(dx * dx + dy * dy + dz * dz) * 100.0
            if local.get("yaw") is not None and vision.get("yaw") is not None:
                yaw_gap = abs(local["yaw"] - vision["yaw"])
                if yaw_gap > 180:
                    yaw_gap = 360 - yaw_gap
        why, fused = self._diagnose(local, vision, vrpn, gap_cm)
        fly = self._why_no_takeoff(local, vision, sp, fused)
        with self._lock:
            return {
                "ns": self.ns,
                "nss": list(self.nss),
                "backend": self.backend,
                "state": dict(self.state),
                "params": list(self.params),
                "local": local,
                "vision": vision,
                "vrpn": vrpn,
                "setpoint": sp,
                "gap_cm": gap_cm,
                "yaw_gap_deg": yaw_gap,
                "fused": fused,
                "why": why,
                "fly": fly,
                "fw": self.fw,
            }

    def _why_no_takeoff(self, local, vision, sp, fused):
        st = self.state or {}
        if not st.get("connected") and self.backend == "none":
            return "no MAVROS / FCU link"
        if st and not st.get("connected"):
            return "FCU not connected"
        if not st.get("armed"):
            gps = self._param_num("COM_ARM_WO_GPS")
            if gps is not None and int(gps) == 0:
                return "disarmed; COM_ARM_WO_GPS=0 (indoor often cannot arm)"
            return "disarmed"
        mode = (st.get("mode") or "").upper()
        if "OFFBOARD" in mode and not sp.get("live"):
            return "OFFBOARD but no setpoint stream"
        if mode in ("MANUAL", "STABILIZED", "STAB") and not st.get("rc"):
            return "manual mode and no RC"
        if not fused and not local.get("live"):
            return "no local pose — EKF has no position"
        if "AUTO.TAKEOFF" in mode or "TAKEOFF" in mode:
            return "takeoff mode commanded; watch climb"
        if st.get("landed") == "ON_GROUND" and "OFFBOARD" in mode:
            return "on ground in OFFBOARD; need upward setpoint / takeoff"
        return ""

    def _diagnose(self, local, vision, vrpn, gap_cm):
        ev = self._param_num("EKF2_EV_CTRL")
        aid = self._param_num("EKF2_AID_MASK")
        ev_on = None
        if ev is not None:
            ev_on = bool(int(ev) & (1 | 2 | 8))
        elif aid is not None:
            ev_on = bool(int(aid) & (8 | 16))
        if not vision.get("live") and not vrpn.get("live"):
            return "no mocap / vision stream into MAVROS", False
        if vision.get("live") is False and vrpn.get("live"):
            return "VRPN live, but /mavros/vision_pose is silent (bridge?)", False
        if ev_on is False:
            return "FCU not set to fuse vision (EKF2_EV_CTRL / AID_MASK)", False
        if not local.get("live"):
            return "vision in, but local pose silent (FCU / EKF not publishing)", False
        if gap_cm is None:
            return "waiting for both poses", False
        if gap_cm <= GAP_OK_CM:
            return "vision and local agree (likely fused)", True
        if gap_cm >= GAP_BAD_CM:
            return "gap %.0f cm — EKF rejecting or not following EV" % gap_cm, False
        return "gap %.0f cm — marginal fusion" % gap_cm, False

    def _param_num(self, name):
        with self._lock:
            for row in self.params:
                if row.get("id") == name and row.get("value") is not None:
                    try:
                        return float(row["value"])
                    except (TypeError, ValueError):
                        return None
        return None

    def _run(self):
        self.nss = discover_namespaces()
        if self.nss and not self.ns:
            self.ns = self.nss[0]
        self._wire()
        self._pull_params()
        while not self._stop.is_set():
            self.nss = discover_namespaces()
            if self.nss and self.ns not in self.nss:
                self.ns = self.nss[0]
                self._resub()
                self._pull_params()
            self._pull_state()
            self._stop.wait(2.0)

    def _topic(self, tail):
        ns = self.ns or ""
        if tail.startswith("/"):
            return (ns + tail) if ns else tail
        return (ns + "/" + tail) if ns else "/" + tail

    def _wire(self):
        if self._rospy:
            return
        try:
            import rospy
            from geometry_msgs.msg import PoseStamped
            from mavros_msgs.msg import PositionTarget, State
        except Exception:
            self.backend = "none"
            return
        try:
            if not rospy.core.is_initialized():
                os.environ.setdefault("ROSCONSOLE_MIN_SEVERITY", "ERROR")
                rospy.init_node(
                    "xcli_eval_mav", anonymous=True,
                    disable_signals=True, disable_rosout=True,
                )
            self._rospy = True
            self.backend = "mavros"
        except Exception:
            self.backend = "none"
            return
        self._resub()

    def _resub(self):
        for sub in list(self._subs):
            try:
                sub.unregister()
            except Exception:
                pass
        self._subs = []
        if not self._rospy:
            return
        try:
            import rospy
            from geometry_msgs.msg import PoseStamped
            from mavros_msgs.msg import PositionTarget, State
        except Exception:
            return

        def pose_cb(msg, key):
            p = msg.pose.position
            o = msg.pose.orientation
            self.taps[key].hit((p.x, p.y, p.z), _quat_rpy_deg(o.x, o.y, o.z, o.w))

        def state_cb(msg):
            with self._lock:
                cur = dict(self.state)
                cur.update({
                    "connected": bool(msg.connected),
                    "armed": bool(msg.armed),
                    "mode": msg.mode or "",
                    "guided": bool(getattr(msg, "guided", False)),
                    "manual": bool(getattr(msg, "manual_input", False)),
                })
                self.state = cur

        def ext_cb(msg):
            landed = getattr(msg, "landed_state", 0)
            names = {0: "undef", 1: "ON_GROUND", 2: "IN_AIR", 3: "TAKEOFF", 4: "LANDING"}
            with self._lock:
                cur = dict(self.state)
                cur["landed"] = names.get(int(landed), str(landed))
                self.state = cur

        def rc_cb(msg):
            chans = list(getattr(msg, "channels", []) or [])
            with self._lock:
                cur = dict(self.state)
                cur["rc"] = bool(chans) and any(c > 900 for c in chans[:4])
                cur["rssi"] = getattr(msg, "rssi", None)
                self.state = cur

        def sp_cb(msg):
            pos = getattr(msg, "position", None)
            xyz = (0.0, 0.0, 0.0)
            if pos is not None:
                xyz = (pos.x, pos.y, pos.z)
            mask = getattr(msg, "type_mask", 0)
            yaw = getattr(msg, "yaw", None)
            rpy = (None, None, math.degrees(yaw) if yaw is not None else None)
            self.taps["setpoint"].hit(
                xyz, rpy,
                extra={"mask": mask, "used": decode_type_mask(mask)},
            )

        try:
            from mavros_msgs.msg import ExtendedState, RCIn
        except Exception:
            ExtendedState = RCIn = None

        pairs = [
            (self._topic("/mavros/local_position/pose"), PoseStamped, lambda m: pose_cb(m, "local")),
            (self._topic("/mavros/vision_pose/pose"), PoseStamped, lambda m: pose_cb(m, "vision")),
            (self._topic("/pose"), PoseStamped, lambda m: pose_cb(m, "vrpn")),
            (self._topic("/mavros/setpoint_raw/local"), PositionTarget, sp_cb),
            (self._topic("/mavros/state"), State, state_cb),
        ]
        if ExtendedState is not None:
            pairs.append((self._topic("/mavros/extended_state"), ExtendedState, ext_cb))
        if RCIn is not None:
            pairs.append((self._topic("/mavros/rc/in"), RCIn, rc_cb))
        extra = self._topic("/vrpn_client_node/pose")
        pairs.append((extra, PoseStamped, lambda m: pose_cb(m, "vrpn")))
        for name, typ, cb in pairs:
            try:
                self._subs.append(rospy.Subscriber(name, typ, cb, queue_size=2))
            except Exception:
                continue

    def _pull_state(self):
        return

    def _pull_params(self):
        rows = []
        have_ev = False
        have_aid = False
        for spec in PX4_PARAMS:
            chosen = None
            val = None
            for pid in spec["ids"]:
                got = self._param_get(pid)
                if got is None:
                    continue
                chosen = pid
                val = got
                break
            if chosen is None:
                continue
            if chosen == "EKF2_EV_CTRL":
                have_ev = True
            if chosen == "EKF2_AID_MASK":
                have_aid = True
            rows.append({
                "id": chosen,
                "ids": spec["ids"],
                "label": spec["label"],
                "hint": spec["indoor"],
                "era": spec["era"],
                "kind": spec["kind"],
                "value": val,
                "note": _param_note(chosen, val),
            })
        if have_ev:
            fw = "PX4 1.14-1.16"
        elif have_aid:
            fw = "PX4 1.12-1.13"
        elif rows:
            fw = "PX4 (param set partial)"
        else:
            fw = "no FCU params"
        with self._lock:
            self.params = rows
            self.fw = fw

    def _param_get(self, name):
        svc = self._topic("/mavros/param/get")
        try:
            import rospy
            from mavros_msgs.srv import ParamGet
            rospy.wait_for_service(svc, timeout=0.4)
            fn = rospy.ServiceProxy(svc, ParamGet)
            resp = fn(name)
            if not resp.success:
                return None
            pv = resp.value
            if abs(pv.real) > 1e-9 or pv.integer == 0:
                # prefer integer when it looks integral
                if pv.integer != 0:
                    return pv.integer
                return pv.real
            return pv.integer
        except Exception:
            pass
        out = _cmd(
            ["rosservice", "call", svc, "param_id: '%s'" % name],
            timeout=1.5,
        )
        if not out:
            return None
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("integer:"):
                try:
                    iv = int(line.split(":", 1)[1])
                    if iv:
                        return iv
                except ValueError:
                    pass
            if line.startswith("real:"):
                try:
                    return float(line.split(":", 1)[1])
                except ValueError:
                    pass
        return None

    def param_set(self, name, value):
        allowed = set()
        for spec in PX4_PARAMS:
            allowed.update(spec["ids"])
        if name not in allowed:
            return False, "blocked param"
        svc = self._topic("/mavros/param/set")
        try:
            ival = int(float(value))
            rval = float(value)
        except (TypeError, ValueError):
            return False, "bad value"
        try:
            import rospy
            from mavros_msgs.srv import ParamSet
            from mavros_msgs.msg import ParamValue
            rospy.wait_for_service(svc, timeout=0.6)
            fn = rospy.ServiceProxy(svc, ParamSet)
            pv = ParamValue(integer=ival, real=rval)
            resp = fn(name, pv)
            if resp.success:
                self._pull_params()
                return True, "set %s=%s" % (name, value)
            return False, "FCU rejected %s" % name
        except Exception as exc:
            return False, str(exc)

    def reboot_fcu(self):
        svc = self._topic("/mavros/cmd/command")
        try:
            import rospy
            from mavros_msgs.srv import CommandLong
            rospy.wait_for_service(svc, timeout=0.6)
            fn = rospy.ServiceProxy(svc, CommandLong)
            # MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN = 246, param1=1 reboot autopilot
            resp = fn(False, 246, 0, 1, 0, 0, 0, 0, 0, 0)
            if resp.success:
                return True, "FCU reboot sent"
            return False, "FCU reboot rejected"
        except Exception as exc:
            return False, str(exc)


def _param_note(pid, val):
    if val is None:
        return "unread"
    try:
        num = float(val)
    except (TypeError, ValueError):
        return ""
    if pid == "EKF2_EV_CTRL":
        bits = int(num)
        flags = []
        if bits & 1:
            flags.append("hpos")
        if bits & 2:
            flags.append("vpos")
        if bits & 4:
            flags.append("vel")
        if bits & 8:
            flags.append("yaw")
        return ",".join(flags) or "vision fusion OFF"
    if pid == "EKF2_AID_MASK":
        bits = int(num)
        flags = []
        if bits & 8:
            flags.append("vis pos")
        if bits & 16:
            flags.append("vis yaw")
        if bits & 1:
            flags.append("gps")
        return ",".join(flags) or "no vis bits"
    if pid in ("EKF2_HGT_REF", "EKF2_HGT_MODE"):
        return {0: "baro", 1: "gps", 2: "range", 3: "vision"}.get(int(num), str(int(num)))
    if pid == "EKF2_RNG_CTRL":
        return "range ON" if int(num) else "range OFF"
    if pid == "EKF2_GPS_CTRL":
        return "gps OFF (indoor ok)" if int(num) == 0 else "gps ON"
    if pid == "COM_ARM_WO_GPS":
        return "can arm indoor" if int(num) else "needs GPS to arm"
    if pid == "EKF2_MAG_TYPE":
        return {
            0: "auto",
            1: "heading",
            2: "3D",
            3: "init only",
            4: "unused",
            5: "none (indoor ok)",
        }.get(int(num), str(int(num)))
    if pid == "EKF2_OF_CTRL":
        return "flow ON" if int(num) else "flow OFF"
    if pid == "MPC_THR_HOVER":
        return "hover %.0f%%" % (num * 100.0) if num <= 1.5 else "hover %.2f" % num
    return ""
