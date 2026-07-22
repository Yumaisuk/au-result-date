import os
import json
import queue
from flask import Flask, render_template, Response, jsonify, send_file

import discord_bot
import run_manager

app = Flask(__name__)

# Start the Discord bot alongside the web app (no-op if DISCORD_BOT_TOKEN isn't set).
if __name__ != "__main__":
    # Imported by a WSGI server (gunicorn) - start immediately, no reloader involved.
    discord_bot.start_bot()


@app.route("/")
def home():
    return render_template("fetch-date.html")


@app.route("/fetch-date")
def fetch_date_page():
    return render_template("fetch-date.html")


@app.route("/run")
def run():
    msg_queue = queue.Queue()

    def progress_callback(message):
        msg_queue.put(message)

    def done_callback(result):
        msg_queue.put(("__DONE__", result))

    started = run_manager.start_run(progress_callback, done_callback)
    if not started:
        return Response(
            f"data: {json.dumps({'type': 'error', 'message': 'Already running! Please wait for the current task to finish.'})}\n\n",
            mimetype="text/event-stream",
        )

    def generate():
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
                summary = {
                    "success": result.get("success", False),
                    "total_rows": result.get("total_rows", 0),
                    "error": result.get("error"),
                }
                yield f"data: {json.dumps({'type': 'complete', 'result': summary})}\n\n"
                break
            else:
                yield f"data: {json.dumps({'type': 'progress', 'message': msg})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/results")
def results():
    return jsonify(run_manager.state)


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
    run_manager.reset()
    return jsonify({"status": "reset", "message": "State reset OK"})


@app.route("/status")
def status():
    """Quick check if the server is alive and if a task is running."""
    return jsonify({
        "status": "ok",
        "running": run_manager.state["running"],
        "last_run": run_manager.state["last_run"],
    })


if __name__ == "__main__":
    # Guard against Werkzeug's debug reloader starting the bot twice.
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        discord_bot.start_bot()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
