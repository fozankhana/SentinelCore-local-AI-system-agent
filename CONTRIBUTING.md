# Contributing to SentinelCore

## Core Rules

1. **No cloud dependencies** — PRs that add any non-optional cloud feature will not be merged.
2. **No telemetry** — The agent must make zero outbound connections in its default configuration.
3. **Auditable actions** — Every agent action must be logged to the local audit trail.
4. **Minimal footprint** — The agent itself must stay under 100MB RAM and 1% CPU at idle.
5. **GPU enforcement must be explicit** — The agent never changes GPU assignment without a config rule.

## Setup

```bash
git clone https://github.com/yourusername/sentinelcore
cd sentinelcore
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.toml config.toml
python sentinelcore.py
```

## Testing

Run on a real machine — the agent tests itself by monitoring the system it runs on.

For unit tests of individual collectors:

```bash
python -c "from core.collector import MetricsCollector; from core.config import load_config; c = MetricsCollector(load_config()); print(c.collect())"
```

## PR Guidelines

- Keep PRs focused on one feature or fix
- Test on at least two platforms if touching cross-platform code
- All new enforcement actions must write to the audit log
- Update `config.example.toml` if adding new config keys
