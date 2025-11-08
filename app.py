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
from collections import OrderedDict
from threading import RLock
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

class _TimedCache:
    """Thread-safe TTL cache with simple LRU eviction."""

    def __init__(self, ttl_seconds: float, max_entries: int) -> None:
        self._ttl = float(ttl_seconds)
        self._max_entries = max_entries
        self._data: "OrderedDict[str, tuple[float, Any]]" = OrderedDict()
        self._lock = RLock()

    def get(self, key: str) -> Optional[Any]:
        cutoff = time.time()
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            expires_at, value = entry
            if expires_at < cutoff:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        expires_at = time.time() + self._ttl
        with self._lock:
            self._data[key] = (expires_at, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max_entries:
                self._data.popitem(last=False)

    def purge_expired(self) -> None:
        cutoff = time.time()
        with self._lock:
            expired = [key for key, (expires_at, _) in self._data.items() if expires_at < cutoff]
            for key in expired:
                self._data.pop(key, None)


def _configure_app(flask_app: Flask) -> None:
    """Populate default configuration from environment variables."""

    flask_app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production-123456789")
    flask_app.config.setdefault("SESSION_TYPE", os.getenv("SESSION_TYPE", "filesystem"))
    flask_app.config.setdefault("SESSION_PERMANENT", False)
    flask_app.config.setdefault("SESSION_USE_SIGNER", True)

    session_dir = Path(os.getenv("SESSION_FILE_DIR", Path.cwd() / "flask_session")).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    flask_app.config.setdefault("SESSION_FILE_DIR", str(session_dir))

    download_env = os.getenv("DOWNLOAD_FOLDER")
    download_root_selected = bool(download_env)
    if download_env:
        candidate = Path(download_env)
    else:
        candidate = Path.home() / "Downloads"

    try:
        download_root = candidate.expanduser().resolve()
        download_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        fallback = (Path.cwd() / "downloads").resolve()
        fallback.mkdir(parents=True, exist_ok=True)
        download_root = fallback
        if download_env:
            download_root_selected = True

    flask_app.config.setdefault("DOWNLOAD_ROOT", download_root)
    flask_app.config.setdefault("DOWNLOAD_ROOT_SELECTED", download_root_selected)

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

_video_info_cache = _TimedCache(ttl_seconds=600, max_entries=64)
_search_cache = _TimedCache(ttl_seconds=300, max_entries=64)


def _parse_duration(value: Any) -> Optional[int]:
    """Convert yt-dlp duration field (seconds or timestamp string) to seconds."""

    if isinstance(value, (int, float)):
        if value < 0:
            return None
        return int(value)
    if isinstance(value, str):
        total = 0
        parts = value.split(":")
        try:
            for part in parts:
                total = total * 60 + int(part)
        except ValueError:
            return None
        return total
    return None


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


def _apply_download_root(path: Path) -> Path:
    resolved = _download_manager.set_download_root(path)
    app.config["DOWNLOAD_ROOT"] = resolved
    app.config["DOWNLOAD_ROOT_SELECTED"] = True
    return resolved


def _open_directory_dialog() -> Optional[Path]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Folder selection dialog is not available on this system.") from exc

    root = tk.Tk()
    root.withdraw()
    try:
        try:
            root.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass
        root.update_idletasks()
        selected = filedialog.askdirectory(parent=root, title="Select download folder")
    finally:
        root.destroy()

    if not selected:
        return None
    return Path(selected).expanduser()


@app.before_request
def _cleanup_downloads() -> None:
    _cleanup_counter["value"] += 1
    if _cleanup_counter["value"] % app.config["CLEANUP_EVERY_N_REQUESTS"] == 0:
        _download_manager.cleanup_expired()
        _video_info_cache.purge_expired()
        _search_cache.purge_expired()


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


@app.route("/api/download_directory")
def get_download_directory() -> Any:
    current = Path(app.config["DOWNLOAD_ROOT"])
    return jsonify(
        {
            "directory": str(current),
            "user_selected": bool(app.config.get("DOWNLOAD_ROOT_SELECTED", False)),
        }
    )


@app.route("/api/download_directory/select", methods=["POST"])
def select_download_directory() -> Any:
    payload = request.get_json(silent=True) or {}
    manual_path = payload.get("path") if isinstance(payload, dict) else None

    if manual_path:
        path_str = str(manual_path).strip()
        if not path_str:
            return jsonify({"error": "Path cannot be empty."}), 400
        candidate = Path(path_str).expanduser()
    else:
        try:
            choice = _open_directory_dialog()
        except RuntimeError as exc:
            return jsonify({"error": str(exc), "code": "dialog_unavailable"}), 501
        if not choice:
            return jsonify({"error": "No directory selected."}), 400
        candidate = choice

    try:
        resolved = _apply_download_root(candidate)
    except Exception as exc:  # noqa: BLE001
        LOG.error("Failed to set download directory", exc_info=True)
        return jsonify({"error": f"Could not use directory: {exc}"}), 400

    return jsonify({"directory": str(resolved), "user_selected": True})


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
    cache_key = url
    cached = _video_info_cache.get(cache_key)
    if cached:
        LOG.debug("video_info cache hit for %s", url)
        return jsonify(cached)

    started = time.time()
    options = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": False,
        "writesubtitles": False,
        "writeautomaticsub": False,
    }
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

    # Prefer https thumbnails when available
    thumbnail = info.get("thumbnail")
    if not thumbnail:
        thumbs = info.get("thumbnails") or []
        if thumbs:
            thumbnail = thumbs[-1].get("url")
    if isinstance(thumbnail, str) and thumbnail.startswith("http://"):
        thumbnail = "https://" + thumbnail[len("http://"):]

    response_payload = {
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "view_count": info.get("view_count"),
        "description": info.get("description"),
        "website": info.get("extractor_key"),
        "thumbnail": thumbnail,
        "formats": formats,
        "entries": entries,
        "subtitles": info.get("subtitles", {}),
    }
    _video_info_cache.set(cache_key, response_payload)
    elapsed = time.time() - started
    LOG.info("video_info fetched in %.2fs for %s", elapsed, url)
    return jsonify(response_payload)


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
    cache_key = f"{search_url}"
    cached = _search_cache.get(cache_key)
    if cached:
        LOG.debug("search cache hit for %s", query)
        return jsonify(cached)

    started = time.time()
    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True,
        "playlistend": limit,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            search_results = ydl.extract_info(search_url, download=False)
    except Exception as exc:  # noqa: BLE001
        raise BadRequest(str(exc)) from exc

    videos = []
    for entry in search_results.get("entries", []) or []:
        duration_value = _parse_duration(entry.get("duration")) or _parse_duration(entry.get("duration_string"))
        view_count = entry.get("view_count")
        if isinstance(view_count, str) and view_count.isdigit():
            view_count = int(view_count)

        thumbnail = entry.get("thumbnail")
        if not thumbnail:
            thumbs = entry.get("thumbnails") or []
            if thumbs:
                thumbnail = thumbs[-1].get("url")
        if isinstance(thumbnail, str) and thumbnail.startswith("http://"):
            thumbnail = "https://" + thumbnail[len("http://"):]

        videos.append(
            {
                "id": entry.get("id"),
                "title": entry.get("title"),
                "url": f"https://www.youtube.com/watch?v={entry.get('id')}" if entry.get("id") else None,
                "thumbnail": thumbnail,
                "uploader": entry.get("uploader"),
                "duration": duration_value,
                "view_count": view_count,
            }
        )
    payload = {"query": query, "results": videos}
    _search_cache.set(cache_key, payload)
    elapsed = time.time() - started
    LOG.info("search_youtube fetched in %.2fs for %s", elapsed, query)
    return jsonify(payload)


@app.route("/api/options")
def get_options() -> Any:
    return jsonify(
        {
            "max_parallel_downloads": app.config["MAX_CONCURRENT_DOWNLOADS"],
            "speed_limit": None,
            "supported_formats": ["mp4", "mp3", "webm", "m4a", "wav", "aac", "flac"],
            "default_download_folder": str(app.config["DOWNLOAD_ROOT"]),
            "download_folder_selected": bool(app.config.get("DOWNLOAD_ROOT_SELECTED", False)),
            "history_export_formats": ["json", "csv"],
            "theme_modes": ["light", "dark", "auto"],
            "max_batch_urls": 10,
            "google_drive_enabled": HAS_GOOGLE_DRIVE,
        }
    )


@app.errorhandler(BadRequest)
def handle_bad_request(error):
    """Handle BadRequest exceptions and return JSON for API routes."""
    if request.path.startswith('/api/'):
        return jsonify({"error": str(error.description)}), error.code
    return error


@app.errorhandler(NotFound)
def handle_not_found(error):
    """Handle NotFound exceptions and return JSON for API routes."""
    if request.path.startswith('/api/'):
        return jsonify({"error": "Resource not found"}), error.code
    return error


@app.errorhandler(Exception)
def handle_general_error(error):
    """Handle general exceptions and return JSON for API routes."""
    if request.path.startswith('/api/'):
        LOG.exception("Unhandled API error")
        return jsonify({"error": "Internal server error"}), 500
    LOG.exception("Unhandled error")
    return error


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=bool(os.getenv("FLASK_DEBUG", "1")), host="0.0.0.0", port=port)