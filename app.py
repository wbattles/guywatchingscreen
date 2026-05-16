import os

from common import app, init_db
import alerts  # noqa: F401
import communication  # noqa: F401
import websites  # noqa: F401
from websites import start_scheduler


init_db()
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    start_scheduler()


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=True, host=host, port=port)
