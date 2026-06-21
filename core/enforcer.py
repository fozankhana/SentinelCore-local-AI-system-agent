import logging
import platform
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List

import psutil

log = logging.getLogger("enforcer")

CHROME_GPU_FLAGS = [
    "--use-angle=vulkan",
    "--use-gl=angle",
    "--enable-gpu-rasterization",
    "--enable-oop-rasterization",
    "--enable-zero-copy",
    "--ignore-gpu-blocklist",
    "--disable-software-rasterizer",
    "--enable-accelerated-video-decode",
    "--enable-accelerated-video-encode",
    "--force-gpu-mem-available-mb=4096",
]

CHROME_LIKE = {"chrome", "brave", "msedge", "edge", "arc", "chromium", "google-chrome", "brave-browser"}


class GPUEnforcer:
    def __init__(self, config, store):
        self.config = config
        self.store = store
        self._block_set = {r.exe.lower() for r in config.block}
        self._enforce_map = {r.exe.lower(): r for r in config.enforce}
        self._schedule_map = {r.exe.lower(): r for r in config.schedule}
        self._alert_cooldown: Dict[str, float] = {}
        self._cooldown_secs = 60

    def check(self, metrics: Dict[str, Any]):
        if not self.config.gpu.enforcement:
            return
        for proc in metrics.get("processes", []):
            self._evaluate(proc)

    def _evaluate(self, proc: Dict):
        raw_name = proc.get("name", "")
        name_lower = raw_name.lower()
        exe_base = name_lower.replace(".exe", "")

        matched_block = next(
            (k for k in self._block_set if k == exe_base or k == name_lower.replace(".exe", "")), None
        )
        if matched_block:
            self._kill(proc, f"blocked: {matched_block}")
            return

        matched_sched = next(
            (k for k in self._schedule_map if k in exe_base or k == exe_base), None
        )
        if matched_sched:
            rule = self._schedule_map[matched_sched]
            if not _within_hours(rule.allowed_hours):
                self._kill(proc, f"outside schedule ({rule.allowed_hours})")
                return

        matched_enforce = next(
            (k for k in self._enforce_map if k in exe_base or k == exe_base), None
        )
        if matched_enforce:
            rule = self._enforce_map[matched_enforce]
            self._enforce(proc, rule)

    def _enforce(self, proc: Dict, rule):
        violations = []
        cpu = proc.get("cpu_pct", 0)
        ram = proc.get("ram_mb", 0)

        if rule.max_cpu_pct and cpu > rule.max_cpu_pct:
            violations.append(f"CPU {cpu:.1f}% > cap {rule.max_cpu_pct}%")
        if rule.max_ram_mb and ram > rule.max_ram_mb:
            violations.append(f"RAM {ram:.0f}MB > cap {rule.max_ram_mb}MB")

        if not violations:
            return

        reason = "; ".join(violations)
        pid = proc.get("pid")
        name = proc.get("name", "")
        action = rule.action

        try:
            p = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return

        if action == "alert":
            self._throttled_alert(f"{pid}:{reason}", "warning", f"{name} (PID {pid}): {reason}", name, reason)
        elif action == "throttle":
            self._throttle(p, name, reason)
        elif action == "kill":
            self._kill(proc, reason)
        elif action == "restart":
            self._restart(p, name, reason)

    def _throttled_alert(self, key: str, severity: str, message: str, target: str, reason: str):
        now = time.time()
        if now - self._alert_cooldown.get(key, 0) < self._cooldown_secs:
            return
        self._alert_cooldown[key] = now
        self.store.add_alert(severity, message)
        self.store.add_audit("ALERT", target, reason, "alerted")

    def _throttle(self, p: psutil.Process, name: str, reason: str):
        try:
            if platform.system() == "Windows":
                p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            else:
                p.nice(10)
            log.info("Throttled %s (PID %s): %s", name, p.pid, reason)
            self.store.add_audit("THROTTLE", name, reason, f"priority lowered (PID {p.pid})")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            log.debug("Throttle failed for %s: %s", name, e)

    def _kill(self, proc: Dict, reason: str):
        pid = proc.get("pid")
        name = proc.get("name", "")
        if not pid:
            return
        key = f"kill:{pid}"
        now = time.time()
        if now - self._alert_cooldown.get(key, 0) < self._cooldown_secs:
            return
        self._alert_cooldown[key] = now
        try:
            psutil.Process(pid).terminate()
            msg = f"Killed {name} (PID {pid}): {reason}"
            log.warning(msg)
            self.store.add_audit("KILL", name, reason, f"terminated (PID {pid})")
            self.store.add_alert("warning", msg)
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            log.debug("Kill failed for %s: %s", name, e)

    def _restart(self, p: psutil.Process, name: str, reason: str):
        key = f"restart:{p.pid}"
        now = time.time()
        if now - self._alert_cooldown.get(key, 0) < self._cooldown_secs:
            return
        self._alert_cooldown[key] = now
        try:
            cmdline = p.cmdline()
            cwd = p.cwd()
            p.terminate()
            time.sleep(0.5)
            if cmdline:
                subprocess.Popen(cmdline, cwd=cwd)
            msg = f"Restarted {name}: {reason}"
            log.info(msg)
            self.store.add_audit("RESTART", name, reason, "restarted with original args")
            self.store.add_alert("info", msg)
        except Exception as e:
            log.error("Restart failed for %s: %s", name, e)

    def get_enforcement_status(self) -> List[Dict]:
        status = []
        try:
            running_procs = {
                p.info["name"].lower().replace(".exe", ""): p.pid
                for p in psutil.process_iter(["name"], ad_value=None)
                if p.info.get("name")
            }
        except Exception:
            running_procs = {}

        for exe, rule in self._enforce_map.items():
            matched_pid = next(
                (pid for name, pid in running_procs.items() if exe in name or name == exe), None
            )
            status.append({
                "rule_name": rule.name,
                "exe": rule.exe,
                "gpu_enforce": rule.gpu_enforce,
                "max_cpu_pct": rule.max_cpu_pct,
                "max_ram_mb": rule.max_ram_mb,
                "action": rule.action,
                "running": matched_pid is not None,
                "pid": matched_pid,
            })
        return status


def _within_hours(allowed_hours: str) -> bool:
    if not allowed_hours:
        return True
    try:
        parts = allowed_hours.split("-")
        if len(parts) != 2:
            return True
        sh, sm = map(int, parts[0].split(":"))
        eh, em = map(int, parts[1].split(":"))
        now = datetime.now()
        start = sh * 60 + sm
        end = eh * 60 + em
        cur = now.hour * 60 + now.minute
        if start <= end:
            return start <= cur <= end
        return cur >= start or cur <= end
    except Exception:
        return True
