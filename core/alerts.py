import logging
import threading
import time
from typing import Any, Dict

log = logging.getLogger("alerts")


class AlertSystem:
    def __init__(self, config, store):
        self.config = config
        self.store = store
        self._last_fired: Dict[str, float] = {}
        self._cooldown = 60

    def check(self, metrics: Dict[str, Any]):
        cfg = self.config.alerts

        cpu_pct = metrics.get("cpu", {}).get("total_pct", 0)
        if cpu_pct > cfg.cpu_pct_warn:
            self._fire("warning", f"High CPU usage: {cpu_pct:.1f}%", "cpu_high")

        mem = metrics.get("memory", {})
        mem_pct = mem.get("pct", 0)
        if mem_pct > 97:
            self._fire("critical", f"Critical RAM pressure: {mem_pct:.1f}%", "mem_critical")
        elif mem_pct > cfg.ram_pct_warn:
            self._fire("warning", f"High RAM usage: {mem_pct:.1f}%", "mem_high")

        for gpu in metrics.get("gpus", []):
            gid = gpu.get("id", 0)
            temp = gpu.get("temp_c")
            if temp:
                if temp > 95:
                    self._fire("critical", f"GPU {gid} critical temperature: {temp}°C", f"gpu_temp_crit_{gid}")
                elif temp > cfg.gpu_temp_warn_c:
                    self._fire("warning", f"GPU {gid} high temperature: {temp}°C", f"gpu_temp_{gid}")

            vram_total = gpu.get("vram_total_mb") or 0
            vram_used = gpu.get("vram_used_mb") or 0
            if vram_total > 0:
                vram_pct = vram_used / vram_total * 100
                if vram_pct > cfg.vram_pct_warn:
                    self._fire("warning", f"GPU {gid} VRAM near full: {vram_pct:.1f}%", f"vram_{gid}")

    def _fire(self, severity: str, message: str, key: str):
        now = time.time()
        if now - self._last_fired.get(key, 0) < self._cooldown:
            return
        self._last_fired[key] = now
        log.warning("ALERT [%s]: %s", severity.upper(), message)
        self.store.add_alert(severity, message)
        self._maybe_webhook(severity, message)

    def _maybe_webhook(self, severity: str, message: str):
        wh = self.config.alert_webhook
        if not wh.enabled or not wh.url or severity not in wh.severity:
            return
        threading.Thread(
            target=self._send_webhook,
            args=(wh, severity, message),
            daemon=True,
        ).start()

    @staticmethod
    def _send_webhook(wh, severity: str, message: str):
        try:
            import requests
            payload = {"severity": severity, "message": message, "timestamp": time.time()}
            if wh.method.upper() == "POST":
                requests.post(wh.url, json=payload, timeout=5)
            else:
                requests.get(wh.url, params=payload, timeout=5)
        except Exception as e:
            log.debug("Webhook failed: %s", e)
