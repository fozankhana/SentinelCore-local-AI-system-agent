import logging
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

import psutil

log = logging.getLogger("browser_monitor")

# Executable name → display label
_BROWSER_EXE: Dict[str, str] = {
    # Windows
    "chrome.exe":   "Chrome",
    "msedge.exe":   "Edge",
    "brave.exe":    "Brave",
    "opera.exe":    "Opera",
    "vivaldi.exe":  "Vivaldi",
    "firefox.exe":  "Firefox",
    # Linux
    "google-chrome": "Chrome",
    "google-chrome-stable": "Chrome",
    "chromium":     "Chromium",
    "chromium-browser": "Chromium",
    "microsoft-edge": "Edge",
    "brave-browser": "Brave",
    "firefox":      "Firefox",
    # macOS
    "google chrome": "Chrome",
}

_CDP_PROBE_PORTS = list(range(9222, 9232))
_CDP_TTL = 4.0  # seconds before re-probing a port


class BrowserMonitor:
    def __init__(self):
        self._cdp_cache: Dict[int, List[Dict]] = {}
        self._cdp_ts: Dict[int, float] = {}

    def collect(self) -> List[Dict]:
        results = []
        seen: set = set()

        for proc in psutil.process_iter(
            ["pid", "name", "cmdline", "cpu_percent", "memory_info"], ad_value=None
        ):
            try:
                exe = (proc.info["name"] or "").lower()
                label = _BROWSER_EXE.get(exe)
                if not label:
                    continue

                cmdline = proc.info.get("cmdline") or []

                # Skip renderer / GPU / utility child processes
                if any(a.startswith("--type=") for a in cmdline):
                    continue

                pid = proc.info["pid"]
                if pid in seen:
                    continue
                seen.add(pid)

                mem = proc.info.get("memory_info")
                ram_mb = round(mem.rss / 1048576, 1) if mem else 0.0

                # --- CDP ---
                cdp_port = self._cmdline_cdp_port(cmdline)
                tabs: List[Dict] = []
                cdp_ok = False

                if cdp_port:
                    tabs = self._probe_cdp(cdp_port)
                    cdp_ok = bool(tabs)
                else:
                    for port in _CDP_PROBE_PORTS:
                        tabs = self._probe_cdp(port)
                        if tabs:
                            cdp_port = port
                            cdp_ok = True
                            break

                # --- renderer child count as tab estimate ---
                renderer_count = _count_renderers(pid, exe)

                # --- history fallback (only when CDP unavailable) ---
                recent_urls: List[Dict] = []
                if not cdp_ok and label != "Firefox":
                    recent_urls = _read_chromium_history(label, limit=25)

                results.append({
                    "pid": pid,
                    "name": label,
                    "exe": exe,
                    "ram_mb": ram_mb,
                    "cpu_pct": round(proc.info.get("cpu_percent") or 0.0, 1),
                    "cdp_port": cdp_port,
                    "cdp_connected": cdp_ok,
                    "tabs": tabs,
                    "renderer_count": renderer_count,
                    "recent_urls": recent_urls,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return results

    # ── CDP ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _cmdline_cdp_port(cmdline: List[str]) -> Optional[int]:
        for arg in cmdline:
            if arg.startswith("--remote-debugging-port="):
                try:
                    return int(arg.split("=", 1)[1])
                except ValueError:
                    pass
        return None

    def _probe_cdp(self, port: int) -> List[Dict]:
        now = time.time()
        cached_ts = self._cdp_ts.get(port, 0)
        if now - cached_ts < _CDP_TTL:
            return self._cdp_cache.get(port, [])

        try:
            import requests
            r = requests.get(f"http://localhost:{port}/json", timeout=0.8)
            r.raise_for_status()
            raw = r.json()
            tabs = [
                {
                    "id":    t.get("id", ""),
                    "title": t.get("title", ""),
                    "url":   t.get("url", ""),
                    "type":  t.get("type", ""),
                }
                for t in raw
                if t.get("type") == "page"
                and not (t.get("url") or "").startswith("devtools://")
            ]
            self._cdp_cache[port] = tabs
            self._cdp_ts[port] = now
            return tabs
        except Exception:
            self._cdp_cache[port] = []
            self._cdp_ts[port] = now
            return []


# ── Helpers ──────────────────────────────────────────────────────────────────

def _count_renderers(parent_pid: int, exe_lower: str) -> int:
    count = 0
    try:
        for child in psutil.Process(parent_pid).children(recursive=True):
            try:
                if child.name().lower() == exe_lower:
                    cmdline = child.cmdline()
                    if any(a.startswith("--type=renderer") for a in cmdline):
                        count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return count


def _read_chromium_history(label: str, limit: int = 25) -> List[Dict]:
    for path in _history_paths(label):
        if not path.exists():
            continue
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                tmp = f.name
            shutil.copy2(str(path), tmp)
            conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            rows = conn.execute(
                "SELECT url, title FROM urls ORDER BY last_visit_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
            return [{"url": r[0], "title": r[1] or _short_url(r[0])} for r in rows]
        except Exception as e:
            log.debug("History read error (%s): %s", label, e)
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    return []


def _history_paths(label: str) -> List[Path]:
    local = os.environ.get("LOCALAPPDATA", "")
    roam  = os.environ.get("APPDATA", "")
    home  = Path.home()

    if label == "Chrome":
        return [
            Path(local) / "Google/Chrome/User Data/Default/History",
            home / ".config/google-chrome/Default/History",
            home / "Library/Application Support/Google/Chrome/Default/History",
        ]
    if label == "Edge":
        return [Path(local) / "Microsoft/Edge/User Data/Default/History"]
    if label == "Brave":
        return [
            Path(local) / "BraveSoftware/Brave-Browser/User Data/Default/History",
            home / ".config/BraveSoftware/Brave-Browser/Default/History",
        ]
    if label in ("Opera",):
        return [Path(roam) / "Opera Software/Opera Stable/History"]
    if label == "Vivaldi":
        return [Path(local) / "Vivaldi/User Data/Default/History"]
    if label == "Chromium":
        return [
            home / ".config/chromium/Default/History",
            Path(local) / "Chromium/User Data/Default/History",
        ]
    return []


def _short_url(url: str) -> str:
    try:
        from urllib.parse import urlparse
        p = urlparse(url)
        return p.netloc or url[:60]
    except Exception:
        return url[:60]
