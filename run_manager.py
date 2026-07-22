import threading
from datetime import datetime

from fetcher import run_fetcher

state = {
    "running": False,
    "results": None,
    "error": None,
    "last_run": None,
    "run_start": None,
}

_lock = threading.Lock()
_STALE_SECONDS = 300


def start_run(progress_callback, done_callback, progress_percent_callback=None):
    """Start a fetch run in a background thread.

    Returns True if a run was started, False if one is already in progress
    (and not stale).
    """
    with _lock:
        if state["running"]:
            if state["run_start"]:
                try:
                    started = datetime.fromisoformat(state["run_start"])
                    if (datetime.now() - started).total_seconds() <= _STALE_SECONDS:
                        return False
                except (ValueError, TypeError):
                    pass
            # No run_start, unparsable, or stale beyond the timeout — reclaim it below.

        state["running"] = True
        state["run_start"] = datetime.now().isoformat()
        state["results"] = None
        state["error"] = None

    def worker():
        try:
            result = run_fetcher(
                progress_callback=progress_callback,
                progress_percent_callback=progress_percent_callback,
            )
            state["results"] = result
            state["error"] = result.get("error")
            done_callback(result)
        except Exception as e:
            state["error"] = str(e)
            done_callback({"success": False, "total_rows": 0, "results": [], "error": str(e)})
        finally:
            state["running"] = False
            state["last_run"] = datetime.now().isoformat()

    threading.Thread(target=worker, daemon=True).start()
    return True


def reset():
    state["running"] = False
    state["run_start"] = None
