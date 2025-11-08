from __future__ import annotations

import csv
import io
import json
import logging
import mimetypes
import os
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from flask import Flask, jsonify, render_template, request, send_file, session
from flask_session import Session
from werkzeug.exceptions import BadRequest, NotFound

import yt_dlp

from downloader import DownloadJob, DownloadManager, JobStatus

try:  # Optional Google Drive dependencies
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from oauth2client.service_account import ServiceAccountCredentials

    HAS_GOOGLE_DRIVE = True
except Exception:  # noqa: BLE001
    HAS_GOOGLE_DRIVE = False


LOG = logging.getLogger(__name__)

app = Flask(__name__)


def _configure_app(flask_app: Flask) -> None:
    """Populate default configuration from environment variables."""

    flask_app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production-123456789")
    flask_app.config.setdefault("SESSION_TYPE", os.getenv("SESSION_TYPE", "filesystem"))
    flask_app.config.setdefault("SESSION_PERMANENT", False)
    flask_app.config.setdefault("SESSION_USE_SIGNER", True)

    session_dir = Path(os.getenv("SESSION_FILE_DIR", Path.cwd() / "flask_session")).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    flask_app.config.setdefault("SESSION_FILE_DIR", str(session_dir))

    download_root = Path(os.getenv("DOWNLOAD_FOLDER", Path.cwd() / "downloads")).resolve()
    download_root.mkdir(parents=True, exist_ok=True)
    flask_app.config.setdefault("DOWNLOAD_ROOT", download_root)

    flask_app.config.setdefault("MAX_CONCURRENT_DOWNLOADS", int(os.getenv("MAX_DOWNLOADS", "4")))
    flask_app.config.setdefault("JOB_RETENTION_HOURS", int(os.getenv("JOB_RETENTION_HOURS", "24")))
    flask_app.config.setdefault("CLEANUP_EVERY_N_REQUESTS", int(os.getenv("CLEANUP_INTERVAL", "20")))


_configure_app(app)
Session(app)

mimetypes.init()
logging.basicConfig(level=logging.INFO)

_download_manager = DownloadManager(
    download_root=app.config["DOWNLOAD_ROOT"],
    max_workers=app.config["MAX_CONCURRENT_DOWNLOADS"],
    retention_hours=app.config["JOB_RETENTION_HOURS"],
)

_cleanup_counter = {"value": 0}
app.start_time = time.time()


def _require_json(*required_keys: str) -> Dict[str, Any]:
    if not request.is_json:
        raise BadRequest("Request must include a JSON body.")
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        raise BadRequest("Invalid JSON payload.")
    for key in required_keys:
        if not payload.get(key):
            raise BadRequest(f"{key} is required.")
    return payload


def _job_or_404(download_id: str) -> DownloadJob:
    job = _download_manager.get_job(download_id)
    if not job:
        raise NotFound(f"Download {download_id} not found.")
    return job


def _ensure_client_id() -> str:
    client_id = session.get("client_id")
    if not client_id:
        client_id = str(uuid.uuid4())
        session["client_id"] = client_id
        session.modified = True
    return client_id


def _track_download(download_id: str) -> None:
    downloads = session.setdefault("downloads", [])
    if download_id not in downloads:
        downloads.append(download_id)
        session.modified = True


def _session_download_ids() -> List[str]:
    downloads = session.get("downloads", [])
    return [d for d in downloads if isinstance(d, str)]


def _job_to_response(job: DownloadJob) -> Dict[str, Any]:
    data = job.to_dict()
    data["metadata"] = job.metadata
    return data


def _guess_mimetype(filename: str) -> str:
    mimetype, _ = mimetypes.guess_type(filename)
    return mimetype or "application/octet-stream"


def _aggregate_stats(jobs: Iterable[DownloadJob]) -> Dict[str, Any]:
    total_downloaded = sum(job.downloaded for job in jobs if job.downloaded)
    active = [job for job in jobs if job.status in {JobStatus.QUEUED, JobStatus.PREPARING, JobStatus.DOWNLOADING}]
    completed = [job for job in jobs if job.status == JobStatus.COMPLETED]
    failed = [job for job in jobs if job.status == JobStatus.FAILED]
    average_speed = 0.0
    speeds = [job.speed for job in jobs if job.speed]
    if speeds:
        average_speed = sum(speeds) / len(speeds)
    return {
        "total_downloads": len(list(jobs)),
        "active_downloads": len(active),
        "completed_downloads": len(completed),
        "failed_downloads": len(failed),
        "total_downloaded_bytes": total_downloaded,
        "average_speed": average_speed,
        "server_uptime": int(time.time() - app.start_time),
    }


@app.before_request
def _cleanup_downloads() -> None:
    _cleanup_counter["value"] += 1
    if _cleanup_counter["value"] % app.config["CLEANUP_EVERY_N_REQUESTS"] == 0:
        _download_manager.cleanup_expired()


@app.route("/")
def index() -> str:
    _ensure_client_id()
    return render_template("index.html")


@app.route("/api/start_download", methods=["POST"])
def start_download() -> Any:
    payload = _require_json("url")
    url = str(payload["url"]).strip()
    if not url:
        raise BadRequest("URL is required.")

    format_type = str(payload.get("format", "video")).lower()
    if format_type not in {"video", "audio"}:
        format_type = "video"

    format_id = payload.get("format_id")
    playlist_urls_raw = payload.get("playlist_urls")
    playlist_urls: Optional[List[str]] = None
    if isinstance(playlist_urls_raw, list):
        cleaned = [str(u).strip() for u in playlist_urls_raw if str(u).strip()]
        playlist_urls = cleaned or None

    download_id = str(uuid.uuid4())
    job = DownloadJob(
        job_id=download_id,
        url=url,
        format_type=format_type,
        format_id=format_id,
        playlist_urls=playlist_urls,
        requested_by=_ensure_client_id(),
    )
    _download_manager.start_download(job)
    _track_download(download_id)
    return jsonify({"download_id": download_id, "job": _job_to_response(job)}), 202


@app.route("/api/progress/<download_id>")
def download_progress(download_id: str) -> Any:
    job = _job_or_404(download_id)
    return jsonify(_job_to_response(job))


@app.route("/api/download_file/<download_id>")
def download_file(download_id: str):
    job = _job_or_404(download_id)
    if job.status != JobStatus.COMPLETED or not job.file_path:
        raise BadRequest("Download not completed yet.")
    if not job.file_path.exists():
        raise NotFound("File not found on server.")
    return send_file(
        job.file_path,
        as_attachment=True,
        download_name=job.filename or job.file_path.name,
        mimetype=_guess_mimetype(job.filename),
    )


@app.route("/api/supported_sites")
def supported_sites() -> Any:
    cache_key = "supported_sites_cache"
    cached = app.config.get(cache_key)
    if cached and cached["expires_at"] > time.time():
        return jsonify(cached["payload"])

    try:
        extractors = yt_dlp.list_extractors()
    except Exception as exc:  # noqa: BLE001
        raise BadRequest(str(exc)) from exc

    popular = {
        "youtube",
        "twitter",
        "instagram",
        "tiktok",
        "facebook",
        "vimeo",
        "dailymotion",
        "twitch",
        "reddit",
        "pinterest",
        "linkedin",
        "soundcloud",
        "bandcamp",
    }

    sites = []
    for extractor in extractors:
        name = extractor.IE_NAME.lower()
        if any(p in name for p in popular):
            sites.append(
                {
                    "name": extractor.IE_NAME,
                    "description": getattr(extractor, "IE_DESC", ""),
                }
            )
        if len(sites) >= 50:
            break

    payload = {"sites": sites}
    app.config[cache_key] = {"payload": payload, "expires_at": time.time() + 3600}
    return jsonify(payload)


@app.route("/api/video_info", methods=["POST"])
def video_info() -> Any:
    payload = _require_json("url")
    url = str(payload["url"]).strip()
    options = {"quiet": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001
        raise BadRequest(str(exc)) from exc

    formats = [
        {
            "format_id": fmt.get("format_id"),
            "format_note": fmt.get("format_note"),
            "resolution": fmt.get("resolution"),
            "ext": fmt.get("ext"),
            "filesize": fmt.get("filesize"),
            "vcodec": fmt.get("vcodec"),
            "acodec": fmt.get("acodec"),
            "fps": fmt.get("fps"),
            "abr": fmt.get("abr"),
        }
        for fmt in info.get("formats", [])
    ]

    entries = []
    for entry in info.get("entries", []) or []:
        entries.append({"title": entry.get("title"), "url": entry.get("webpage_url") or entry.get("url")})

    return jsonify(
        {
            "title": info.get("title"),
            "uploader": info.get("uploader"),
            "duration": info.get("duration"),
            "view_count": info.get("view_count"),
            "description": info.get("description"),
            "website": info.get("extractor_key"),
            "thumbnail": info.get("thumbnail"),
            "formats": formats,
            "entries": entries,
            "subtitles": info.get("subtitles", {}),
        }
    )


@app.route("/api/my_downloads")
def my_downloads() -> Any:
    ids = _session_download_ids()
    jobs = [_download_manager.get_job(i) for i in ids]
    jobs = [job for job in jobs if job]
    return jsonify({"downloads": [_job_to_response(job) for job in jobs]})


@app.route("/api/update_yt_dlp", methods=["POST"])
def update_yt_dlp() -> Any:
    result = subprocess.run(
        ["python", "-m", "pip", "install", "--upgrade", "yt-dlp"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        LOG.error("yt-dlp update failed: %s", result.stderr)
        return jsonify({"error": result.stderr.strip() or "Failed to update yt-dlp"}), 500
    return jsonify({"message": result.stdout.strip() or "yt-dlp updated"})


@app.route("/api/upload_to_drive", methods=["POST"])
def upload_to_drive() -> Any:
    if not HAS_GOOGLE_DRIVE:
        return jsonify({"error": "Google Drive support is not available."}), 503

    payload = _require_json("download_id")
    job = _job_or_404(payload["download_id"])
    if job.status != JobStatus.COMPLETED or not job.file_path:
        raise BadRequest("Download must be completed before upload.")

    credentials_path = Path("credentials.json")
    if not credentials_path.exists():
        raise BadRequest("credentials.json file not found for Google Drive upload.")

    try:
        scopes = ["https://www.googleapis.com/auth/drive.file"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(str(credentials_path), scopes)
        service = build("drive", "v3", credentials=creds)
        metadata = {"name": job.filename or job.file_path.name}
        media = MediaFileUpload(str(job.file_path), resumable=True)
        created = service.files().create(body=metadata, media_body=media, fields="id").execute()
    except Exception as exc:  # noqa: BLE001
        raise BadRequest(str(exc)) from exc

    return jsonify({"message": "File uploaded to Google Drive", "file_id": created.get("id")})


@app.route("/api/download_subtitles", methods=["POST"])
def download_subtitles() -> Any:
    payload = _require_json("url")
    language = str(payload.get("language", "en"))
    options = {
        "writesubtitles": True,
        "subtitleslangs": [language],
        "skip_download": True,
        "outtmpl": str(app.config["DOWNLOAD_ROOT"] / "%(title)s.%(ext)s"),
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(payload["url"], download=False)
    except Exception as exc:  # noqa: BLE001
        raise BadRequest(str(exc)) from exc
    return jsonify({"message": "Subtitles processed", "title": info.get("title")})


@app.route("/api/convert_audio", methods=["POST"])
def convert_audio() -> Any:
    payload = _require_json("download_id")
    audio_format = str(payload.get("format", "mp3")).lower()
    job = _job_or_404(payload["download_id"])
    if job.status != JobStatus.COMPLETED or not job.file_path:
        raise BadRequest("Download must be completed before conversion.")

    target = job.file_path.with_suffix(f".{audio_format}")
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(job.file_path),
        str(target),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        LOG.error("ffmpeg conversion failed: %s", result.stderr)
        return jsonify({"error": result.stderr.strip() or "Conversion failed"}), 500
    return jsonify({"message": "Audio converted", "output_file": str(target)}), 200


@app.route("/api/drag_and_drop", methods=["POST"])
def drag_and_drop() -> Any:
    payload = _require_json("urls")
    urls = payload.get("urls")
    if not isinstance(urls, list) or not urls:
        raise BadRequest("urls must be a non-empty list")

    limit = min(len(urls), 10)
    responses = []
    for url in urls[:limit]:
        if not url:
            continue
        download_id = str(uuid.uuid4())
        job = DownloadJob(
            job_id=download_id,
            url=str(url).strip(),
            format_type="video",
            requested_by=_ensure_client_id(),
        )
        _download_manager.start_download(job)
        _track_download(download_id)
        responses.append({"url": url, "download_id": download_id})

    return jsonify({"message": f"Processing {len(responses)} URLs", "downloads": responses})


@app.route("/api/set_speed_limit", methods=["POST"])
def set_speed_limit() -> Any:
    payload = _require_json("speed_limit")
    try:
        limit_kb = int(payload["speed_limit"])
    except (TypeError, ValueError) as exc:
        raise BadRequest("speed_limit must be numeric") from exc
    if limit_kb <= 0:
        _download_manager.set_rate_limit(None)
        return jsonify({"message": "Speed limit disabled"})
    _download_manager.set_rate_limit(limit_kb)
    return jsonify({"message": f"Speed limit set to {limit_kb} KB/s"})


def _jobs_from_session() -> List[DownloadJob]:
    jobs = []
    for download_id in _session_download_ids():
        job = _download_manager.get_job(download_id)
        if job:
            jobs.append(job)
    return jobs


@app.route("/api/export_history_json")
def export_history_json() -> Any:
    jobs = _jobs_from_session()
    history = [_job_to_response(job) for job in jobs]
    response = app.response_class(
        response=json.dumps(history, indent=2),
        mimetype="application/json",
    )
    response.headers["Content-Disposition"] = "attachment; filename=download_history.json"
    return response


@app.route("/api/export_history_csv")
def export_history_csv() -> Any:
    jobs = _jobs_from_session()
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "id",
            "title",
            "filename",
            "status",
            "filesize",
            "progress",
            "completed",
            "error",
        ],
    )
    writer.writeheader()
    for job in jobs:
        data = _job_to_response(job)
        writer.writerow(
            {
                "id": data["id"],
                "title": data["metadata"].get("title"),
                "filename": data.get("filename"),
                "status": data.get("status"),
                "filesize": data.get("filesize"),
                "progress": data.get("progress"),
                "completed": data.get("completed"),
                "error": data.get("error"),
            }
        )
    response = app.response_class(response=buffer.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=download_history.csv"
    return response


@app.route("/api/system_stats")
def system_stats() -> Any:
    jobs = list(_download_manager.list_jobs())
    stats = _aggregate_stats(jobs)
    stats["active_jobs"] = [_job_to_response(job) for job in jobs if job.status in {JobStatus.DOWNLOADING, JobStatus.PREPARING}]
    return jsonify(stats)


@app.route("/api/clear_history", methods=["POST"])
def clear_history() -> Any:
    payload = request.get_json(silent=True) or {}
    download_id = payload.get("download_id") if isinstance(payload, dict) else None

    if download_id:
        downloads = _session_download_ids()
        if download_id in downloads:
            downloads.remove(download_id)
            session["downloads"] = downloads
            session.modified = True
        job = _download_manager.get_job(download_id)
        if job and job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            _download_manager.clear_job(download_id)
        return jsonify({"message": f"Download {download_id} removed"})

    session.pop("downloads", None)
    session.modified = True
    return jsonify({"message": "History cleared"})


@app.route("/api/retry_download", methods=["POST"])
def retry_download() -> Any:
    payload = _require_json("download_id")
    download_id = payload["download_id"]
    job = _job_or_404(download_id)
    if job.status not in {JobStatus.FAILED, JobStatus.CANCELLED}:
        raise BadRequest("Only failed or cancelled downloads can be retried.")
    new_job = _download_manager.retry_job(download_id)
    if not new_job:
        raise NotFound("Unable to retry download.")
    return jsonify({"message": "Download restarted", "job": _job_to_response(new_job)})


@app.route("/api/cancel_download", methods=["POST"])
def cancel_download() -> Any:
    payload = _require_json("download_id")
    job = _job_or_404(payload["download_id"])
    if job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
        raise BadRequest("Download cannot be cancelled in its current state.")
    _download_manager.cancel_job(payload["download_id"])
    return jsonify({"message": "Download cancelled", "job": _job_to_response(job)})


@app.route("/api/search_youtube", methods=["POST"])
def search_youtube() -> Any:
    payload = _require_json("query")
    query = str(payload["query"]).strip()
    limit = min(int(payload.get("limit", 10)), 30)
    search_url = f"ytsearch{limit}:{query}"
    options = {"quiet": True, "no_warnings": True, "extract_flat": True}
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            search_results = ydl.extract_info(search_url, download=False)
    except Exception as exc:  # noqa: BLE001
        raise BadRequest(str(exc)) from exc

    videos = []
    for entry in search_results.get("entries", []) or []:
        videos.append(
            {
                "id": entry.get("id"),
                "title": entry.get("title"),
                "url": f"https://www.youtube.com/watch?v={entry.get('id')}" if entry.get("id") else None,
                "thumbnail": entry.get("thumbnail"),
                "uploader": entry.get("uploader"),
                "duration": entry.get("duration"),
                "view_count": entry.get("view_count"),
            }
        )
    return jsonify({"query": query, "results": videos})


@app.route("/api/options")
def get_options() -> Any:
    return jsonify(
        {
            "max_parallel_downloads": app.config["MAX_CONCURRENT_DOWNLOADS"],
            "speed_limit": None,
            "supported_formats": ["mp4", "mp3", "webm", "m4a", "wav", "aac", "flac"],
            "default_download_folder": str(app.config["DOWNLOAD_ROOT"]),
            "history_export_formats": ["json", "csv"],
            "theme_modes": ["light", "dark", "auto"],
            "max_batch_urls": 10,
            "google_drive_enabled": HAS_GOOGLE_DRIVE,
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=bool(os.getenv("FLASK_DEBUG", "1")), host="0.0.0.0", port=port)