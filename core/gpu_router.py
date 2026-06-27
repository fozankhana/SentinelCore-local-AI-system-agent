import logging
import os
import platform
import subprocess
import threading
import time
from typing import Dict, List, Optional

import psutil

log = logging.getLogger("gpu_router")


class GPURouter:
    """
    Tracks which processes are on which GPU and handles GPU-specific
    environment-variable injection when relaunching processes.
    """

    def __init__(self, collector, config):
        self._collector = collector
        self._config    = config
        self._manual: Dict[int, int] = {}   # pid → gpu_index (user-requested)
        self._lock = threading.Lock()
        self._gpu_cache: List[Dict] = []
        self._gpu_cache_ts: float   = 0.0
        self._GPU_CACHE_TTL = 2.0

    # ── GPU list ─────────────────────────────────────────────────────────────

    def list_gpus(self) -> List[Dict]:
        now = time.time()
        if now - self._gpu_cache_ts < self._GPU_CACHE_TTL:
            return self._gpu_cache
        try:
            gpus = self._collector._collect_gpu()
        except Exception:
            gpus = []
        self._gpu_cache    = gpus
        self._gpu_cache_ts = now
        return gpus

    def pick_least_loaded(self) -> int:
        gpus = self.list_gpus()
        valid = [g for g in gpus if g.get("util_pct") is not None]
        if not valid:
            return 0
        return min(valid, key=lambda g: g["util_pct"])["id"]

    # ── Routing table ────────────────────────────────────────────────────────

    def routing_table(self) -> List[Dict]:
        """
        Merges:
        - processes detected on each GPU by the driver (NVML / ROCm)
        - manually routed PIDs recorded via route_pid()
        """
        per_gpu: Dict[int, Dict[int, float]] = {}
        try:
            per_gpu = self._collector.get_per_gpu_process_map()
        except Exception:
            pass

        rows: List[Dict] = []
        seen: set = set()

        # Driver-detected assignments
        for gpu_idx, pid_map in per_gpu.items():
            for pid, vram_mb in pid_map.items():
                if pid in seen:
                    continue
                seen.add(pid)
                try:
                    p = psutil.Process(pid)
                    rows.append({
                        "pid":       pid,
                        "name":      p.name(),
                        "gpu_index": gpu_idx,
                        "vram_mb":   round(vram_mb, 1),
                        "source":    "driver",
                    })
                except psutil.NoSuchProcess:
                    pass

        # Manual assignments (add if not already from driver)
        with self._lock:
            manual = dict(self._manual)
        for pid, gpu_idx in manual.items():
            if pid in seen:
                continue
            seen.add(pid)
            try:
                p = psutil.Process(pid)
                rows.append({
                    "pid":       pid,
                    "name":      p.name(),
                    "gpu_index": gpu_idx,
                    "vram_mb":   0.0,
                    "source":    "manual",
                })
            except psutil.NoSuchProcess:
                with self._lock:
                    self._manual.pop(pid, None)

        return sorted(rows, key=lambda r: (r["gpu_index"], r["name"].lower()))

    # ── Manual routing ────────────────────────────────────────────────────────

    def route_pid(self, pid: int, gpu_index: int) -> Dict:
        """Relaunch a running process restricted to gpu_index."""
        try:
            p       = psutil.Process(pid)
            name    = p.name()
            cmdline = p.cmdline()
            cwd     = p.cwd()
        except Exception as e:
            return {"ok": False, "error": str(e)}

        env = os.environ.copy()
        env.update(self.build_env(gpu_index))

        try:
            p.terminate()
            time.sleep(0.4)
            flags = subprocess.DETACHED_PROCESS if platform.system() == "Windows" else 0
            subprocess.Popen(cmdline, cwd=cwd, env=env, creationflags=flags)
            with self._lock:
                self._manual[pid] = gpu_index
            log.info("Routed %s (PID %s) -> GPU %d", name, pid, gpu_index)
            return {"ok": True, "name": name, "gpu_index": gpu_index}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def record(self, pid: int, gpu_index: int):
        with self._lock:
            self._manual[pid] = gpu_index

    def forget(self, pid: int):
        with self._lock:
            self._manual.pop(pid, None)

    # ── Env builder ───────────────────────────────────────────────────────────

    def build_env(self, gpu_index: int) -> Dict[str, str]:
        """Return env vars that restrict a process to gpu_index."""
        backend = getattr(self._collector, "_gpu_backend", "cuda")
        idx = str(gpu_index)
        if backend == "cuda":
            return {
                "CUDA_VISIBLE_DEVICES":      idx,
                "__NV_PRIME_RENDER_OFFLOAD": "1",
                "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
            }
        if backend == "rocm":
            return {
                "HIP_VISIBLE_DEVICES":  idx,
                "ROCR_VISIBLE_DEVICES": idx,
                "GPU_DEVICE_ORDINAL":   idx,
                "HSA_ENABLE_SDMA":      "0",
            }
        if backend == "arc":
            return {
                "SYCL_DEVICE_FILTER":        f"level_zero:gpu:{idx}",
                "ZES_ENABLE_SYSMAN":         "1",
                "ONEAPI_DEVICE_SELECTOR":    f"level_zero:gpu:{idx}",
            }
        return {}

    # ── Config-driven static routes ───────────────────────────────────────────

    def gpu_index_for_rule(self, rule_key: str) -> int:
        """Return the configured gpu_index for an enforce rule, or -1 for auto."""
        for rule in self._config.enforce:
            if rule.exe.lower() == rule_key:
                return getattr(rule, "gpu_index", -1)
        return -1
