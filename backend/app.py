from __future__ import annotations

import shutil
import time
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, request
from scenedetect import ContentDetector, detect
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_ROOT = DATA_DIR / "uploads"
OUTPUT_ROOT = DATA_DIR / "outputs"

ALLOWED_VIDEO_EXTENSIONS = {
    ".mpe",
    ".mpeg",
    ".ogm",
    ".mkv",
    ".mpg",
    ".wmv",
    ".webm",
    ".ogv",
    ".mov",
    ".m4v",
    ".asx",
    ".mp4",
    ".avi",
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024


def ensure_directories() -> None:
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


ensure_directories()


def serialize_scene(index: int, start_time, end_time) -> dict:
    start_seconds = round(start_time.get_seconds(), 3)
    end_seconds = round(end_time.get_seconds(), 3)
    return {
        "index": index,
        "startSeconds": start_seconds,
        "endSeconds": end_seconds,
        "durationSeconds": round(end_seconds - start_seconds, 3),
    }


def process_single_video(uploaded_file: FileStorage, upload_dir: Path) -> dict:
    extension = Path(uploaded_file.filename).suffix.lower()
    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise Exception(f"不支持的文件类型: {uploaded_file.filename}")

    safe_name = secure_filename(uploaded_file.filename) or f"video{extension}"
    video_id = f"{Path(safe_name).stem}-{uuid4().hex[:8]}"
    saved_path = upload_dir / f"{video_id}{extension}"
    uploaded_file.save(saved_path)

    started_at = time.perf_counter()
    scenes = detect(
        str(saved_path),
        ContentDetector(),
        start_in_scene=True,
    )
    elapsed = time.perf_counter() - started_at

    scene_payload = [
        serialize_scene(index, start, end)
        for index, (start, end) in enumerate(scenes, start=1)
    ]

    return {
        "videoId": video_id,
        "sourceName": uploaded_file.filename,
        "storedName": saved_path.name,
        "success": True,
        "sceneCount": len(scene_payload),
        "processingSeconds": round(elapsed, 2),
        "scenes": scene_payload,
    }


@app.route("/api/process", methods=["POST"])
def process_videos():
    files = [file for file in request.files.getlist("videos") if file and file.filename]
    if not files:
        return jsonify({"message": "请至少上传一个视频文件"}), 400

    job_id = uuid4().hex
    job_upload_dir = UPLOAD_ROOT / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for uploaded_file in files:
        try:
            results.append(
                process_single_video(
                    uploaded_file=uploaded_file,
                    upload_dir=job_upload_dir,
                )
            )
        except Exception as exception:
            results.append(
                {
                    "sourceName": uploaded_file.filename,
                    "success": False,
                    "error": str(exception),
                }
            )

    success_count = sum(1 for item in results if item["success"])
    status_code = 200 if success_count else 500

    return (
        jsonify(
            {
                "jobId": job_id,
                "message": f"共处理{len(results)}个文件，成功{success_count}个",
                "results": results,
            }
        ),
        status_code,
    )


@app.post("/api/split")
def split_videos():
    payload = request.get_json(silent=True) or {}
    job_id = payload.get("jobId")
    videos = payload.get("videos") or []

    if not job_id:
        return jsonify({"message": "缺少 jobId"}), 400
    if not videos:
        return jsonify({"message": "缺少可处理的视频数据"}), 400


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "http://127.0.0.1:5173"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    if shutil.which("ffmpeg") is None:
        return jsonify({"message": "系统中未找到ffmpeg"}), 500
    return response


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
