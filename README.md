<p align="center">
  <img src="https://img.shields.io/badge/LOCAL%20ONLY-NO%20CLOUD-red?style=for-the-badge&logoColor=white" />
  <img src="https://img.shields.io/badge/GPU%20ENFORCED-CUDA%20%7C%20ROCm%20%7C%20Metal-76b900?style=for-the-badge" />
  <img src="https://img.shields.io/badge/PRIVACY-ZERO%20TELEMETRY-blue?style=for-the-badge" />
  <img src="https://img.shields.io/badge/DASHBOARD-WEB%20UI-orange?style=for-the-badge" />
</p>
<h1 align="center">🛡️ SentinelCore</h1>
<p align="center"><b>A privacy-first, GPU-enforced local AI system agent that watches your machine, controls what runs and where, and surfaces everything through a live web dashboard — with zero cloud dependency.</b></p>

---

## Table of Contents
1. [What This Is](#what-this-is)
2. [Core Philosophy](#core-philosophy)
3. [Feature Overview](#feature-overview)
4. [GPU Enforcement Engine](#gpu-enforcement-engine)
5. [Process & App Controller](#process--app-controller)
6. [System Monitoring Agent](#system-monitoring-agent)
7. [Web Dashboard](#web-dashboard)
8. [AI Agent Layer](#ai-agent-layer)
9. [Architecture](#architecture)
10. [Hardware Requirements](#hardware-requirements)
11. [Installation](#installation)
12. [Configuration Reference](#configuration-reference)
13. [GPU Enforcement Rules — Full Spec](#gpu-enforcement-rules--full-spec)
14. [Dashboard Pages & Panels](#dashboard-pages--panels)
15. [Alert System](#alert-system)
16. [Roadmap](#roadmap)
17. [Contributing](#contributing)
18. [License](#license)

---

## What This Is

**SentinelCore** is a local AI-powered system automation agent. You run it once. It runs forever in the background, watching your machine, making decisions, and enforcing rules — all without touching the internet.

It does four things that no existing tool does together:

1. **Monitors** every system event in real time — CPU, RAM, GPU, network, disk I/O, and running processes.
2. **Enforces** GPU usage for any app or browser you specify — redirecting their compute away from CPU/RAM and onto your GPU, automatically.
3. **Controls** which software is allowed to run, when it can run, how much resource it can consume, and kills or throttles anything that violates your rules.
4. **Surfaces everything** through a live local web dashboard you open in any browser — no Electron, no app install, just `http://localhost:4080`.

Everything is local. Nothing leaves your machine. No accounts. No API keys required (you can optionally connect a local LLM for natural language alerts and suggestions).

---

## Core Philosophy

| Principle | What it means in practice |
|---|---|
| **GPU-first** | Any workload you designate must run on GPU. If it tries to fall back to CPU, SentinelCore blocks it or migrates it. |
| **Zero cloud** | No metrics, logs, or system data ever leave the device. Dashboard is served on localhost only. |
| **Zero telemetry** | No crash reports, no usage analytics, no update pings. The binary makes no outbound connections unless you configure one explicitly. |
| **Explicit over automatic** | SentinelCore enforces exactly what you tell it to. It does not make decisions you haven't approved via config. |
| **Auditable** | Every action taken by the agent is written to a local, human-readable audit log. You can see exactly what it did and why. |
| **Minimal footprint** | The agent itself uses under 80MB RAM and <0.5% CPU at idle. It does not become the problem it is trying to solve. |

---

## Feature Overview

### 🔴 GPU Enforcement Engine
- Force specific browsers (Chrome, Firefox, Edge, Brave, Arc) to render entirely on GPU
- Force specific applications (VS Code, Blender, DaVinci Resolve, games, ML training scripts) to use GPU compute
- Per-app GPU VRAM quota — cap how much VRAM a single process can claim
- Automatic GPU migration: detect when a whitelisted process is running on CPU and re-launch it with correct GPU flags
- Multi-GPU routing: assign different apps to different GPUs on multi-GPU systems
- Supports NVIDIA (CUDA), AMD (ROCm/HIP), Apple Silicon (Metal), and Intel Arc (oneAPI)
- Real-time GPU utilization, VRAM usage, temperature, and power draw per process

### 🟡 System Monitoring Agent
- Per-core CPU usage, frequency, temperature, and throttle state
- Per-process RAM, swap, and memory leak detection
- GPU utilization, VRAM, encoder/decoder usage, temperature, power draw (per GPU, per process)
- Network: per-interface bytes in/out, active connections, DNS queries, connection destinations
- Disk: read/write throughput, queue depth, latency, SMART health status
- Process tree: full parent/child hierarchy, CPU/RAM/GPU per process, start time, open files, open ports
- All metrics stored locally in a time-series SQLite database — queryable, exportable

### 🟢 Process & App Controller
- Allowlist / blocklist for any executable by name, path, or hash
- Per-app resource caps: max CPU%, max RAM MB, max VRAM MB — enforced via cgroups (Linux) or Job Objects (Windows)
- Automatic kill or throttle when a process exceeds its cap
- Schedule rules: allow an app to run only between certain hours, or only when GPU is below X% load
- Auto-restart rules: if a whitelisted process dies, restart it automatically

### 🔵 Web Dashboard
- Live at `http://localhost:4080` — open in any browser, no install
- Real-time charts for all metrics, updating every second
- GPU enforcement status board — which apps are GPU-enforced, which are violating
- Process manager with one-click kill, throttle, or GPU-migrate
- Alert history and audit log viewer
- Config editor UI — view all rules from the browser
- Dark mode by default

### 🟣 AI Agent Layer (optional, fully local)
- Connect any local LLM via Ollama, llama.cpp, or LM Studio
- The agent reads your system state and writes natural language summaries every N minutes
- Ask the agent questions in plain English: *"Why is my RAM so high?"* *"Which process is killing my GPU?"*
- All LLM inference runs locally — your system data never leaves the machine

---

## GPU Enforcement Engine

When you add an app to the GPU enforcement list, SentinelCore does the following:

**Step 1 — Process Detection**
The agent watches the process table for the target executable (by name or full path). The moment it spawns, the agent intercepts it.

**Step 2 — Enforcement Monitoring**
Every 2 seconds, SentinelCore checks:
- Is the target process still alive?
- Is its CPU usage within the defined cap?
- Is its RAM usage within the defined cap?
- Is its VRAM usage within the defined cap?

If any check fails, the configured enforcement action triggers: warn, throttle, kill, or re-launch.

**Step 3 — Audit Log**
Every enforcement action is written:
```
[2026-06-21 14:32:01] ENFORCE  chrome.exe        GPU=94% CPU=3%  VRAM=2.1GB  ✓ compliant
[2026-06-21 14:32:03] VIOLATED blender.exe       GPU=0%  CPU=87% VRAM=0MB    → re-launching with CUDA flags
[2026-06-21 14:32:05] RESTORED blender.exe       GPU=91% CPU=4%  VRAM=3.8GB  ✓ compliant
```

### Supported GPU Backends

| Backend | Hardware | Detection Method |
|---|---|---|
| CUDA 12+ | NVIDIA RTX, GTX, Quadro | `nvidia-smi`, `nvml` |
| ROCm 6+ | AMD RX 6000/7000, Pro | `rocm-smi`, `amdsmi` |
| Metal 3 | Apple M1/M2/M3/M4 | `IOKit`, `Metal Performance Shaders` |
| oneAPI | Intel Arc A/B series | `xpu-smi`, `igpu-mon` |
| Vulkan | Any Vulkan-capable GPU | `vulkaninfo` |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        SentinelCore                         │
│                                                             │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────┐  │
│  │  Collector   │   │  Enforcer    │   │   AI Agent     │  │
│  │              │   │              │   │                │  │
│  │ · psutil     │   │ · Job Objects│   │ · Ollama API   │  │
│  │ · nvml       │   │ · setrlimit  │   │ · llama.cpp    │  │
│  │ · rocm-smi   │   │ · GPU flags  │   │ · LM Studio    │  │
│  │ · IOKit      │   │ · process    │   │                │  │
│  │ · /proc/net  │   │   migration  │   │ local only,    │  │
│  │ · smartctl   │   │              │   │ opt-in         │  │
│  └──────┬───────┘   └──────┬───────┘   └───────┬────────┘  │
│         └──────────────────┼────────────────────┘           │
│                            │                                │
│                   ┌────────▼────────┐                       │
│                   │  SQLite Store   │                       │
│                   │  metrics.db     │                       │
│                   └────────┬────────┘                       │
│                            │                                │
│                   ┌────────▼────────┐                       │
│                   │  Web Server     │                       │
│                   │  Flask + SSE    │                       │
│                   │  localhost:4080 │                       │
│                   └────────┬────────┘                       │
└────────────────────────────┼────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  Browser        │
                    │  Dashboard      │
                    │  (any browser)  │
                    └─────────────────┘
```

**Single process, no microservices.** SentinelCore runs as one background daemon. No Docker required.

---

## Hardware Requirements

### Minimum
| Component | Minimum |
|---|---|
| CPU | Any dual-core (x86-64 or ARM64) |
| RAM | 256 MB available for the agent itself |
| GPU | Any GPU with a driver (NVIDIA, AMD, Intel, Apple Silicon) |
| OS | Linux 5.15+, Windows 10 22H2+, macOS 13+ |
| Python | 3.11+ |

### Recommended
| Component | Recommended |
|---|---|
| GPU | NVIDIA RTX 3060+ / AMD RX 6700+ / Apple M2+ for AI agent |
| VRAM | 8GB+ if using local LLM for the AI agent layer |
| RAM | 16GB system RAM |
| Storage | 2GB free for metric database |

---

## Installation

### Linux
```bash
git clone https://github.com/yourusername/sentinelcore
cd sentinelcore
pip install -r requirements.txt
cp config.example.toml ~/.sentinelcore/config.toml
python3 sentinelcore.py
# Open http://localhost:4080
```

### Windows
```powershell
git clone https://github.com/yourusername/sentinelcore
cd sentinelcore
pip install -r requirements.txt
python sentinelcore.py
# Open http://localhost:4080
```

### macOS
```bash
git clone https://github.com/yourusername/sentinelcore
cd sentinelcore
pip3 install -r requirements.txt
python3 sentinelcore.py
open http://localhost:4080
```

---

## Configuration Reference

Full config lives at `~/.sentinelcore/config.toml` or `./config.toml`.

See [`config.example.toml`](config.example.toml) for the complete reference.

```toml
[agent]
poll_interval_ms  = 1000        # metric collection frequency
dashboard_port    = 4080
dashboard_host    = "127.0.0.1" # never expose to LAN without auth

[gpu]
backend           = "auto"      # auto | cuda | rocm | metal | vulkan
enforcement       = true

[[enforce]]
name        = "Chrome"
exe         = "chrome"
gpu_enforce = true
max_cpu_pct = 15
max_ram_mb  = 3000
action      = "throttle"        # alert | throttle | kill | restart

[[block]]
exe    = "teams"
reason = "CPU/RAM hog"

[[schedule]]
exe           = "steam"
allowed_hours = "20:00-23:59"
```

---

## Alert System

| Trigger | Default Threshold | Severity |
|---|---|---|
| CPU sustained high | >85% | Warning |
| RAM pressure | >90% used | Warning |
| RAM pressure critical | >97% used | Critical |
| GPU temperature | >85°C | Warning |
| GPU temperature critical | >95°C | Critical |
| VRAM near full | >95% used | Warning |
| Blocked process launch | Any | Warning |
| Process exceeds cap | Any | Info |

---

## Roadmap

- [x] **v0.1** — Core metrics collector, SQLite storage, web dashboard
- [x] **v0.2** — GPU enforcement engine (NVIDIA CUDA + browser flags)
- [x] **v0.3** — Process controller (cgroups, Job Objects, kill/throttle)
- [x] **v0.4** — AMD ROCm + Apple Metal enforcement support
- [x] **v0.5** — AI agent layer (Ollama integration, summaries, Q&A)
- [ ] **v0.6** — Browser tab awareness
- [ ] **v0.7** — Multi-GPU routing
- [ ] **v1.0** — Stable release, Windows installer, macOS .app bundle

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

Core rules: no cloud dependencies, no telemetry, all actions auditable, agent stays under 100MB RAM.

---

## License

(LICENSE)

---

<p align="center">Built for people who want their machine to work for them — not for someone else's server.</p>
