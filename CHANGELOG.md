# Changelog

All notable changes to SentinelCore are documented here.

---

## [1.0.0] — 2026-06-28

### Added
- **Windows installer** — Inno Setup `.exe` wrapping the PyInstaller bundle;
  creates Start Menu shortcuts, optional desktop icon, optional Windows startup entry
- **macOS .app bundle** — `build.py --platform macos` produces `SentinelCore.app`
  with an Info.plist and a launcher shell script that starts the server and opens
  `http://localhost:4080` in the default browser
- **`sentinelcore.spec`** — PyInstaller spec that bundles `dashboard/templates`,
  `dashboard/static`, and all hidden imports into a single redistributable folder
- **`build.py`** — cross-platform build script; runs PyInstaller, optionally runs
  Inno Setup (Windows), or assembles the `.app` structure (macOS)
- **`CHANGELOG.md`** — this file

### Changed
- `requirements.txt` — removed unused `flask-sock`, added optional-dependency notes
- `config.example.toml` — documents all options added in v0.3–v0.7 (`auto_restart`,
  `gpu_index`, `path` block rule)
- `README.md` — installer section, correct repo URL, v1.0 roadmap entry checked
- Dashboard nav — deduplicated GPU icons, sidebar footer shows current version

---

## [0.7.0] — 2026-06-27

### Added
- `core/gpu_router.py` — `GPURouter` with `list_gpus()`, `pick_least_loaded()`,
  `build_env(gpu_index)`, `route_pid()`, `routing_table()`
- `collector.get_per_gpu_process_map()` — `{gpu_id: {pid: vram_mb}}` for CUDA + ROCm
- `gpu_index: int = -1` on `EnforceRule` — pin an app to a specific GPU in config
- Enforcer `_migrate_to_gpu` now injects the correct device index (not always `"0"`)
- `/multi-gpu` dashboard page — per-GPU cards + routing table + one-click reroute buttons
- `GET /api/gpu/list`, `GET /api/gpu/routing`, `POST /api/processes/<pid>/route`

---

## [0.6.0] — 2026-06-27

### Added
- `core/browser_monitor.py` — detects Chrome, Edge, Brave, Opera, Vivaldi, Firefox;
  probes CDP (`--remote-debugging-port`) for live tabs; counts renderer child processes;
  reads Chromium SQLite history as fallback
- `/browsers` dashboard page — summary cards, CDP status banner, per-browser tab rows
- `GET /api/browsers`

---

## [0.5.0] — 2026-06-25

### Added
- `core/ai_agent.py` — full rewrite: streaming generators, 10-pair conversation history,
  rich context (top processes, GPU, alerts, audit), Ollama + llama.cpp + LM Studio
- `store.get_latest_processes(limit)` for AI context
- Streaming Q&A via SSE (`GET /api/ai/stream`)
- Proactive alert when AI summary flags critical conditions
- `/ai` dashboard page — backend status, auto-summary panel, streaming chat,
  quick-prompt chips, typewriter cursor effect
- `GET /api/ai/models`, `GET /api/ai/status`, `POST /api/ai/history/clear`,
  `POST /api/ai/summary/refresh`

---

## [0.4.0] — 2026-06-24

### Added
- AMD ROCm/HIP enforcement: `HIP_VISIBLE_DEVICES`, `ROCR_VISIBLE_DEVICES`
- Apple Metal enforcement: `CHROME_METAL_FLAGS` for Chromium browsers on macOS
- Intel Arc/oneAPI enforcement: `SYCL_DEVICE_FILTER`, `ONEAPI_DEVICE_SELECTOR`
- Backend-aware GPU migration — detects CUDA/ROCm/Metal/Arc at runtime
- `gpu_backend` field on `EnforceRule`; `_get_rocm_process_map()` via `rocm-smi --json`
- GPU backend badge on the GPU dashboard page (`#gpu-backend-badge`)
- `snapshot["gpu_backend"]` in SSE stream

---

## [0.3.0] — 2026-06-23

### Added
- `core/job_objects.py` — Windows Job Objects via ctypes: `apply_memory_cap()`,
  `apply_cpu_rate()`, `release_cap()`, `list_capped()`; graceful fallback for
  processes already in a Job Object (ERROR_ACCESS_DENIED = 5)
- Auto-restart watcher: background daemon thread relaunches whitelisted processes
  that die unexpectedly
- Path-based blocklist: `path` field on `BlockRule`
- `auto_restart` field on `EnforceRule`
- Dashboard: `POST /api/processes/<pid>/cap`, `POST /api/processes/<pid>/release_cap`,
  `POST /api/processes/<pid>/affinity`, `GET /api/enforcement/rules`

---

## [0.2.0] — 2026-06-22

### Added
- GPU enforcement engine: NVIDIA CUDA + browser GPU flag injection
- Per-process VRAM tracking via NVML
- GPU dashboard page with enforcement board and process attribution table
- Automatic GPU migration: re-launch CPU-falling-back processes with GPU flags
- `chrome.exe`, `msedge.exe`, `brave.exe`, `firefox.exe` enforcement profiles
- Multi-GPU detection groundwork in collector

---

## [0.1.0] — 2026-06-21

### Added
- Core metrics collector: CPU (per-core), RAM, GPU, network (per-interface),
  disk I/O, process list — all via `psutil` + `pynvml`
- SQLite time-series storage with WAL mode, configurable retention
- Flask + SSE web dashboard at `http://localhost:4080`
- Alert system with configurable per-metric thresholds
- Audit log for all agent actions
- Background agent: hidden processes, services, startup items, open ports
- AI agent skeleton (Ollama integration)
- TOML configuration loader with `~/.sentinelcore/config.toml` search
