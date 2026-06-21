import platform
import time
import logging
from typing import Any, Dict, List, Optional

import psutil

log = logging.getLogger("collector")


class MetricsCollector:
    def __init__(self, config):
        self.config = config
        self._last_net: Dict[str, Any] = {}
        self._last_disk: Dict[str, Any] = {}
        self._last_collect_time: Optional[float] = None
        self._gpu_backend: str = "none"
        self._nvml_handles: list = []
        self._pynvml = None
        self._init_gpu()
        psutil.cpu_percent(percpu=True)

    def _init_gpu(self):
        try:
            import pynvml
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            self._nvml_handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(count)]
            self._pynvml = pynvml
            self._gpu_backend = "cuda"
            log.info(f"NVIDIA NVML: {count} GPU(s) detected")
            return
        except Exception:
            pass

        try:
            import pyamdgpuinfo
            self._pyamdgpuinfo = pyamdgpuinfo
            self._gpu_backend = "rocm"
            log.info("AMD ROCm GPU detected")
            return
        except Exception:
            pass

        if platform.system() == "Darwin":
            self._gpu_backend = "metal"
            log.info("Apple Metal GPU backend")
            return

        log.info("No dedicated GPU backend found — GPU metrics unavailable")

    def collect(self) -> Dict[str, Any]:
        now = time.time()
        dt = (now - self._last_collect_time) if self._last_collect_time else 1.0
        self._last_collect_time = now

        return {
            "timestamp": now,
            "cpu": self._collect_cpu(),
            "memory": self._collect_memory(),
            "gpus": self._collect_gpu(),
            "network": self._collect_network(dt),
            "disks": self._collect_disk(dt),
            "processes": self._collect_processes(),
            "platform": platform.system(),
        }

    def _collect_cpu(self) -> Dict:
        per_core_pct = psutil.cpu_percent(percpu=True)
        total_pct = sum(per_core_pct) / len(per_core_pct) if per_core_pct else 0

        try:
            freqs = psutil.cpu_freq(percpu=True) or []
        except Exception:
            freqs = []

        try:
            raw_temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
            temps_list = (
                raw_temps.get("coretemp")
                or raw_temps.get("cpu_thermal")
                or raw_temps.get("k10temp")
                or []
            )
        except Exception:
            temps_list = []

        global_freq = psutil.cpu_freq()
        cores = []
        for i, pct in enumerate(per_core_pct):
            core: Dict[str, Any] = {"id": i, "pct": pct}
            if i < len(freqs):
                core["mhz"] = round(freqs[i].current, 1)
            elif global_freq:
                core["mhz"] = round(global_freq.current, 1)
            else:
                core["mhz"] = None
            core["temp_c"] = temps_list[i].current if i < len(temps_list) else None
            cores.append(core)

        return {
            "cores": cores,
            "total_pct": round(total_pct, 1),
            "count_physical": psutil.cpu_count(logical=False),
            "count_logical": psutil.cpu_count(logical=True),
        }

    def _collect_memory(self) -> Dict:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        return {
            "total_mb": round(vm.total / 1048576, 1),
            "used_mb": round(vm.used / 1048576, 1),
            "available_mb": round(vm.available / 1048576, 1),
            "cached_mb": round(getattr(vm, "cached", 0) / 1048576, 1),
            "pct": vm.percent,
            "swap_mb": round(sw.used / 1048576, 1),
            "swap_total_mb": round(sw.total / 1048576, 1),
            "swap_pct": sw.percent,
        }

    def _collect_gpu(self) -> List[Dict]:
        if self._gpu_backend == "cuda" and self._nvml_handles:
            return self._collect_nvidia()
        if self._gpu_backend == "rocm":
            return self._collect_rocm()
        if self._gpu_backend == "metal":
            return [{"id": 0, "name": "Apple Silicon GPU", "backend": "metal",
                     "util_pct": None, "vram_used_mb": None, "vram_total_mb": None,
                     "temp_c": None, "watts": None, "fan_pct": None}]
        return []

    def _collect_nvidia(self) -> List[Dict]:
        pynvml = self._pynvml
        gpus = []
        for i, handle in enumerate(self._nvml_handles):
            try:
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode()
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                try:
                    temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                except Exception:
                    temp = None
                try:
                    watts = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
                except Exception:
                    watts = None
                try:
                    fan = pynvml.nvmlDeviceGetFanSpeed(handle)
                except Exception:
                    fan = None
                gpus.append({
                    "id": i,
                    "name": name,
                    "backend": "cuda",
                    "util_pct": util.gpu,
                    "mem_util_pct": util.memory,
                    "vram_used_mb": round(mem.used / 1048576, 1),
                    "vram_total_mb": round(mem.total / 1048576, 1),
                    "vram_free_mb": round(mem.free / 1048576, 1),
                    "temp_c": temp,
                    "watts": round(watts, 1) if watts else None,
                    "fan_pct": fan,
                })
            except Exception as e:
                log.debug(f"GPU {i} error: {e}")
                gpus.append({"id": i, "name": f"GPU {i}", "backend": "cuda", "error": str(e)})
        return gpus

    def _collect_rocm(self) -> List[Dict]:
        try:
            pyamd = self._pyamdgpuinfo
            gpus = []
            for i in range(pyamd.detect_gpus()):
                gpu = pyamd.get_gpu(i)
                gpus.append({
                    "id": i,
                    "name": gpu.name,
                    "backend": "rocm",
                    "util_pct": gpu.query_load(),
                    "vram_used_mb": round(gpu.query_vram_used() / 1048576, 1),
                    "vram_total_mb": round(gpu.query_vram() / 1048576, 1),
                    "temp_c": gpu.query_temperature(),
                    "watts": gpu.query_power(),
                    "fan_pct": None,
                })
            return gpus
        except Exception as e:
            log.debug(f"ROCm collect error: {e}")
            return []

    def _collect_network(self, dt: float) -> Dict:
        try:
            counters = psutil.net_io_counters(pernic=True) or {}
        except Exception:
            counters = {}

        interfaces = []
        for name, stats in counters.items():
            prev = self._last_net.get(name)
            if prev:
                bytes_in = max(0.0, (stats.bytes_recv - prev.bytes_recv) / dt)
                bytes_out = max(0.0, (stats.bytes_sent - prev.bytes_sent) / dt)
            else:
                bytes_in = bytes_out = 0.0
            self._last_net[name] = stats
            interfaces.append({
                "name": name,
                "bytes_in": round(bytes_in, 1),
                "bytes_out": round(bytes_out, 1),
                "total_recv_mb": round(stats.bytes_recv / 1048576, 1),
                "total_sent_mb": round(stats.bytes_sent / 1048576, 1),
                "packets_in": stats.packets_recv,
                "packets_out": stats.packets_sent,
                "errin": stats.errin,
                "errout": stats.errout,
            })

        conns = []
        try:
            for c in psutil.net_connections(kind="inet"):
                if c.raddr:
                    try:
                        proc_name = psutil.Process(c.pid).name() if c.pid else ""
                    except Exception:
                        proc_name = ""
                    conns.append({
                        "pid": c.pid,
                        "proc": proc_name,
                        "laddr": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else "",
                        "raddr": f"{c.raddr.ip}:{c.raddr.port}",
                        "status": c.status or "",
                    })
        except Exception:
            pass

        return {"interfaces": interfaces, "connections": conns[:100]}

    def _collect_disk(self, dt: float) -> List[Dict]:
        try:
            counters = psutil.disk_io_counters(perdisk=True) or {}
        except Exception:
            counters = {}

        disks = []
        for device, stats in counters.items():
            prev = self._last_disk.get(device)
            if prev:
                read_mbs = max(0.0, (stats.read_bytes - prev.read_bytes) / dt / 1048576)
                write_mbs = max(0.0, (stats.write_bytes - prev.write_bytes) / dt / 1048576)
            else:
                read_mbs = write_mbs = 0.0
            self._last_disk[device] = stats
            disks.append({
                "device": device,
                "read_mbs": round(read_mbs, 2),
                "write_mbs": round(write_mbs, 2),
                "read_count": stats.read_count,
                "write_count": stats.write_count,
            })

        partitions = []
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                partitions.append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / 1073741824, 1),
                    "used_gb": round(usage.used / 1073741824, 1),
                    "free_gb": round(usage.free / 1073741824, 1),
                    "pct": usage.percent,
                })
            except Exception:
                pass

        return disks

    def _collect_processes(self) -> List[Dict]:
        procs = []
        attrs = ["pid", "name", "cpu_percent", "memory_info", "status", "username"]
        for proc in psutil.process_iter(attrs, ad_value=None):
            try:
                info = proc.info
                mem = info.get("memory_info")
                procs.append({
                    "pid": info["pid"],
                    "name": info.get("name") or "",
                    "cpu_pct": round(info.get("cpu_percent") or 0.0, 1),
                    "ram_mb": round(mem.rss / 1048576, 1) if mem else 0.0,
                    "vram_mb": 0.0,
                    "status": info.get("status") or "",
                    "username": info.get("username") or "",
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        procs.sort(key=lambda x: x["cpu_pct"], reverse=True)
        return procs[:100]

    def get_gpu_process_map(self) -> Dict[int, float]:
        result: Dict[int, float] = {}
        if self._gpu_backend != "cuda" or not self._nvml_handles:
            return result
        pynvml = self._pynvml
        for handle in self._nvml_handles:
            for getter in (
                pynvml.nvmlDeviceGetComputeRunningProcesses,
                pynvml.nvmlDeviceGetGraphicsRunningProcesses,
            ):
                try:
                    for p in getter(handle):
                        vram = (p.usedGpuMemory or 0) / 1048576
                        result[p.pid] = result.get(p.pid, 0.0) + vram
                except Exception:
                    pass
        return result

    def get_disk_partitions(self) -> List[Dict]:
        parts = []
        for part in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(part.mountpoint)
                parts.append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / 1073741824, 1),
                    "used_gb": round(usage.used / 1073741824, 1),
                    "free_gb": round(usage.free / 1073741824, 1),
                    "pct": usage.percent,
                })
            except Exception:
                pass
        return parts
