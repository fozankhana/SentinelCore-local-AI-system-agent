import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None


@dataclass
class AgentConfig:
    poll_interval_ms: int = 1000
    dashboard_port: int = 4080
    dashboard_host: str = "127.0.0.1"
    log_level: str = "info"
    audit_log: bool = True


@dataclass
class StorageConfig:
    db_path: str = "~/.sentinelcore/metrics.db"
    retention_1s_hrs: int = 24
    retention_1m_days: int = 30
    retention_1h_days: int = 365


@dataclass
class GPUConfig:
    backend: str = "auto"
    enforcement: bool = True
    check_interval_ms: int = 2000


@dataclass
class AIConfig:
    enabled: bool = False
    backend: str = "ollama"
    endpoint: str = "http://localhost:11434"
    model: str = "llama3.2:3b"
    interval: int = 300


@dataclass
class AlertsConfig:
    cpu_pct_warn: int = 85
    ram_pct_warn: int = 90
    gpu_temp_warn_c: int = 85
    vram_pct_warn: int = 95
    disk_smart_fail: bool = True


@dataclass
class AlertWebhookConfig:
    enabled: bool = False
    url: str = ""
    method: str = "POST"
    severity: List[str] = field(default_factory=lambda: ["warning", "critical"])


@dataclass
class EnforceRule:
    name: str = ""
    exe: str = ""
    gpu_enforce: bool = False
    gpu_backend: str = "auto"
    max_cpu_pct: Optional[int] = None
    max_ram_mb: Optional[int] = None
    max_vram_mb: Optional[int] = None
    action: str = "alert"
    browser_flags: List[str] = field(default_factory=list)
    auto_restart: bool = False


@dataclass
class BlockRule:
    exe: str = ""
    reason: str = ""
    path: str = ""


@dataclass
class ScheduleRule:
    exe: str = ""
    allowed_hours: str = ""


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    gpu: GPUConfig = field(default_factory=GPUConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    alert_webhook: AlertWebhookConfig = field(default_factory=AlertWebhookConfig)
    enforce: List[EnforceRule] = field(default_factory=list)
    block: List[BlockRule] = field(default_factory=list)
    schedule: List[ScheduleRule] = field(default_factory=list)


def _parse_toml(path: str) -> dict:
    if tomllib is None:
        raise RuntimeError("TOML parser not available. Install: pip install tomli")
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_config(path: Optional[str] = None) -> "Config":
    if path is None:
        candidates = [
            Path.home() / ".sentinelcore" / "config.toml",
            Path("config.toml"),
        ]
        for c in candidates:
            if c.exists():
                path = str(c)
                break

    config = Config()

    if path is None or not Path(path).exists():
        return config

    data = _parse_toml(path)

    if "agent" in data:
        a = data["agent"]
        config.agent = AgentConfig(
            poll_interval_ms=a.get("poll_interval_ms", 1000),
            dashboard_port=a.get("dashboard_port", 4080),
            dashboard_host=a.get("dashboard_host", "127.0.0.1"),
            log_level=a.get("log_level", "info"),
            audit_log=a.get("audit_log", True),
        )

    if "storage" in data:
        s = data["storage"]
        config.storage = StorageConfig(
            db_path=s.get("db_path", "~/.sentinelcore/metrics.db"),
            retention_1s_hrs=s.get("retention_1s_hrs", 24),
            retention_1m_days=s.get("retention_1m_days", 30),
            retention_1h_days=s.get("retention_1h_days", 365),
        )

    if "gpu" in data:
        g = data["gpu"]
        config.gpu = GPUConfig(
            backend=g.get("backend", "auto"),
            enforcement=g.get("enforcement", True),
            check_interval_ms=g.get("check_interval_ms", 2000),
        )

    if "ai" in data:
        ai = data["ai"]
        config.ai = AIConfig(
            enabled=ai.get("enabled", False),
            backend=ai.get("backend", "ollama"),
            endpoint=ai.get("endpoint", "http://localhost:11434"),
            model=ai.get("model", "llama3.2:3b"),
            interval=ai.get("interval", 300),
        )

    if "alerts" in data:
        al = data["alerts"]
        config.alerts = AlertsConfig(
            cpu_pct_warn=al.get("cpu_pct_warn", 85),
            ram_pct_warn=al.get("ram_pct_warn", 90),
            gpu_temp_warn_c=al.get("gpu_temp_warn_c", 85),
            vram_pct_warn=al.get("vram_pct_warn", 95),
            disk_smart_fail=al.get("disk_smart_fail", True),
        )
        if "webhook" in al:
            wh = al["webhook"]
            config.alert_webhook = AlertWebhookConfig(
                enabled=wh.get("enabled", False),
                url=wh.get("url", ""),
                method=wh.get("method", "POST"),
                severity=wh.get("severity", ["warning", "critical"]),
            )

    for rule in data.get("enforce", []):
        config.enforce.append(EnforceRule(
            name=rule.get("name", ""),
            exe=rule.get("exe", ""),
            gpu_enforce=rule.get("gpu_enforce", False),
            gpu_backend=rule.get("gpu_backend", "auto"),
            max_cpu_pct=rule.get("max_cpu_pct"),
            max_ram_mb=rule.get("max_ram_mb"),
            max_vram_mb=rule.get("max_vram_mb"),
            action=rule.get("action", "alert"),
            browser_flags=rule.get("browser_flags", []),
            auto_restart=rule.get("auto_restart", False),
        ))

    for rule in data.get("block", []):
        config.block.append(BlockRule(
            exe=rule.get("exe", ""),
            reason=rule.get("reason", ""),
            path=rule.get("path", ""),
        ))

    for rule in data.get("schedule", []):
        config.schedule.append(ScheduleRule(
            exe=rule.get("exe", ""),
            allowed_hours=rule.get("allowed_hours", ""),
        ))

    return config
