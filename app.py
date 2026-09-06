"""
app.py
Flask REST API + Server-Sent Events backend for the
Outlook Auto-Expand Account Manager dashboard.
"""

import csv
import io
import json
import queue
import time
import uuid
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from flask_cors import CORS

from automation_engine import BatchJob

app = Flask(__name__)
CORS(app)

# ─── Global state ─────────────────────────────────────────────────────────────

_current_job: BatchJob | None = None
_log_queue: queue.Queue = queue.Queue(maxsize=1000)
_log_history: list[dict] = []  # persistent for page reload
_last_results: list[dict] = [] # persistent results backup across reloads


def _emit(account_id: str, level: str, message: str) -> None:
    """Callback passed to the automation engine to enqueue log events."""
    entry = {
        "ts": time.strftime("%H:%M:%S"),
        "account": account_id,
        "level": level,
        "message": message,
    }
    _log_history.append(entry)
    if len(_log_history) > 2000:
        _log_history.pop(0)
    try:
        _log_queue.put_nowait(entry)
    except queue.Full:
        pass  # drop oldest if queue is saturated


# ─── API Routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/api/start", methods=["POST"])
def api_start():
    global _current_job, _log_queue, _log_history, _last_results

    if _current_job and _current_job.running:
        return jsonify({"ok": False, "error": "A job is already running."}), 409

    data = request.get_json(force=True)
    accounts_raw: str = data.get("accounts", "")
    config: dict = data.get("config", {})

    # Parse "email:password" lines
    accounts = []
    for line in accounts_raw.strip().splitlines():
        line = line.strip()
        if ":" in line:
            parts = line.split(":", 1)
            accounts.append({"email": parts[0].strip(), "password": parts[1].strip()})

    if not accounts:
        return jsonify({"ok": False, "error": "No valid accounts found. Use email:password format."}), 400

    # Reset state in-place
    while not _log_queue.empty():
        try:
            _log_queue.get_nowait()
        except queue.Empty:
            break
    _log_history.clear()
    _last_results = []

    _current_job = BatchJob(accounts=accounts, config=config, emit_fn=_emit)
    _current_job.start()

    return jsonify({"ok": True, "total": len(accounts)})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global _current_job
    if _current_job:
        _current_job.stop()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "No job running."}), 404


@app.route("/api/status")
def api_status():
    global _current_job, _last_results
    if _current_job:
        status = _current_job.get_status()
        if status.get("results"):
            _last_results = status["results"]
        return jsonify(status)
    return jsonify({
        "running": False,
        "total": 0,
        "completed": 0,
        "successful": 0,
        "failed": 0,
        "results": _last_results,
    })


@app.route("/api/logs")
def api_logs():
    """Return persistent log history for page loads and SSE reconnects."""
    return jsonify(_log_history)


@app.route("/api/clear", methods=["POST"])
def api_clear():
    """Clear log history on server."""
    global _log_history
    _log_history.clear()
    return jsonify({"ok": True})


@app.route("/api/clear_results", methods=["POST"])
def api_clear_results():
    """Clear account results on server."""
    global _current_job, _last_results
    if _current_job and _current_job.running:
        _current_job.stop()
    _current_job = None
    _last_results = []
    return jsonify({"ok": True})


@app.route("/api/stream")
def api_stream():
    """Server-Sent Events endpoint. Streams ONLY new real-time log entries (no past backlog)."""
    def event_generator():
        # Do not send past history backlog on connect — start clean every time!
        while True:
            try:
                entry = _log_queue.get(timeout=3)
                yield f"data: {json.dumps(entry)}\n\n"
            except queue.Empty:
                try:
                    yield ": heartbeat\n\n"
                except Exception:
                    break
            except (GeneratorExit, BrokenPipeError, ConnectionResetError, OSError):
                break
            except Exception:
                break

    return Response(
        stream_with_context(event_generator()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/export")
def api_export():
    """Export results in txt, csv, or json format."""
    fmt = request.args.get("format", "json").lower()
    global _current_job, _last_results

    raw_results = (_current_job.results if _current_job else []) or _last_results
    results = [r for r in raw_results if r.get("status") == "success" or r.get("created_aliases")]

    if fmt == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Email", "Password", "Status", "Created Aliases", "Error"])
        for r in results:
            writer.writerow([
                r.get("email", ""),
                r.get("password", ""),
                r.get("status", ""),
                ", ".join(r.get("created_aliases", [])),
                r.get("error", ""),
            ])
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=outlook_aliases.csv"},
        )

    elif fmt == "txt":
        lines = []
        lines.append("=================================================")
        lines.append("           OUTLOOK ALIASES EXPORT")
        lines.append("=================================================")
        lines.append("")
        lines.append("--- ALL CREATED ALIASES (ALIAS:PASSWORD) ---")
        all_alias_lines = []
        for r in results:
            pwd = f":{r.get('password')}" if r.get("password") else ""
            for alias in r.get("created_aliases", []):
                all_alias_lines.append(f"{alias}{pwd}")
        
        if all_alias_lines:
            lines.extend(all_alias_lines)
        else:
            lines.append("(No aliases created)")

        lines.append("")
        lines.append("--- ACCOUNT SUMMARY BREAKDOWN ---")
        for r in results:
            pwd = f":{r.get('password')}" if r.get("password") else ""
            aliases_str = ", ".join(r.get("created_aliases", [])) or "None"
            lines.append(f"Account : {r.get('email')}{pwd}")
            lines.append(f"Status  : {r.get('status')}")
            lines.append(f"Aliases : {aliases_str}")
            if r.get("error"):
                lines.append(f"Error   : {r.get('error')}")
            lines.append("-------------------------------------------------")

        return Response(
            "\n".join(lines),
            mimetype="text/plain",
            headers={"Content-Disposition": "attachment; filename=outlook_aliases.txt"},
        )

    else:  # json
        return Response(
            json.dumps(results, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=outlook_aliases.json"},
        )


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True)
