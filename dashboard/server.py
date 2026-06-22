import json
import logging
import time
import threading
from typing import Any, Dict

import psutil
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

log = logging.getLogger("dashboard")

_store = None
_collector = None
_enforcer = None
_alerts_sys = None
_config = None
_ai_agent = None
_bg_agent = None
_latest: Dict[str, Any] = {}
_latest_lock = threading.Lock()


def set_latest(metrics: Dict[str, Any]):
    global _latest
    with _latest_lock:
        _latest = metrics


def create_app(config, store, collector, enforcer, alerts, ai_agent=None, bg_agent=None):
    global _store, _collector, _enforcer, _alerts_sys, _config, _ai_agent, _bg_agent
    _store = store
    _collector = collector
    _enforcer = enforcer
    _alerts_sys = alerts
    _config = config
    _ai_agent = ai_agent
    _bg_agent = bg_agent

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = "sentinelcore-local-only"

    # --- Pages ---

    @app.route("/")
    def overview():
        return render_template("overview.html", page="overview")

    @app.route("/gpu")
    def gpu():
        return render_template("gpu.html", page="gpu")

    @app.route("/processes")
    def processes():
        return render_template("processes.html", page="processes")

    @app.route("/network")
    def network():
        return render_template("network.html", page="network")

    @app.route("/alerts")
    def alerts_page():
        return render_template("alerts.html", page="alerts")

    @app.route("/config")
    def config_page():
        return render_template("config_editor.html", page="config")

    @app.route("/audit")
    def audit_page():
        return render_template("audit.html", page="audit")

    @app.route("/background")
    def background_page():
        return render_template("background.html", page="background")

    # --- API: background agent ---

    @app.route("/api/background")
    def api_background():
        if _bg_agent is None:
            return jsonify({"error": "background agent not initialised"}), 503
        return jsonify(_bg_agent.collect())

    @app.route("/api/background/stream")
    def api_background_stream():
        def generate():
            while True:
                try:
                    if _bg_agent:
                        data = _bg_agent.collect()
                        yield f"data: {json.dumps(data, default=str)}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
                time.sleep(3)
        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    @app.route("/api/background/allprocs")
    def api_all_procs():
        if _bg_agent is None:
            return jsonify([])
        return jsonify(_bg_agent.collect_all_procs())

    # --- API: live stream ---

    @app.route("/api/stream")
    def api_stream():
        def generate():
            while True:
                try:
                    with _latest_lock:
                        snapshot = dict(_latest)
                    snapshot["enforcement"] = _enforcer.get_enforcement_status()
                    snapshot["alert_count"] = len(
                        _store.get_alerts(limit=50, unacknowledged_only=True)
                    )
                    yield f"data: {json.dumps(snapshot, default=str)}\n\n"
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
                time.sleep(1)

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # --- API: metrics ---

    @app.route("/api/metrics/latest")
    def api_latest():
        with _latest_lock:
            return jsonify(dict(_latest))

    @app.route("/api/metrics/history/<table>")
    def api_history(table):
        hours = request.args.get("hours", 1, type=int)
        return jsonify(_store.get_history(table, hours))

    @app.route("/api/metrics/partitions")
    def api_partitions():
        return jsonify(_collector.get_disk_partitions())

    # --- API: health ---

    @app.route("/api/health")
    def api_health():
        with _latest_lock:
            m = dict(_latest)
        cpu_pct = m.get("cpu", {}).get("total_pct", 0)
        mem_pct = m.get("memory", {}).get("pct", 0)
        scores = [max(0, 100 - cpu_pct), max(0, 100 - mem_pct)]
        for gpu in m.get("gpus", []):
            temp = gpu.get("temp_c")
            if temp:
                scores.append(max(0, 100 - max(0, temp - 50) * 1.5))
        health = int(sum(scores) / max(len(scores), 1))
        return jsonify({"score": min(100, health), "cpu_pct": cpu_pct, "mem_pct": mem_pct})

    # --- API: alerts ---

    @app.route("/api/alerts")
    def api_alerts():
        limit = request.args.get("limit", 100, type=int)
        unack = request.args.get("unack", "false").lower() == "true"
        return jsonify(_store.get_alerts(limit, unacknowledged_only=unack))

    @app.route("/api/alerts/<int:aid>/ack", methods=["POST"])
    def api_ack(aid):
        _store.acknowledge_alert(aid)
        return jsonify({"ok": True})

    @app.route("/api/alerts/<int:aid>/ack_all", methods=["POST"])
    def api_ack_all(aid=None):
        for a in _store.get_alerts(limit=1000, unacknowledged_only=True):
            _store.acknowledge_alert(a["id"])
        return jsonify({"ok": True})

    @app.route("/api/alerts/ack_all", methods=["POST"])
    def api_ack_all2():
        for a in _store.get_alerts(limit=1000, unacknowledged_only=True):
            _store.acknowledge_alert(a["id"])
        return jsonify({"ok": True})

    # --- API: audit ---

    @app.route("/api/audit")
    def api_audit():
        limit = request.args.get("limit", 200, type=int)
        return jsonify(_store.get_audit(limit))

    # --- API: enforcement ---

    @app.route("/api/enforcement")
    def api_enforcement():
        return jsonify(_enforcer.get_enforcement_status())

    # --- API: processes ---

    @app.route("/api/processes")
    def api_processes():
        with _latest_lock:
            return jsonify(_latest.get("processes", []))

    @app.route("/api/processes/<int:pid>/kill", methods=["POST"])
    def api_kill(pid):
        try:
            p = psutil.Process(pid)
            name = p.name()
            p.terminate()
            _store.add_audit("KILL", name, "user action via dashboard", f"terminated PID {pid}")
            return jsonify({"ok": True, "message": f"Terminated {name} (PID {pid})"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/processes/<int:pid>/migrate", methods=["POST"])
    def api_migrate(pid):
        result = _enforcer.migrate_pid(pid)
        return jsonify(result), (200 if result["ok"] else 400)

    @app.route("/api/processes/<int:pid>/throttle", methods=["POST"])
    def api_throttle(pid):
        import platform
        try:
            p = psutil.Process(pid)
            name = p.name()
            if platform.system() == "Windows":
                p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            else:
                p.nice(10)
            _store.add_audit("THROTTLE", name, "user action via dashboard", f"priority lowered PID {pid}")
            return jsonify({"ok": True, "message": f"Throttled {name} (PID {pid})"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    # --- API: config ---

    @app.route("/api/config")
    def api_config():
        import dataclasses
        return jsonify(dataclasses.asdict(_config))

    # --- API: AI ---

    @app.route("/api/ai/summary")
    def api_ai_summary():
        if _ai_agent is None:
            return jsonify({"enabled": False})
        return jsonify({"enabled": True, **_ai_agent.get_last_summary()})

    @app.route("/api/ai/ask", methods=["POST"])
    def api_ai_ask():
        if _ai_agent is None:
            return jsonify({"error": "AI agent not enabled"}), 400
        question = (request.json or {}).get("question", "")
        if not question:
            return jsonify({"error": "No question provided"}), 400
        answer = _ai_agent.ask(question)
        return jsonify({"answer": answer})

    return app
