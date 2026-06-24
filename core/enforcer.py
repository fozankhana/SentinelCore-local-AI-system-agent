import logging
import os
import platform
import subprocess
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import psutil

from core import job_objects

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

# Chrome/Chromium flags for Apple Metal (macOS)
CHROME_METAL_FLAGS = [
    "--use-angle=metal",
    "--enable-gpu-rasterization",
    "--enable-oop-rasterization",
    "--enable-zero-copy",
    "--ignore-gpu-blocklist",
    "--disable-software-rasterizer",
    "--enable-accelerated-video-decode",
]

CHROME_LIKE  = {"chrome", "brave", "msedge", "edge", "arc", "chromium", "google-chrome", "brave-browser"}
FIREFOX_LIKE = {"firefox", "firefox-esr"}

# NVIDIA CUDA
CUDA_ENV = {
    "CUDA_VISIBLE_DEVICES": "0",
    "__NV_PRIME_RENDER_OFFLOAD": "1",
    "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
}

# AMD ROCm / HIP
ROCM_ENV = {
    "HIP_VISIBLE_DEVICES": "0",
    "ROCR_VISIBLE_DEVICES": "0",
    "GPU_DEVICE_ORDINAL": "0",
    "HSA_ENABLE_SDMA": "0",
}

# Intel Arc / oneAPI
ARC_ENV = {
    "SYCL_DEVICE_FILTER": "level_zero:gpu",
    "ZES_ENABLE_SYSMAN": "1",
    "ONEAPI_DEVICE_SELECTOR": "level_zero:gpu",
}


class GPUEnforcer:
    def __init__(self, config, store, collector=None):
        self.config     = config
        self.store      = store
        self._collector = collector
        self._block_set    = {r.exe.lower() for r in config.block}
        self._enforce_map  = {r.exe.lower(): r for r in config.enforce}
        self._schedule_map = {r.exe.lower(): r for r in config.schedule}
        self._alert_cooldown: Dict[str, float] = {}
        self._cooldown_secs = 60
        self._rule_state: Dict[str, Dict] = {
            exe: {"compliant": True, "vram_mb": 0.0, "last_action": None,
                  "last_violation": None, "violation_count": 0}
            for exe in self._enforce_map
        }
        # Auto-restart watcher: stores the last known cmdline/cwd per exe key
        self._last_cmdline: Dict[str, List] = {}
        self._last_cwd:     Dict[str, str]  = {}
        self._stop_watcher  = threading.Event()
        self._watcher_thread = threading.Thread(
            target=self._watcher_loop, daemon=True, name="sentinel-watcher"
        )
        self._watcher_thread.start()

    def _watcher_loop(self):
        """Background thread: auto-restart processes that have auto_restart=True and have died."""
        while not self._stop_watcher.wait(5):
            try:
                running_names = {
                    p.info["name"].lower().replace(".exe", "")
                    for p in psutil.process_iter(["name"], ad_value=None)
                    if p.info.get("name")
                }
                for exe, rule in list(self._enforce_map.items()):
                    if not rule.auto_restart:
                        continue
                    is_running = any(exe in n or n == exe for n in running_names)
                    if is_running or exe not in self._last_cmdline:
                        continue
                    key = f"watcher:{exe}"
                    now = time.time()
                    if now - self._alert_cooldown.get(key, 0) < 30:
                        continue
                    self._alert_cooldown[key] = now
                    cmdline = self._last_cmdline[exe]
                    cwd     = self._last_cwd.get(exe, ".")
                    try:
                        flags = subprocess.DETACHED_PROCESS if platform.system() == "Windows" else 0
                        subprocess.Popen(cmdline, cwd=cwd, creationflags=flags)
                        log.info("Auto-restart: relaunched %s", exe)
                        self.store.add_audit("AUTO_RESTART", exe, "process died", cmdline[0])
                        self.store.add_alert("info", f"Auto-restarted {exe}")
                    except Exception as exc:
                        log.error("Auto-restart failed for %s: %s", exe, exc)
            except Exception as exc:
                log.debug("Watcher loop error: %s", exc)

    def check(self, metrics: Dict[str, Any]):
        if not self.config.gpu.enforcement:
            return

        gpu_proc_map: Dict[int, float] = {}
        if self._collector:
            try:
                gpu_proc_map = self._collector.get_gpu_process_map()
            except Exception:
                pass

        for proc in metrics.get("processes", []):
            pid = proc.get("pid")
            if pid and pid in gpu_proc_map:
                proc["vram_mb"] = gpu_proc_map[pid]
            self._evaluate(proc)

    def _evaluate(self, proc: Dict):
        name_lower = proc.get("name", "").lower()
        exe_base   = name_lower.replace(".exe", "")

        # Name-based block
        blocked = next((k for k in self._block_set if k == exe_base), None)

        # Path-based block (checked only when name didn't match)
        if not blocked:
            path_rules = [r for r in self.config.block if r.path]
            if path_rules:
                try:
                    full_path = psutil.Process(proc["pid"]).exe().lower()
                    for rule in path_rules:
                        if rule.path.lower() in full_path:
                            blocked = rule.exe
                            break
                except Exception:
                    pass

        if blocked:
            self._kill(proc, f"blocked: {blocked}")
            return

        sched = next((k for k in self._schedule_map if k in exe_base or k == exe_base), None)
        if sched and not _within_hours(self._schedule_map[sched].allowed_hours):
            self._kill(proc, f"outside schedule ({self._schedule_map[sched].allowed_hours})")
            return

        matched = next((k for k in self._enforce_map if k in exe_base or k == exe_base), None)
        if matched:
            # Capture cmdline/cwd so the auto-restart watcher can relaunch if needed
            if self._enforce_map[matched].auto_restart:
                try:
                    p_obj = psutil.Process(proc["pid"])
                    self._last_cmdline[matched] = p_obj.cmdline()
                    self._last_cwd[matched]     = p_obj.cwd()
                except Exception:
                    pass
            self._enforce(proc, self._enforce_map[matched], matched)

    def _enforce(self, proc: Dict, rule, rule_key: str):
        cpu  = proc.get("cpu_pct",  0)
        ram  = proc.get("ram_mb",   0)
        vram = proc.get("vram_mb",  0.0)
        pid  = proc.get("pid")
        name = proc.get("name", "")

        if rule_key in self._rule_state:
            self._rule_state[rule_key]["vram_mb"] = vram

        violations = []
        if rule.max_cpu_pct  and cpu  > rule.max_cpu_pct:
            violations.append(f"CPU {cpu:.1f}% > cap {rule.max_cpu_pct}%")
        if rule.max_ram_mb   and ram  > rule.max_ram_mb:
            violations.append(f"RAM {ram:.0f}MB > cap {rule.max_ram_mb}MB")
        if rule.max_vram_mb  and vram > rule.max_vram_mb:
            violations.append(f"VRAM {vram:.0f}MB > cap {rule.max_vram_mb}MB")
        if rule.gpu_enforce and cpu > 5 and vram == 0.0:
            violations.append(f"GPU fallback: CPU={cpu:.1f}% VRAM=0 (not on GPU)")

        if not violations:
            if rule_key in self._rule_state:
                self._rule_state[rule_key]["compliant"] = True
            return

        reason = "; ".join(violations)
        if rule_key in self._rule_state:
            s = self._rule_state[rule_key]
            s["compliant"]       = False
            s["last_violation"]  = time.time()
            s["violation_count"] = s.get("violation_count", 0) + 1

        try:
            p = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return

        # GPU fallback takes priority: migrate before other actions
        if rule.gpu_enforce and any("GPU fallback" in v for v in violations):
            self._migrate_to_gpu(p, name, reason, rule_key)
            return

        action = rule.action
        if   action == "alert":    self._throttled_alert(f"{pid}:{reason}", "warning", f"{name} (PID {pid}): {reason}", name, reason)
        elif action == "throttle": self._throttle(p, name, reason, rule_key)
        elif action == "kill":     self._kill(proc, reason, rule_key)
        elif action == "restart":  self._restart(p, name, reason, rule_key)

    def _migrate_to_gpu(self, p: psutil.Process, name: str, reason: str, rule_key: str = ""):
        key = f"migrate:{p.pid}"
        now = time.time()
        if now - self._alert_cooldown.get(key, 0) < self._cooldown_secs:
            return
        self._alert_cooldown[key] = now

        exe_base = name.lower().replace(".exe", "")
        backend  = getattr(self._collector, "_gpu_backend", "cuda") if self._collector else "cuda"
        try:
            cmdline = p.cmdline()
            cwd     = p.cwd()
            env     = os.environ.copy()

            if exe_base in CHROME_LIKE:
                # Pick the right flag set for the active GPU backend
                flags_for_backend = CHROME_METAL_FLAGS if backend == "metal" else CHROME_GPU_FLAGS
                sentinel_flag     = flags_for_backend[0]
                # If flags already injected but VRAM still 0 — don't loop, alert instead
                if any(f in cmdline for f in flags_for_backend[:2]):
                    self._throttled_alert(
                        f"gpu-stuck:{p.pid}", "warning",
                        f"{name} has GPU flags but GPU utilisation is still 0 — hardware acceleration may be blocked.",
                        name, reason,
                    )
                    return
                extra       = [f for f in flags_for_backend if f not in cmdline]
                new_cmdline = cmdline + extra
            elif exe_base in FIREFOX_LIKE:
                env["MOZ_WEBRENDER"]   = "1"
                env["MOZ_ACCELERATED"] = "1"
                new_cmdline = cmdline
            else:
                # Non-browser: inject backend-specific env vars
                if backend == "rocm":
                    env.update(ROCM_ENV)
                elif backend == "arc":
                    env.update(ARC_ENV)
                elif backend == "metal":
                    pass  # Metal is automatic on macOS; no env injection needed
                else:
                    env.update(CUDA_ENV)
                new_cmdline = cmdline

            p.terminate()
            time.sleep(0.4)
            if new_cmdline:
                flags = 0
                if platform.system() == "Windows":
                    flags = subprocess.DETACHED_PROCESS
                subprocess.Popen(new_cmdline, cwd=cwd, env=env, creationflags=flags)

            log.info("MIGRATED  %-20s  → relaunched with %s GPU flags", name, backend.upper())
            self.store.add_audit("MIGRATE", name, reason, f"relaunched with {backend.upper()} flags (PID {p.pid})")
            self.store.add_alert("info", f"GPU migrated {name} (PID {p.pid})")
            if rule_key in self._rule_state:
                self._rule_state[rule_key]["last_action"] = "migrate"
        except Exception as e:
            log.error("GPU migration failed for %s: %s", name, e)

    def migrate_pid(self, pid: int) -> Dict:
        """Manual GPU migration from the dashboard."""
        try:
            p    = psutil.Process(pid)
            name = p.name()
            self._migrate_to_gpu(p, name, "manual migration via dashboard")
            return {"ok": True, "message": f"GPU migration triggered for {name} (PID {pid})"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _throttled_alert(self, key: str, severity: str, message: str, target: str, reason: str):
        now = time.time()
        if now - self._alert_cooldown.get(key, 0) < self._cooldown_secs:
            return
        self._alert_cooldown[key] = now
        self.store.add_alert(severity, message)
        self.store.add_audit("ALERT", target, reason, "alerted")

    def _throttle(self, p: psutil.Process, name: str, reason: str, rule_key: str = ""):
        try:
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if platform.system() == "Windows" else 10)
            log.info("Throttled %s (PID %s): %s", name, p.pid, reason)
            self.store.add_audit("THROTTLE", name, reason, f"priority lowered (PID {p.pid})")
            if rule_key in self._rule_state:
                self._rule_state[rule_key]["last_action"] = "throttle"
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            log.debug("Throttle failed for %s: %s", name, e)
            return
        # Best-effort: also apply a 25% CPU rate cap via Job Object
        r = job_objects.apply_cpu_rate(p.pid, 25)
        if r.get("ok"):
            log.info("Job Object CPU cap applied to %s (PID %s)", name, p.pid)

    def _kill(self, proc: Dict, reason: str, rule_key: str = ""):
        pid  = proc.get("pid")
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
            log.warning("Killed %s (PID %s): %s", name, pid, reason)
            self.store.add_audit("KILL", name, reason, f"terminated (PID {pid})")
            self.store.add_alert("warning", f"Killed {name} (PID {pid}): {reason}")
            if rule_key in self._rule_state:
                self._rule_state[rule_key]["last_action"] = "kill"
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            log.debug("Kill failed for %s: %s", name, e)

    def _restart(self, p: psutil.Process, name: str, reason: str, rule_key: str = ""):
        key = f"restart:{p.pid}"
        now = time.time()
        if now - self._alert_cooldown.get(key, 0) < self._cooldown_secs:
            return
        self._alert_cooldown[key] = now
        try:
            cmdline = p.cmdline()
            cwd     = p.cwd()
            p.terminate()
            time.sleep(0.5)
            if cmdline:
                subprocess.Popen(cmdline, cwd=cwd)
            log.info("Restarted %s: %s", name, reason)
            self.store.add_audit("RESTART", name, reason, "restarted with original args")
            self.store.add_alert("info", f"Restarted {name}: {reason}")
            if rule_key in self._rule_state:
                self._rule_state[rule_key]["last_action"] = "restart"
        except Exception as e:
            log.error("Restart failed for %s: %s", name, e)

    def cap_pid(self, pid: int, max_ram_mb: Optional[int] = None, cpu_rate_pct: Optional[int] = None) -> Dict:
        """Apply Job Object caps to an arbitrary PID from the dashboard."""
        results: Dict[str, Any] = {}
        try:
            name = psutil.Process(pid).name()
        except Exception:
            name = str(pid)
        if max_ram_mb:
            r = job_objects.apply_memory_cap(pid, max_ram_mb * 1048576)
            results["memory"] = r
            if r.get("ok"):
                self.store.add_audit("CAP", name, f"memory cap {max_ram_mb}MB", "Job Object applied")
        if cpu_rate_pct:
            r = job_objects.apply_cpu_rate(pid, cpu_rate_pct)
            results["cpu"] = r
            if r.get("ok"):
                self.store.add_audit("CAP", name, f"CPU rate {cpu_rate_pct}%", "Job Object applied")
        ok = any(v.get("ok") for v in results.values()) if results else False
        return {"ok": ok, "results": results}

    def release_cap(self, pid: int):
        job_objects.release_cap(pid)

    def list_capped(self) -> Dict:
        return {str(pid): True for pid in job_objects.list_capped()}

    def get_enforcement_status(self) -> List[Dict]:
        try:
            running = {
                p.info["name"].lower().replace(".exe", ""): p.pid
                for p in psutil.process_iter(["name"], ad_value=None)
                if p.info.get("name")
            }
        except Exception:
            running = {}

        result = []
        for exe, rule in self._enforce_map.items():
            matched_pid = next((pid for n, pid in running.items() if exe in n or n == exe), None)
            state = self._rule_state.get(exe, {})
            result.append({
                "rule_name":       rule.name,
                "exe":             rule.exe,
                "gpu_enforce":     rule.gpu_enforce,
                "max_cpu_pct":     rule.max_cpu_pct,
                "max_ram_mb":      rule.max_ram_mb,
                "max_vram_mb":     rule.max_vram_mb,
                "action":          rule.action,
                "auto_restart":    rule.auto_restart,
                "running":         matched_pid is not None,
                "pid":             matched_pid,
                "compliant":       state.get("compliant", True),
                "vram_mb":         state.get("vram_mb", 0.0),
                "last_action":     state.get("last_action"),
                "last_violation":  state.get("last_violation"),
                "violation_count": state.get("violation_count", 0),
            })
        return result


def _within_hours(allowed_hours: str) -> bool:
    if not allowed_hours:
        return True
    try:
        start_s, end_s = allowed_hours.split("-")
        sh, sm = map(int, start_s.split(":"))
        eh, em = map(int, end_s.split(":"))
        now    = datetime.now()
        start  = sh * 60 + sm
        end    = eh * 60 + em
        cur    = now.hour * 60 + now.minute
        return (start <= cur <= end) if start <= end else (cur >= start or cur <= end)
    except Exception:
        return True
