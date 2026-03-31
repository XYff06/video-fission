from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, request, send_from_directory
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


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def build_segments(split_points: list[float], video_duration_seconds: float) -> list[dict]:
    normalized_points = sorted(
        {
            round(float(point), 3)
            for point in split_points
            if isinstance(point, (int, float)) and 0 <= float(point) < float(video_duration_seconds)
        }
    )

    if not normalized_points or normalized_points[0] != 0:
        normalized_points = [0.0, *normalized_points]

    boundaries = [*normalized_points, round(float(video_duration_seconds), 3)]
    segments: list[dict] = []

    for index in range(len(boundaries) - 1):
        start_seconds = boundaries[index]
        end_seconds = boundaries[index + 1]
        duration_seconds = round(end_seconds - start_seconds, 3)
        if duration_seconds <= 0:
            continue
        segments.append(
            {
                "index": index + 1,
                "startSeconds": round(start_seconds, 3),
                "endSeconds": round(end_seconds, 3),
                "durationSeconds": duration_seconds,
            }
        )

    return segments


def split_video_ffmpeg(input_path: Path, output_dir: Path, source_name: str, segments: list[dict]) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    extension = input_path.suffix.lower() or ".mp4"
    base_name = secure_filename(Path(source_name).stem) or input_path.stem
    clip_payload = []

    for segment in segments:
        clip_name = f"{base_name}-segment-{segment['index']:03d}{extension}"
        clip_path = output_dir / clip_name
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            str(segment["startSeconds"]),
            "-i",
            str(input_path),
            "-t",
            str(segment["durationSeconds"]),
            "-c",
            "copy",
            str(clip_path),
        ]
        subprocess.run(command, check=True, capture_output=True)
        clip_payload.append(
            {
                **segment,
                "filename": clip_name,
            }
        )

    return clip_payload


@app.post("/api/split")
def split_videos():
    payload = request.get_json(silent=True) or {}
    job_id = payload.get("jobId")
    videos = payload.get("videos") or []

    if not job_id:
        return jsonify({"message": "缺少 jobId"}), 400
    if not videos:
        return jsonify({"message": "缺少可处理的视频数据"}), 400
    if not ffmpeg_available():
        return jsonify({"message": "系统中未找到 ffmpeg，无法执行视频切分"}), 500

    job_upload_dir = UPLOAD_ROOT / job_id
    if not job_upload_dir.exists():
        return jsonify({"message": "找不到对应的上传任务目录"}), 404

    split_manifest = {
        "jobId": job_id,
        "scope": payload.get("scope", "single"),
        "thresholdPercent": payload.get("thresholdPercent"),
        "videos": [],
    }

    split_results = []

    for video in videos:
        video_id = video.get("videoId")
        stored_name = video.get("storedName")
        source_name = video.get("sourceName")
        markers = video.get("markers") or []
        split_points = video.get("splitPoints") or []
        video_duration_seconds = video.get("videoDurationSeconds")

        if not video_id or not stored_name or not source_name:
            split_results.append(
                {
                    "videoId": video_id,
                    "sourceName": source_name,
                    "success": False,
                    "message": "视频参数不完整",
                }
            )
            continue

        has_white_marker = any(marker.get("state") == "split" for marker in markers)
        if has_white_marker:
            split_results.append(
                {
                    "videoId": video_id,
                    "sourceName": source_name,
                    "success": False,
                    "message": "仍然存在白色分割线，拒绝处理",
                }
            )
            continue

        try:
            input_path = (job_upload_dir / stored_name).resolve()
            if not input_path.exists():
                raise FileNotFoundError("找不到对应的原始视频文件")

            segments = build_segments(split_points, float(video_duration_seconds))
            if not segments:
                raise ValueError("没有可切分的有效片段")

            output_dir = OUTPUT_ROOT / job_id / video_id
            clips = split_video_ffmpeg(
                input_path=input_path,
                output_dir=output_dir,
                source_name=source_name,
                segments=segments,
            )

            split_result = {
                "videoId": video_id,
                "sourceName": source_name,
                "success": True,
                "clipCount": len(clips),
                "clips": [
                    {
                        **clip,
                        "downloadUrl": f"/api/download/{job_id}/{video_id}/{clip['filename']}",
                    }
                    for clip in clips
                ],
            }
            split_results.append(split_result)
            split_manifest["videos"].append(
                {
                    "videoId": video_id,
                    "sourceName": source_name,
                    "storedName": stored_name,
                    "splitPoints": split_points,
                    "segments": segments,
                }
            )
        except subprocess.CalledProcessError as exception:
            error_message = exception.stderr.decode("utf-8", errors="ignore").strip() or "ffmpeg 切分失败"
            split_results.append(
                {
                    "videoId": video_id,
                    "sourceName": source_name,
                    "success": False,
                    "message": error_message,
                }
            )
        except Exception as exception:
            split_results.append(
                {
                    "videoId": video_id,
                    "sourceName": source_name,
                    "success": False,
                    "message": str(exception),
                }
            )

    manifest_path = OUTPUT_ROOT / job_id / "split-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(split_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    success_count = sum(1 for item in split_results if item["success"])
    status_code = 200 if success_count else 500
    return (
        jsonify(
            {
                "jobId": job_id,
                "message": f"已提交{len(videos)}个视频的分割点，成功处理{success_count}个",
                "results": split_results,
            }
        ),
        status_code,
    )


@app.get("/api/download/<job_id>/<video_id>/<path:filename>")
def download_clip(job_id: str, video_id: str, filename: str):
    target_dir = (OUTPUT_ROOT / job_id / video_id).resolve()
    if not target_dir.exists():
        return jsonify({"message": "文件不存在"}), 404
    if OUTPUT_ROOT.resolve() not in target_dir.parents:
        return jsonify({"message": "非法路径"}), 400
    return send_from_directory(target_dir, filename, as_attachment=False)


@app.get("/api/health")
def health_check():
    return jsonify(
        {
            "message": "ok",
            "ffmpegAvailable": ffmpeg_available(),
        }
    )


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "http://127.0.0.1:5173"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
