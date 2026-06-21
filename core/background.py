import os
import platform
import subprocess
import threading
import time
import logging
from pathlib import Path
from typing import Any, Dict, List

import psutil

log = logging.getLogger("background")

_SYSTEM_ACCOUNTS = {"system", "local service", "network service", "nt authority\\system",
                    "nt authority\\local service", "nt authority\\network service"}

_INTERESTING_PORTS = {
    20, 21, 22, 23, 25, 53, 80, 110, 135, 137, 138, 139, 143,
    443, 445, 993, 995, 1080, 1433, 1521, 3306, 3389, 5432, 5900,
    6379, 8080, 8443, 9200, 27017,
}


class BackgroundAgent:
    def __init__(self):
        self._task_cache: List[Dict] = []
        self._task_ts: float = 0.0
        self._task_lock = threading.Lock()
        self._refresh_tasks_async()

    def collect(self) -> Dict[str, Any]:
        return {
            "timestamp": time.time(),
            "hidden_procs": self._collect_hidden(),
            "services": self._collect_services(),
            "startup": self._collect_startup(),
            "listeners": self._collect_listeners(),
            "scheduled_tasks": self._get_cached_tasks(),
            "outbound_silent": self._collect_silent_outbound(),
        }

    # ── Hidden / background processes ──────────────────────────────────────
    def _collect_hidden(self) -> List[Dict]:
        procs = []
        for p in psutil.process_iter(
            ["pid", "name", "username", "cpu_percent", "memory_info",
             "status", "ppid", "create_time"],
            ad_value=None,
        ):
            try:
                info = p.info
                username = (info.get("username") or "").lower()
                name     = (info.get("name") or "")
                mem      = info.get("memory_info")

                is_sys = any(acct in username for acct in _SYSTEM_ACCOUNTS)
                is_svc = "svchost" in name.lower() or "lsass" in name.lower() or "services.exe" in name.lower()

                if not (is_sys or is_svc):
                    continue

                procs.append({
                    "pid":      info["pid"],
                    "name":     name,
                    "username": info.get("username") or "",
                    "cpu_pct":  round(info.get("cpu_percent") or 0.0, 1),
                    "ram_mb":   round(mem.rss / 1048576, 1) if mem else 0.0,
                    "ppid":     info.get("ppid"),
                    "status":   info.get("status") or "",
                    "is_sys":   is_sys,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        procs.sort(key=lambda x: x["ram_mb"], reverse=True)
        return procs[:80]

    # ── All running processes (ungrouped, for the "all background" view) ───
    def collect_all_procs(self) -> List[Dict]:
        procs = []
        for p in psutil.process_iter(
            ["pid", "name", "username", "cpu_percent", "memory_info", "status", "ppid"],
            ad_value=None,
        ):
            try:
                info = p.info
                mem  = info.get("memory_info")
                username = (info.get("username") or "")
                procs.append({
                    "pid":      info["pid"],
                    "name":     info.get("name") or "",
                    "username": username,
                    "cpu_pct":  round(info.get("cpu_percent") or 0.0, 1),
                    "ram_mb":   round(mem.rss / 1048576, 1) if mem else 0.0,
                    "status":   info.get("status") or "",
                    "ppid":     info.get("ppid"),
                    "is_sys":   username.lower().split("\\")[-1] in
                                {"system", "local service", "network service"},
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        procs.sort(key=lambda x: x["ram_mb"], reverse=True)
        return procs

    # ── Windows services ───────────────────────────────────────────────────
    def _collect_services(self) -> List[Dict]:
        if not hasattr(psutil, "win_service_iter"):
            return []
        services = []
        for svc in psutil.win_service_iter():
            try:
                d = svc.as_dict()
                services.append({
                    "name":         d.get("name", ""),
                    "display_name": d.get("display_name", ""),
                    "status":       d.get("status", ""),
                    "start_type":   d.get("start_type", ""),
                    "pid":          d.get("pid"),
                    "username":     d.get("username", ""),
                    "description":  d.get("description", ""),
                    "binpath":      d.get("binpath", ""),
                })
            except Exception:
                continue
        services.sort(key=lambda s: (s["status"] != "running", s["display_name"].lower()))
        return services

    # ── Startup programs ───────────────────────────────────────────────────
    def _collect_startup(self) -> List[Dict]:
        items = []

        if platform.system() == "Windows":
            try:
                import winreg
                reg_keys = [
                    (winreg.HKEY_CURRENT_USER,
                     r"Software\Microsoft\Windows\CurrentVersion\Run", "HKCU"),
                    (winreg.HKEY_CURRENT_USER,
                     r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "HKCU RunOnce"),
                    (winreg.HKEY_LOCAL_MACHINE,
                     r"Software\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
                    (winreg.HKEY_LOCAL_MACHINE,
                     r"Software\Microsoft\Windows\CurrentVersion\RunOnce", "HKLM RunOnce"),
                    (winreg.HKEY_LOCAL_MACHINE,
                     r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Run", "HKLM WOW64"),
                ]
                for hive, path, label in reg_keys:
                    try:
                        key = winreg.OpenKey(hive, path)
                        i = 0
                        while True:
                            try:
                                name, val, _ = winreg.EnumValue(key, i)
                                items.append({
                                    "name":     name,
                                    "command":  val,
                                    "location": label,
                                    "type":     "registry",
                                    "enabled":  True,
                                })
                                i += 1
                            except OSError:
                                break
                        winreg.CloseKey(key)
                    except Exception:
                        pass
            except ImportError:
                pass

            startup_dirs = [
                os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"),
                os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs\Startup"),
            ]
            for folder in startup_dirs:
                try:
                    for f in Path(folder).iterdir():
                        if f.suffix.lower() not in (".ini", ".db"):
                            items.append({
                                "name":     f.stem,
                                "command":  str(f),
                                "location": "Startup Folder",
                                "type":     "startup_folder",
                                "enabled":  True,
                            })
                except Exception:
                    pass

        return items

    # ── Listening ports ────────────────────────────────────────────────────
    def _collect_listeners(self) -> List[Dict]:
        listeners = []
        seen = set()
        try:
            for conn in psutil.net_connections(kind="inet"):
                if conn.status != "LISTEN":
                    continue
                port = conn.laddr.port if conn.laddr else 0
                if port in seen:
                    continue
                seen.add(port)
                try:
                    proc_name = psutil.Process(conn.pid).name() if conn.pid else ""
                except Exception:
                    proc_name = ""
                listeners.append({
                    "pid":       conn.pid,
                    "proc":      proc_name,
                    "addr":      conn.laddr.ip if conn.laddr else "",
                    "port":      port,
                    "public":    (conn.laddr.ip in ("0.0.0.0", "::")) if conn.laddr else False,
                    "notable":   port in _INTERESTING_PORTS,
                })
        except Exception:
            pass
        return sorted(listeners, key=lambda x: x["port"])

    # ── Silent outbound connections ─────────────────────────────────────────
    def _collect_silent_outbound(self) -> List[Dict]:
        result = []
        try:
            user_procs = {}
            for p in psutil.process_iter(["pid", "name", "username"], ad_value=None):
                try:
                    user_procs[p.pid] = {
                        "name": p.info.get("name") or "",
                        "username": p.info.get("username") or "",
                    }
                except Exception:
                    pass

            seen = set()
            for conn in psutil.net_connections(kind="inet"):
                if conn.status != "ESTABLISHED" or not conn.raddr:
                    continue
                key = (conn.pid, conn.raddr.ip)
                if key in seen:
                    continue
                seen.add(key)
                pinfo = user_procs.get(conn.pid, {})
                uname = pinfo.get("username", "").lower()
                is_sys = any(a in uname for a in _SYSTEM_ACCOUNTS)
                result.append({
                    "pid":      conn.pid,
                    "proc":     pinfo.get("name", ""),
                    "username": pinfo.get("username", ""),
                    "remote":   f"{conn.raddr.ip}:{conn.raddr.port}",
                    "is_sys":   is_sys,
                })
        except Exception:
            pass
        return result[:60]

    # ── Scheduled tasks (cached, refreshed async every 60s) ────────────────
    def _get_cached_tasks(self) -> List[Dict]:
        with self._task_lock:
            return list(self._task_cache)

    def _refresh_tasks_async(self):
        t = threading.Thread(target=self._fetch_tasks, daemon=True)
        t.start()

    def _fetch_tasks(self):
        while True:
            tasks = []
            try:
                if platform.system() == "Windows":
                    r = subprocess.run(
                        ["schtasks", "/query", "/fo", "csv", "/v"],
                        capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace",
                    )
                    lines = [l for l in r.stdout.splitlines() if l.strip()]
                    if len(lines) > 1:
                        raw_headers = [h.strip('"') for h in lines[0].split('","')]
                        for line in lines[1:]:
                            cols = [c.strip('"') for c in line.split('","')]
                            if len(cols) < 3:
                                continue
                            row = dict(zip(raw_headers, cols))
                            status = row.get("Status", "").strip()
                            if not status or status.lower() in ("disabled", ""):
                                continue
                            tname = row.get("TaskName", "").strip()
                            if not tname or tname.startswith("\\Microsoft\\Windows\\"):
                                continue
                            tasks.append({
                                "name":     tname,
                                "status":   status,
                                "next_run": row.get("Next Run Time", "N/A"),
                                "last_run": row.get("Last Run Time", "N/A"),
                                "author":   row.get("Author", ""),
                                "task_to_run": row.get("Task To Run", ""),
                            })
            except Exception as e:
                log.debug("schtasks error: %s", e)
            with self._task_lock:
                self._task_cache = tasks[:60]
            time.sleep(60)
