import hashlib
import os
from pathlib import Path

from flask import Response

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


def _versioned_index():
    """index.html with a content hash on app.js and style.css.

    Cloudflare tells browsers to cache static files for hours, so without this a deploy
    serves new HTML with the old JS and CSS. The hash changes whenever either file does.
    """
    static = Path(app.static_folder)
    html = (static / "index.html").read_text()
    for name in ("app.js", "style.css"):
        digest = hashlib.sha256((static / name).read_bytes()).hexdigest()[:12]
        html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={digest}"')
    return html


INDEX_HTML = _versioned_index()


@app.route("/")
def index():
    # Always revalidate the page itself, so it never points at stale asset versions.
    html = _versioned_index() if app.debug else INDEX_HTML
    return Response(html, mimetype="text/html", headers={"Cache-Control": "no-cache"})


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
