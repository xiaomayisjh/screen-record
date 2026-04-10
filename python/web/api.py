import json
import time
import os
from flask import jsonify, request, send_from_directory, Response
from recorder.settings_manager import ENCODER_NAMES


def register_api(app):

    def _engine():
        return app.config["engine"]

    def _settings():
        return app.config["settings"]

    @app.route("/api/status")
    def api_status():
        return jsonify(_engine().get_state())

    @app.route("/api/record/start", methods=["POST"])
    def api_start():
        data = request.get_json(force=True, silent=True) or {}
        try:
            _engine().start_recording({
                "filename": data.get("filename"),
                "source": data.get("source", "desktop"),
                "window_title": data.get("window_title", ""),
                "webcam": data.get("webcam", False),
                "webcam_device": data.get("webcam_device", ""),
            })
            return jsonify({"ok": True, "filename": _engine()._filename})
        except RuntimeError as e:
            return jsonify({"ok": False, "error": str(e)}), 409

    @app.route("/api/record/stop", methods=["POST"])
    def api_stop():
        _engine().stop_recording()
        return jsonify({"ok": True})

    @app.route("/api/files")
    def api_files():
        return jsonify({"files": _engine().list_files()})

    @app.route("/api/files/<name>", methods=["DELETE"])
    def api_delete_file(name):
        if _engine().delete_file(name):
            return jsonify({"ok": True})
        return jsonify({"error": "File not found"}), 404

    @app.route("/api/files/<name>/download")
    def api_download_file(name):
        safe_name = os.path.basename(name)
        return send_from_directory(
            _engine().captures_dir, safe_name, as_attachment=True
        )

    @app.route("/api/settings")
    def api_get_settings():
        return jsonify(_settings().get_all())

    @app.route("/api/settings", methods=["PUT"])
    def api_update_settings():
        data = request.get_json(force=True, silent=True) or {}
        return jsonify(_settings().update(data))

    @app.route("/api/devices")
    def api_devices():
        engine = _engine()
        dshow = engine.device_enumerator.list_all()

        audio_devices = []
        try:
            for i in range(engine.audio_recorder.get_device_count()):
                if engine.audio_recorder.is_input_device(i):
                    audio_devices.append({
                        "id": i,
                        "name": engine.audio_recorder.get_device_name(i),
                        "api": engine.audio_recorder.get_api_name(i),
                    })
        except Exception:
            pass

        return jsonify({
            "audio": audio_devices,
            "webcam": dshow.get("webcam", []),
        })

    @app.route("/api/events")
    def api_events():
        def event_stream():
            engine = _engine()
            last_version = 0
            try:
                while True:
                    version, state = engine.wait_for_state_change(
                        last_version, timeout=1.0
                    )
                    state_json = json.dumps(state)
                    if version > last_version or state.get("recording"):
                        yield f"data: {state_json}\n\n"
                        last_version = version
                        if state.get("recording"):
                            time.sleep(0.5)
            except GeneratorExit:
                return

        return Response(
            event_stream(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.route("/api/encoders")
    def api_available_encoders():
        available = _settings().detect_available_encoders()
        encoders_list = []
        for enc in available:
            encoders_list.append({
                "id": enc,
                "name": ENCODER_NAMES.get(enc, enc),
                "is_hardware": enc in ("h264_nvenc", "h264_qsv", "h264_amf")
            })
        return jsonify({"encoders": encoders_list})

    @app.route("/api/encoders/best")
    def api_best_encoder():
        best = _settings().get_best_encoder()
        return jsonify({
            "encoder": best,
            "name": ENCODER_NAMES.get(best, best),
            "is_hardware": best in ("h264_nvenc", "h264_qsv", "h264_amf")
        })

    @app.route("/api/filename/next")
    def api_next_filename():
        return jsonify({"filename": _engine().generate_filename()})
