import os
import threading
import webbrowser

from flask import Flask
from web.routes import register_routes
from web.api import register_api
from recorder.engine import RecordingEngine
from recorder.settings_manager import SettingsManager


def create_app():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    app = Flask(
        __name__,
        static_folder="static",
        template_folder="templates",
    )

    settings = SettingsManager(base_dir)
    engine = RecordingEngine(base_dir, settings)

    app.config["BASE_DIR"] = base_dir
    app.config["engine"] = engine
    app.config["settings"] = settings

    register_routes(app)
    register_api(app)

    return app


if __name__ == "__main__":
    app = create_app()
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    print("Screen Recorder WebUI running at http://0.0.0.0:5000")
    print("Access from mobile: http://<your-pc-ip>:5000")
    print("WARNING: No authentication. Anyone on your network can control this recorder.")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
