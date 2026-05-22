import os
import json
import queue
import threading
from datetime import datetime
from flask import Flask, render_template, Response, jsonify, send_file
from fetcher import run_fetcher

app = Flask(__name__)

# Global state for Au Result Date
run_state = {
    "running": False,
    "results": None,
    "error": None,
    "last_run": None,
    "run_start": None,
}


@app.route("/")
def home():
    return render_template("fetch-date.html")


@app.route("/fetch-date")
def fetch_date_page():
    return render_template("fetch-date.html")


@app.route("/run")
def run():
    if run_state["running"]:
        # Stale-run detection: if running for more than 5 min, force reset
        if run_state["run_start"]:
            try:
                started = datetime.fromisoformat(run_state["run_start"])
                if (datetime.now() - started).total_seconds() > 300:
                    run_state["running"] = False
                else:
                    return Response(
                        f"data: {json.dumps({'type': 'error', 'message': 'Already running! Please wait for the current task to finish.'})}\n\n",
                        mimetype="text/event-stream",
                    )
            except (ValueError, TypeError):
                run_state["running"] = False
        else:
            run_state["running"] = False

    def generate():
        msg_queue = queue.Queue()
        run_state["running"] = True
        run_state["run_start"] = datetime.now().isoformat()
        run_state["results"] = None
        run_state["error"] = None

        def progress_callback(message):
            msg_queue.put(message)

        def worker():
            try:
                result = run_fetcher(progress_callback=progress_callback)
                msg_queue.put(("__DONE__", result))
            except Exception as e:
                msg_queue.put(("__ERROR__", str(e)))
            finally:
                run_state["running"] = False
                run_state["last_run"] = datetime.now().isoformat()

        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()

        # Stream messages from queue to SSE
        # Use 15-second heartbeat to prevent proxy from killing connection
        while True:
            try:
                msg = msg_queue.get(timeout=15)
            except queue.Empty:
                yield ": heartbeat\n\n"
                continue

            if isinstance(msg, tuple) and msg[0] == "__DONE__":
                result = msg[1]
                run_state["results"] = result
                run_state["running"] = False
                run_state["last_run"] = datetime.now().isoformat()
                summary = {
                    "success": result.get("success", False),
                    "total_rows": result.get("total_rows", 0),
                    "error": result.get("error"),
                }
                yield f"data: {json.dumps({'type': 'complete', 'result': summary})}\n\n"
                break
            elif isinstance(msg, tuple) and msg[0] == "__ERROR__":
                run_state["error"] = msg[1]
                run_state["running"] = False
                yield f"data: {json.dumps({'type': 'error', 'message': msg[1]})}\n\n"
                break
            else:
                yield f"data: {json.dumps({'type': 'progress', 'message': msg})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/results")
def results():
    return jsonify(run_state)


@app.route("/download-csv")
def download_csv():
    import tempfile
    csv_path = os.path.join(tempfile.gettempdir(), "au_date_result_output.csv")
    if os.path.exists(csv_path):
        return send_file(csv_path, as_attachment=True, download_name="au_date_result_output.csv")
    return "No CSV available. Run the fetcher first.", 404


@app.route("/reset")
def reset():
    """Force reset running state - use when stuck 'Already running!'."""
    run_state["running"] = False
    run_state["run_start"] = None
    return jsonify({"status": "reset", "message": "State reset OK"})


@app.route("/status")
def status():
    """Quick check if the server is alive and if a task is running."""
    return jsonify({
        "status": "ok",
        "running": run_state["running"],
        "last_run": run_state["last_run"],
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
