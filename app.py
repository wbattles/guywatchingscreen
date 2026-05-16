import os

from flask import send_from_directory

from common import app, init_db, get_db
import alerts  # noqa: F401
import communication  # noqa: F401
import websites  # noqa: F401
from websites import start_scheduler


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def should_start_scheduler():
    if os.environ.get("RUN_SCHEDULER") is not None:
        return env_flag("RUN_SCHEDULER")
    if env_flag("FLASK_DEBUG"):
        return os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    return __name__ == "__main__"


init_db()
if should_start_scheduler():
    start_scheduler()


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/health")
def health():
    try:
        get_db().execute("SELECT 1").fetchone()
        return "OK", 200
    except Exception:
        return "Unhealthy", 500


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=env_flag("FLASK_DEBUG"), host=host, port=port)
