import json
import logging
import threading
import time
from typing import Dict, Generator, List, Optional

log = logging.getLogger("ai_agent")

_SYSTEM_PROMPT = (
    "You are SentinelCore's built-in AI assistant. "
    "You have access to real-time data from a local machine: CPU, RAM, GPU, network, and process metrics. "
    "Give concise, direct answers. Reference specific process names or metric values when relevant. "
    "This system is air-gapped by design — never suggest cloud services. "
    "Never invent data not present in the provided context."
)

_MAX_HISTORY = 10  # conversation pairs kept


class AIAgent:
    def __init__(self, config, store):
        self.config = config
        self.store = store
        self._summary = ""
        self._summary_ts = 0.0
        self._lock = threading.Lock()
        self._history: List[Dict] = []

    # ── Periodic cycle ──────────────────────────────────────────────────────

    def run_cycle(self):
        try:
            ctx = self._build_context()
            prompt = self._summary_prompt(ctx)
            text = ""
            for chunk in self._generate(prompt):
                text += chunk
            text = text.strip()
            with self._lock:
                self._summary = text
                self._summary_ts = time.time()
            lower = text.lower()
            if any(w in lower for w in ("critical", "overheating", "memory leak", "spike", "very high", "dangerously")):
                self.store.add_alert("warning", f"AI: {text[:200]}")
            log.info("AI summary updated (%d chars)", len(text))
        except Exception as e:
            log.error("AI cycle error: %s", e)

    # ── Q&A ─────────────────────────────────────────────────────────────────

    def ask(self, question: str) -> str:
        result = ""
        for chunk in self.ask_stream(question):
            result += chunk
        return result

    def ask_stream(self, question: str) -> Generator[str, None, None]:
        ctx = self._build_context()
        user_content = f"Current system state:\n{_ctx_text(ctx)}\n\nQuestion: {question}"

        with self._lock:
            self._history.append({"role": "user", "content": user_content})
            history_snapshot = list(self._history)

        full_response = ""
        try:
            for chunk in self._chat(history_snapshot):
                full_response += chunk
                yield chunk
        finally:
            with self._lock:
                self._history.append({"role": "assistant", "content": full_response or "(no response)"})
                if len(self._history) > _MAX_HISTORY * 2:
                    self._history = self._history[-(_MAX_HISTORY * 2):]

    def clear_history(self):
        with self._lock:
            self._history.clear()

    # ── Introspection ────────────────────────────────────────────────────────

    def get_last_summary(self) -> Dict:
        with self._lock:
            return {"summary": self._summary, "timestamp": self._summary_ts}

    def list_models(self) -> List[str]:
        try:
            import requests
            cfg = self.config.ai
            if cfg.backend == "ollama":
                r = requests.get(f"{cfg.endpoint}/api/tags", timeout=5)
                r.raise_for_status()
                return [m["name"] for m in r.json().get("models", [])]
            r = requests.get(f"{cfg.endpoint}/v1/models", timeout=5)
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
        except Exception:
            return []

    def test_connection(self) -> Dict:
        try:
            import requests
            cfg = self.config.ai
            if cfg.backend == "ollama":
                r = requests.get(f"{cfg.endpoint}/api/tags", timeout=5)
                r.raise_for_status()
                models = [m["name"] for m in r.json().get("models", [])]
                return {"ok": True, "backend": "ollama", "endpoint": cfg.endpoint, "models": models}
            r = requests.get(f"{cfg.endpoint}/v1/models", timeout=5)
            r.raise_for_status()
            return {"ok": True, "backend": cfg.backend, "endpoint": cfg.endpoint}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Context building ─────────────────────────────────────────────────────

    def _build_context(self) -> Dict:
        cpu_rows = self.store.get_latest_cpu()
        mem = self.store.get_latest_mem()
        gpus = self.store.get_latest_gpu()
        alerts = self.store.get_alerts(limit=5, unacknowledged_only=True)
        audit = self.store.get_audit(limit=5)
        procs = self.store.get_latest_processes(limit=10)

        avg_cpu = sum(c.get("pct", 0) for c in cpu_rows) / max(len(cpu_rows), 1)

        return {
            "cpu_pct": round(avg_cpu, 1),
            "ram_used_mb": mem.get("used_mb", 0) if mem else 0,
            "ram_total_mb": mem.get("total_mb", 0) if mem else 0,
            "ram_pct": mem.get("pct", 0) if mem else 0,
            "gpus": [
                {
                    "name": g.get("name"),
                    "util_pct": g.get("util_pct"),
                    "vram_used_mb": g.get("vram_used_mb"),
                    "vram_total_mb": g.get("vram_total_mb"),
                    "temp_c": g.get("temp_c"),
                    "watts": g.get("watts"),
                }
                for g in gpus
            ],
            "top_procs_cpu": sorted(procs, key=lambda p: p.get("cpu_pct", 0), reverse=True)[:5],
            "top_procs_ram": sorted(procs, key=lambda p: p.get("ram_mb", 0), reverse=True)[:5],
            "recent_alerts": [a.get("message", "") for a in alerts],
            "recent_actions": [f"{a.get('action')} {a.get('target')}" for a in audit],
        }

    @staticmethod
    def _summary_prompt(ctx: Dict) -> str:
        return (
            f"{_ctx_text(ctx)}\n\n"
            "Write a single concise paragraph summarizing overall system health and any notable issues. "
            "Be specific about which processes or components are of concern."
        )

    # ── LLM backends ─────────────────────────────────────────────────────────

    def _generate(self, prompt: str) -> Generator[str, None, None]:
        """Non-chat completion for periodic summaries (no history)."""
        import requests
        cfg = self.config.ai
        if cfg.backend == "ollama":
            payload = {
                "model": cfg.model,
                "prompt": prompt,
                "system": _SYSTEM_PROMPT,
                "stream": True,
            }
            with requests.post(f"{cfg.endpoint}/api/generate", json=payload, timeout=60, stream=True) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        chunk = obj.get("response", "")
                        if chunk:
                            yield chunk
                        if obj.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue
        else:
            yield from self._chat([{"role": "user", "content": prompt}])

    def _chat(self, messages: List[Dict]) -> Generator[str, None, None]:
        """Chat completion (with history) — all backends."""
        import requests
        cfg = self.config.ai
        full_messages = [{"role": "system", "content": _SYSTEM_PROMPT}] + messages

        if cfg.backend == "ollama":
            payload = {"model": cfg.model, "messages": full_messages, "stream": True}
            with requests.post(f"{cfg.endpoint}/api/chat", json=payload, timeout=60, stream=True) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        chunk = obj.get("message", {}).get("content", "")
                        if chunk:
                            yield chunk
                        if obj.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue

        elif cfg.backend in ("llamacpp", "lmstudio"):
            payload = {
                "model": cfg.model,
                "messages": full_messages,
                "max_tokens": 500,
                "stream": True,
            }
            with requests.post(f"{cfg.endpoint}/v1/chat/completions", json=payload, timeout=60, stream=True) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line or line == b"data: [DONE]":
                        if line == b"data: [DONE]":
                            break
                        continue
                    if line.startswith(b"data: "):
                        try:
                            obj = json.loads(line[6:])
                            chunk = obj["choices"][0]["delta"].get("content", "")
                            if chunk:
                                yield chunk
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
        else:
            raise ValueError(f"Unknown AI backend: {cfg.backend}")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ctx_text(ctx: Dict) -> str:
    lines = [
        f"CPU: {ctx['cpu_pct']}%",
        f"RAM: {ctx['ram_used_mb']:.0f}/{ctx['ram_total_mb']:.0f} MB ({ctx['ram_pct']}%)",
    ]
    for g in ctx.get("gpus", []):
        lines.append(
            f"GPU {g.get('name', '?')}: util={g.get('util_pct')}%  "
            f"VRAM={g.get('vram_used_mb')}/{g.get('vram_total_mb')} MB  "
            f"temp={g.get('temp_c')}°C  power={g.get('watts')}W"
        )
    cpu_procs = ctx.get("top_procs_cpu", [])
    if cpu_procs:
        lines.append("Top CPU: " + ", ".join(
            f"{p.get('name')}({p.get('cpu_pct', 0):.1f}%)" for p in cpu_procs[:5]
        ))
    ram_procs = ctx.get("top_procs_ram", [])
    if ram_procs:
        lines.append("Top RAM: " + ", ".join(
            f"{p.get('name')}({p.get('ram_mb', 0):.0f}MB)" for p in ram_procs[:5]
        ))
    if ctx.get("recent_alerts"):
        lines.append("Active alerts: " + "; ".join(ctx["recent_alerts"][:3]))
    if ctx.get("recent_actions"):
        lines.append("Recent agent actions: " + "; ".join(ctx["recent_actions"][:3]))
    return "\n".join(lines)
