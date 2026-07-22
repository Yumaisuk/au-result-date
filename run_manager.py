import threading
from datetime import datetime

from fetcher import run_fetcher

state = {
    "running": False,
    "results": None,
    "error": None,
    "last_run": None,
    "run_start": None,
    "run_id": None,
    "last_heartbeat": None,
}

_lock = threading.Lock()
_next_run_id = 0

# A run is only considered "stuck" (and reclaimable) if it hasn't reported any
# progress for this long - NOT simply because the run has been going for a
# while. Fetching many channels across 4 platforms can legitimately take
# well over 5 minutes, so timing out on total elapsed time risked treating a
# genuinely in-progress run as stale and starting a second one concurrently,
# with both writing to the same Google Sheet range at once.
_STALE_HEARTBEAT_SECONDS = 120


def start_run(progress_callback, done_callback, progress_percent_callback=None):
    """Start a fetch run in a background thread.

    Returns True if a run was started, False if one is already in progress
    (i.e. it has reported progress within the last _STALE_HEARTBEAT_SECONDS).
    """
    global _next_run_id
    with _lock:
        if state["running"]:
            last_beat = state["last_heartbeat"] or state["run_start"]
            if last_beat:
                try:
                    beat_time = datetime.fromisoformat(last_beat)
                    if (datetime.now() - beat_time).total_seconds() <= _STALE_HEARTBEAT_SECONDS:
                        return False
                except (ValueError, TypeError):
                    pass
            # No heartbeat, unparsable, or stuck beyond the timeout - reclaim it below.

        _next_run_id += 1
        my_run_id = _next_run_id
        now = datetime.now().isoformat()
        state["running"] = True
        state["run_id"] = my_run_id
        state["run_start"] = now
        state["last_heartbeat"] = now
        state["results"] = None
        state["error"] = None

    def tracked_progress_callback(message):
        state["last_heartbeat"] = datetime.now().isoformat()
        progress_callback(message)

    def worker():
        try:
            result = run_fetcher(
                progress_callback=tracked_progress_callback,
                progress_percent_callback=progress_percent_callback,
            )
            if state["run_id"] == my_run_id:
                state["results"] = result
                state["error"] = result.get("error")
            done_callback(result)
        except Exception as e:
            if state["run_id"] == my_run_id:
                state["error"] = str(e)
            done_callback({"success": False, "total_rows": 0, "results": [], "error": str(e)})
        finally:
            # Only clear "running" if we're still the current run - an
            # orphaned/reclaimed run finishing late must not stomp on a
            # newer run's state.
            if state["run_id"] == my_run_id:
                state["running"] = False
                state["last_run"] = datetime.now().isoformat()

    threading.Thread(target=worker, daemon=True).start()
    return True


def reset():
    state["running"] = False
    state["run_start"] = None
    state["run_id"] = None
    state["last_heartbeat"] = None
