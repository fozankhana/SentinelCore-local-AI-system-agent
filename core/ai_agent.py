import json
import logging
import threading
import time
from typing import Dict

log = logging.getLogger("ai_agent")


class AIAgent:
    def __init__(self, config, store):
        self.config = config
        self.store = store
        self._summary = ""
        self._summary_ts = 0.0
        self._lock = threading.Lock()

    def run_cycle(self):
        try:
            ctx = self._build_context()
            prompt = self._summary_prompt(ctx)
            text = self._query(prompt)
            with self._lock:
                self._summary = text
                self._summary_ts = time.time()
            log.info("AI summary updated")
        except Exception as e:
            log.error("AI cycle error: %s", e)

    def ask(self, question: str) -> str:
        ctx = self._build_context()
        prompt = (
            f"System data:\n{json.dumps(ctx, indent=2)}\n\n"
            f"Question: {question}\n\n"
            "Answer in 2-3 plain sentences based on the data above."
        )
        try:
            return self._query(prompt)
        except Exception as e:
            return f"Query failed: {e}"

    def get_last_summary(self) -> Dict:
        with self._lock:
            return {"summary": self._summary, "timestamp": self._summary_ts}

    def _build_context(self) -> Dict:
        cpu = self.store.get_latest_cpu()
        mem = self.store.get_latest_mem()
        gpu = self.store.get_latest_gpu()
        alerts = self.store.get_alerts(limit=5, unacknowledged_only=True)
        avg_cpu = sum(c.get("pct", 0) for c in cpu) / max(len(cpu), 1)
        return {
            "cpu_pct": round(avg_cpu, 1),
            "ram_used_mb": mem.get("used_mb") if mem else 0,
            "ram_total_mb": mem.get("total_mb") if mem else 0,
            "ram_pct": mem.get("pct") if mem else 0,
            "gpus": gpu,
            "recent_alerts": [a.get("message") for a in alerts],
        }

    @staticmethod
    def _summary_prompt(ctx: Dict) -> str:
        return (
            f"System status:\n"
            f"CPU: {ctx['cpu_pct']}%  "
            f"RAM: {ctx['ram_used_mb']:.0f}/{ctx['ram_total_mb']:.0f}MB ({ctx['ram_pct']}%)\n"
            f"GPUs: {json.dumps(ctx['gpus'])}\n"
            f"Active alerts: {', '.join(ctx['recent_alerts']) or 'none'}\n\n"
            "Write a single concise paragraph summarizing system health and any issues."
        )

    def _query(self, prompt: str) -> str:
        import requests
        cfg = self.config.ai
        if cfg.backend == "ollama":
            r = requests.post(
                f"{cfg.endpoint}/api/generate",
                json={"model": cfg.model, "prompt": prompt, "stream": False},
                timeout=30,
            )
            r.raise_for_status()
            return r.json().get("response", "")
        elif cfg.backend in ("llamacpp", "lmstudio"):
            r = requests.post(
                f"{cfg.endpoint}/v1/completions",
                json={"model": cfg.model, "prompt": prompt, "max_tokens": 300},
                timeout=30,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["text"]
        raise ValueError(f"Unknown AI backend: {cfg.backend}")
