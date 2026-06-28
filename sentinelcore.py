#!/usr/bin/env python3
import argparse
import logging
import signal
import sys
import threading
import time

from core.config import load_config
from core.store import MetricsStore
from core.collector import MetricsCollector
from core.enforcer import GPUEnforcer
from core.alerts import AlertSystem
from core.ai_agent import AIAgent
from core.background import BackgroundAgent
from dashboard import server as dash


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def main():
    parser = argparse.ArgumentParser(
        description="SentinelCore — privacy-first local system agent"
    )
    parser.add_argument("--config", metavar="PATH", help="Path to config.toml")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config.agent.log_level)
    log = logging.getLogger("sentinelcore")

    log.info("Starting SentinelCore v1.0.0")

    store     = MetricsStore(config)
    collector = MetricsCollector(config)
    enforcer  = GPUEnforcer(config, store, collector)
    alerts    = AlertSystem(config, store)

    ai_agent = None
    if config.ai.enabled:
        ai_agent = AIAgent(config, store)
        log.info("AI agent enabled (%s @ %s)", config.ai.model, config.ai.endpoint)

    bg_agent = BackgroundAgent()
    log.info("Background agent started")

    stop = threading.Event()

    def _handle_signal(sig, frame):
        log.info("Shutdown signal received")
        stop.set()

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    def collection_loop():
        cleanup_tick = 0
        interval = config.agent.poll_interval_ms / 1000.0
        while not stop.is_set():
            try:
                metrics = collector.collect()
                store.insert_metrics(metrics)
                alerts.check(metrics)
                enforcer.check(metrics)
                dash.set_latest(metrics)

                cleanup_tick += 1
                if cleanup_tick >= 3600:
                    store.cleanup_old_data()
                    cleanup_tick = 0
            except Exception as exc:
                log.error("Collection error: %s", exc, exc_info=True)
            stop.wait(interval)

    def ai_loop():
        while not stop.is_set():
            try:
                ai_agent.run_cycle()
            except Exception as exc:
                log.error("AI error: %s", exc)
            stop.wait(config.ai.interval)

    t_collect = threading.Thread(target=collection_loop, name="collector", daemon=True)
    t_collect.start()

    if ai_agent:
        t_ai = threading.Thread(target=ai_loop, name="ai_agent", daemon=True)
        t_ai.start()

    app = dash.create_app(config, store, collector, enforcer, alerts, ai_agent, bg_agent)

    host = config.agent.dashboard_host
    port = config.agent.dashboard_port
    log.info("Dashboard @ http://%s:%s", host, port)

    try:
        app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)
    except OSError as e:
        log.error("Cannot bind to %s:%s — %s", host, port, e)
        sys.exit(1)
    except KeyboardInterrupt:
        pass

    stop.set()
    log.info("SentinelCore stopped.")


if __name__ == "__main__":
    main()
