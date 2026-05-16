import os


def load_env_file(path=".env"):
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


load_env_file()

from common import app, init_db
import alerts  # noqa: F401
import communication  # noqa: F401
import websites  # noqa: F401
from websites import start_scheduler
from common import get_db


init_db()
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    start_scheduler()


@app.route('/health')
def health():
    """Simple health check endpoint.

    Returns 200 OK if the app can connect to the SQLite DB.
    """
    try:
        db = get_db()
        db.execute('SELECT 1').fetchone()
        return 'OK', 200
    except Exception:
        return 'Unhealthy', 500


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=True, host=host, port=port)
