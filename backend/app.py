from __future__ import annotations

import json
import math
import mimetypes
import shutil
import subprocess
import time
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, request, send_from_directory
from scenedetect import ContentDetector, detect, split_video_ffmpeg
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
MIN_SCENE_SECONDS = 5

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024


def ensure_directories() -> None:
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


ensure_directories()


def get_video_fps(saved_path: Path) -> float:
    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path is None:
        raise Exception("未检测到ffprobe，无法根据秒数计算最小场景长度")
    result = subprocess.run(
        args=[
            ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,r_frame_rate",
            "-of",
            "json",
            str(saved_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise Exception(f"未从视频中读取到视频流: {saved_path.name}")

    for key in ("avg_frame_rate", "r_frame_rate"):
        rate = streams[0].get(key)
        if not rate or rate == "0/0":
            continue
        fps = float(Fraction(rate))
        if fps > 0:
            return fps

    raise Exception(f"无法解析视频帧率: {saved_path.name}")


def serialize_scene(index, start_time, end_time) -> dict:
    start_seconds = round(start_time.get_seconds(), 3)
    end_seconds = round(end_time.get_seconds(), 3)
    return {
        "index": index,
        "startTimecode": start_time.get_timecode(),
        "endTimecode": end_time.get_timecode(),
        "startSeconds": start_seconds,
        "endSeconds": end_seconds,
        "durationSeconds": round(end_seconds - start_seconds, 3),
        "startFrame": start_time.get_frames(),
        "endFrame": end_time.get_frames(),
    }


def split_detected_scenes(saved_path: Path, output_dir: Path, video_id: str, job_id: str, scenes, ) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    result = split_video_ffmpeg(
        input_video_path=str(saved_path),
        scene_list=scenes,
        output_dir=output_dir,
        output_file_template=f"{video_id}-$SCENE_NUMBER.mp4",
        show_progress=False,
        show_output=False,
    )
    if result != 0:
        raise Exception(f"ffmpeg切分失败，返回码: {result}")

    return [
        {
            "filename": clip.name,
            "downloadUrl": f"/api/download/{job_id}/{video_id}/{clip.name}",
            "contentType": mimetypes.guess_type(clip.name)[0] or "video/mp4",
        }
        for clip in sorted(output_dir.glob("*"))
        if clip.is_file()
    ]


def process_single_video(uploaded_file: FileStorage, job_id: str, upload_dir: Path, output_dir: Path, ) -> dict:
    extension = Path(uploaded_file.filename).suffix.lower()
    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise Exception(f"不支持的文件类型: {uploaded_file.filename}")

    safe_name = secure_filename(uploaded_file.filename) or f"video{extension}"
    video_id = f"{Path(safe_name).stem}-{uuid4().hex[:8]}"
    saved_path = upload_dir / f"{video_id}{extension}"
    uploaded_file.save(saved_path)

    fps = get_video_fps(saved_path)
    min_scene_len_frames = max(1, math.ceil(fps * MIN_SCENE_SECONDS))

    started_at = time.perf_counter()
    scenes = detect(
        str(saved_path),
        ContentDetector(threshold=27, min_scene_len=min_scene_len_frames, ),
        start_in_scene=True,
    )
    elapsed = time.perf_counter() - started_at

    scene_payload = [
        serialize_scene(index, start, end)
        for index, (start, end) in enumerate(scenes, start=1)
    ]
    clips = split_detected_scenes(
        saved_path=saved_path,
        output_dir=output_dir / video_id,
        video_id=video_id,
        job_id=job_id,
        scenes=scenes,
    )

    return {
        "videoId": video_id,
        "sourceName": uploaded_file.filename,
        "storedName": saved_path.name,
        "success": True,
        "fps": round(fps, 3),
        "minSceneSeconds": MIN_SCENE_SECONDS,
        "minSceneFrames": min_scene_len_frames,
        "sceneCount": len(scene_payload),
        "processingSeconds": round(elapsed, 2),
        "scenes": scene_payload,
        "clips": clips,
    }


@app.route("/api/process", methods=["POST"])
def process_videos():
    files = [file for file in request.files.getlist("videos") if file and file.filename]
    if not files:
        return jsonify({"message": "请至少上传一个视频文件!!!"}), 400
    if shutil.which("ffmpeg") is None:
        return jsonify({"message": "未检测到ffmpeg，无法执行视频分割"}), 500
    if shutil.which("ffprobe") is None:
        return jsonify({"message": "未检测到ffprobe，无法读取视频帧率"}), 500
    job_id = uuid4().hex
    job_upload_dir = UPLOAD_ROOT / job_id
    job_output_dir = OUTPUT_ROOT / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)
    job_output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for uploaded_file in files:
        try:
            results.append(
                process_single_video(
                    uploaded_file=uploaded_file,
                    job_id=job_id,
                    upload_dir=job_upload_dir,
                    output_dir=job_output_dir,
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


@app.get("/api/download/<job_id>/<video_id>/<path:filename>")
def download_clip(job_id: str, video_id: str, filename: str):
    target_dir = (OUTPUT_ROOT / job_id / video_id).resolve()
    if not target_dir.exists():
        return jsonify({"message": "文件不存在。"}), 404
    if OUTPUT_ROOT.resolve() not in target_dir.parents:
        return jsonify({"message": "非法路径。"}), 400
    return send_from_directory(target_dir, filename, as_attachment=False)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
