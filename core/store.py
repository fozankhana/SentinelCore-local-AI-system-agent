import sqlite3
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional


class MetricsStore:
    def __init__(self, config):
        db_path = Path(config.storage.db_path).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(db_path)
        self.config = config
        self._write_lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS cpu_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    core_id INTEGER,
                    pct REAL,
                    mhz REAL,
                    temp_c REAL
                );
                CREATE TABLE IF NOT EXISTS mem_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    used_mb REAL,
                    total_mb REAL,
                    cached_mb REAL,
                    swap_mb REAL,
                    swap_total_mb REAL,
                    pct REAL
                );
                CREATE TABLE IF NOT EXISTS gpu_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    gpu_id INTEGER,
                    name TEXT,
                    util_pct REAL,
                    vram_used_mb REAL,
                    vram_total_mb REAL,
                    temp_c REAL,
                    watts REAL,
                    fan_pct REAL
                );
                CREATE TABLE IF NOT EXISTS net_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    iface TEXT,
                    bytes_in REAL,
                    bytes_out REAL,
                    packets_in INTEGER,
                    packets_out INTEGER
                );
                CREATE TABLE IF NOT EXISTS disk_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    device TEXT,
                    read_mbs REAL,
                    write_mbs REAL,
                    read_count INTEGER,
                    write_count INTEGER
                );
                CREATE TABLE IF NOT EXISTS process_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    pid INTEGER,
                    name TEXT,
                    cpu_pct REAL,
                    ram_mb REAL,
                    vram_mb REAL,
                    status TEXT,
                    username TEXT
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    severity TEXT,
                    message TEXT,
                    acknowledged INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    action TEXT,
                    target TEXT,
                    reason TEXT,
                    result TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_cpu_ts    ON cpu_samples(timestamp);
                CREATE INDEX IF NOT EXISTS idx_mem_ts    ON mem_samples(timestamp);
                CREATE INDEX IF NOT EXISTS idx_gpu_ts    ON gpu_samples(timestamp);
                CREATE INDEX IF NOT EXISTS idx_net_ts    ON net_samples(timestamp);
                CREATE INDEX IF NOT EXISTS idx_disk_ts   ON disk_samples(timestamp);
                CREATE INDEX IF NOT EXISTS idx_proc_ts   ON process_samples(timestamp);
                CREATE INDEX IF NOT EXISTS idx_alert_ts  ON alerts(timestamp);
                CREATE INDEX IF NOT EXISTS idx_audit_ts  ON audit_log(timestamp);
            """)

    def insert_metrics(self, metrics: Dict[str, Any]):
        ts = time.time()
        with self._write_lock:
            with self._connect() as conn:
                for core in metrics.get("cpu", {}).get("cores", []):
                    conn.execute(
                        "INSERT INTO cpu_samples (timestamp,core_id,pct,mhz,temp_c) VALUES(?,?,?,?,?)",
                        (ts, core.get("id"), core.get("pct"), core.get("mhz"), core.get("temp_c")),
                    )

                mem = metrics.get("memory", {})
                conn.execute(
                    "INSERT INTO mem_samples (timestamp,used_mb,total_mb,cached_mb,swap_mb,swap_total_mb,pct) VALUES(?,?,?,?,?,?,?)",
                    (ts, mem.get("used_mb"), mem.get("total_mb"), mem.get("cached_mb"),
                     mem.get("swap_mb"), mem.get("swap_total_mb"), mem.get("pct")),
                )

                for gpu in metrics.get("gpus", []):
                    conn.execute(
                        "INSERT INTO gpu_samples (timestamp,gpu_id,name,util_pct,vram_used_mb,vram_total_mb,temp_c,watts,fan_pct) VALUES(?,?,?,?,?,?,?,?,?)",
                        (ts, gpu.get("id"), gpu.get("name"), gpu.get("util_pct"),
                         gpu.get("vram_used_mb"), gpu.get("vram_total_mb"),
                         gpu.get("temp_c"), gpu.get("watts"), gpu.get("fan_pct")),
                    )

                net = metrics.get("network", {})
                for iface in net.get("interfaces", []):
                    conn.execute(
                        "INSERT INTO net_samples (timestamp,iface,bytes_in,bytes_out,packets_in,packets_out) VALUES(?,?,?,?,?,?)",
                        (ts, iface.get("name"), iface.get("bytes_in"), iface.get("bytes_out"),
                         iface.get("packets_in"), iface.get("packets_out")),
                    )

                for disk in metrics.get("disks", []):
                    conn.execute(
                        "INSERT INTO disk_samples (timestamp,device,read_mbs,write_mbs,read_count,write_count) VALUES(?,?,?,?,?,?)",
                        (ts, disk.get("device"), disk.get("read_mbs"), disk.get("write_mbs"),
                         disk.get("read_count"), disk.get("write_count")),
                    )

                for proc in metrics.get("processes", [])[:50]:
                    conn.execute(
                        "INSERT INTO process_samples (timestamp,pid,name,cpu_pct,ram_mb,vram_mb,status,username) VALUES(?,?,?,?,?,?,?,?)",
                        (ts, proc.get("pid"), proc.get("name"), proc.get("cpu_pct"),
                         proc.get("ram_mb"), proc.get("vram_mb"), proc.get("status"), proc.get("username")),
                    )

    def add_alert(self, severity: str, message: str):
        with self._write_lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO alerts (timestamp,severity,message) VALUES(?,?,?)",
                    (time.time(), severity, message),
                )

    def get_alerts(self, limit: int = 100, unacknowledged_only: bool = False) -> List[Dict]:
        with self._connect() as conn:
            q = "SELECT * FROM alerts"
            if unacknowledged_only:
                q += " WHERE acknowledged=0"
            q += " ORDER BY timestamp DESC LIMIT ?"
            return [dict(r) for r in conn.execute(q, (limit,)).fetchall()]

    def acknowledge_alert(self, alert_id: int):
        with self._write_lock:
            with self._connect() as conn:
                conn.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (alert_id,))

    def add_audit(self, action: str, target: str, reason: str, result: str):
        with self._write_lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO audit_log (timestamp,action,target,reason,result) VALUES(?,?,?,?,?)",
                    (time.time(), action, target, reason, result),
                )

    def get_audit(self, limit: int = 200) -> List[Dict]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()]

    def get_history(self, table: str, hours: int = 1, extra_filter: Optional[str] = None) -> List[Dict]:
        allowed = {
            "cpu_samples", "mem_samples", "gpu_samples",
            "net_samples", "disk_samples", "process_samples",
        }
        if table not in allowed:
            return []
        since = time.time() - hours * 3600
        q = f"SELECT * FROM {table} WHERE timestamp >= ? ORDER BY timestamp ASC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(q, (since,)).fetchall()]

    def get_latest_cpu(self) -> List[Dict]:
        with self._connect() as conn:
            ts = conn.execute("SELECT MAX(timestamp) FROM cpu_samples").fetchone()[0]
            if not ts:
                return []
            return [dict(r) for r in conn.execute(
                "SELECT * FROM cpu_samples WHERE timestamp=?", (ts,)
            ).fetchall()]

    def get_latest_mem(self) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mem_samples ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def get_latest_gpu(self) -> List[Dict]:
        with self._connect() as conn:
            ts = conn.execute("SELECT MAX(timestamp) FROM gpu_samples").fetchone()[0]
            if not ts:
                return []
            return [dict(r) for r in conn.execute(
                "SELECT * FROM gpu_samples WHERE timestamp=?", (ts,)
            ).fetchall()]

    def get_latest_processes(self, limit: int = 10) -> List[Dict]:
        with self._connect() as conn:
            ts = conn.execute("SELECT MAX(timestamp) FROM process_samples").fetchone()[0]
            if not ts:
                return []
            return [dict(r) for r in conn.execute(
                "SELECT name, cpu_pct, ram_mb, vram_mb, status FROM process_samples WHERE timestamp=? LIMIT ?",
                (ts, limit),
            ).fetchall()]

    def cleanup_old_data(self):
        now = time.time()
        cutoff_1s = now - self.config.storage.retention_1s_hrs * 3600
        with self._write_lock:
            with self._connect() as conn:
                for tbl in ["cpu_samples", "mem_samples", "gpu_samples",
                             "net_samples", "disk_samples", "process_samples"]:
                    conn.execute(f"DELETE FROM {tbl} WHERE timestamp < ?", (cutoff_1s,))
