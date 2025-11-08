"""Download management primitives for the Universal Video Downloader app."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import yt_dlp

__all__ = [
    "DownloadCancelled",
    "DownloadJob",
    "DownloadManager",
    "JobStatus",
]

LOG = logging.getLogger(__name__)


def _friendly_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    lowered = message.lower()
    if "sign in to confirm you\u2019re not a bot" in lowered or "sign in to confirm you're not a bot" in lowered:
        return (
            "YouTube blocked this request and wants verification. Upload a youtube.com cookies.txt file "
            "under Settings → Cookies and retry after refreshing."
        )
    if "this video is private" in lowered:
        return "This video is private. Ask the uploader for access before downloading."
    if "members-only" in lowered:
        return "This video is for channel members only. Sign in with an account that has access."
    if "premium" in lowered:
        return "This content requires a paid subscription. Provide cookies from an account with access."
    if message:
        return message
    return "Download failed due to an unknown error."


class JobStatus(str, Enum):
    """Stable set of states a download job can be in."""

    QUEUED = "queued"
    PREPARING = "preparing"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadCancelled(RuntimeError):
    """Raised internally when a cancellation is requested."""


@dataclass(slots=True)
class DownloadJob:
    job_id: str
    url: str
    format_type: str = "video"
    format_id: Optional[str] = None
    playlist_urls: Optional[List[str]] = None
    requested_by: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0
    filename: str = ""
    file_path: Optional[Path] = None
    filesize: int = 0
    downloaded: int = 0
    speed: float = 0.0
    eta: int = 0
    error: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)
    cookie_file: Optional[Path] = None
    updated_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    cancel_requested: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def to_dict(self) -> Dict[str, object]:
        """Serialize the job for API responses."""
        with self._lock:
            return {
                "id": self.job_id,
                "url": self.url,
                "format_type": self.format_type,
                "format_id": self.format_id,
                "status": self.status.value,
                "progress": round(self.progress, 2),
                "filename": self.filename,
                "filesize": self.filesize,
                "downloaded": self.downloaded,
                "speed": self.speed,
                "eta": self.eta,
                "error": self.error,
                "completed": self.status == JobStatus.COMPLETED,
                "file_ready": self.file_path is not None and self.status == JobStatus.COMPLETED,
                "metadata": self.metadata,
                "created_at": self.created_at.isoformat(),
                "updated_at": self.updated_at.isoformat(),
                "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            }

    def mark_cancel_requested(self) -> None:
        with self._lock:
            self.cancel_requested = True
            self.updated_at = datetime.utcnow()

    def update_from_hook(self, info: Dict[str, object]) -> None:
        with self._lock:
            status = info.get("status")
            if status == "downloading":
                self.status = JobStatus.DOWNLOADING
                self.filename = Path(str(info.get("filename", self.filename))).name
                self.downloaded = int(info.get("downloaded_bytes" or 0) or 0)
                total = info.get("total_bytes") or info.get("total_bytes_estimate")
                if total:
                    self.filesize = int(total)
                    self.progress = max(0.0, min(100.0, (self.downloaded / self.filesize) * 100.0))
                self.speed = float(info.get("speed") or 0.0)
                self.eta = int(info.get("eta") or 0)
            elif status == "finished":
                filename = info.get("filename")
                if filename:
                    self.filename = Path(str(filename)).name
                    self.file_path = Path(filename)
                self.progress = 100.0
                self.status = JobStatus.COMPLETED
                self.completed_at = datetime.utcnow()
            self.updated_at = datetime.utcnow()


class DownloadManager:
    """Coordinate yt-dlp downloads and expose job state for the API."""

    def __init__(
        self,
        download_root: Path,
        *,
        max_workers: int = 4,
        retention_hours: int = 24,
    ) -> None:
        self._root = Path(download_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="yt_dlp")
        self._jobs: Dict[str, DownloadJob] = {}
        self._futures: Dict[str, Future] = {}
        self._lock = threading.RLock()
        self._retention = timedelta(hours=retention_hours)
        self._rate_limit: Optional[int] = None

    def set_download_root(self, download_root: Path) -> Path:

        new_root = Path(download_root).expanduser().resolve()
        new_root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._root = new_root
        return new_root

    def start_download(
        self,
        job: DownloadJob,
    ) -> DownloadJob:
        with self._lock:
            if job.job_id in self._jobs:
                raise ValueError(f"Job {job.job_id} already exists")
            self._jobs[job.job_id] = job
            future = self._executor.submit(self._run_job, job.job_id)
            self._futures[job.job_id] = future
            LOG.debug("Job %s submitted", job.job_id)
        return job

    def get_job(self, job_id: str) -> Optional[DownloadJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, ids: Optional[Iterable[str]] = None) -> List[DownloadJob]:
        with self._lock:
            if ids is None:
                return list(self._jobs.values())
            return [self._jobs[i] for i in ids if i in self._jobs]

    def cancel_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job:
            return False
        job.mark_cancel_requested()
        with job._lock:
            job.status = JobStatus.CANCELLED
            job.error = "Download cancelled by user"
            job.updated_at = datetime.utcnow()
        return True

    def set_rate_limit(self, kilobytes_per_second: Optional[int]) -> None:
        with self._lock:
            if kilobytes_per_second is None:
                self._rate_limit = None
            else:
                kb = max(1, int(kilobytes_per_second))
                self._rate_limit = kb * 1024

    def retry_job(self, job_id: str) -> Optional[DownloadJob]:
        job = self.get_job(job_id)
        if not job:
            return None
        new_job = DownloadJob(
            job_id=job.job_id,
            url=job.url,
            format_type=job.format_type,
            format_id=job.format_id,
            playlist_urls=list(job.playlist_urls or []),
            requested_by=job.requested_by,
        )
        with self._lock:
            self._jobs[job.job_id] = new_job
            self._futures[job.job_id] = self._executor.submit(self._run_job, job.job_id)
        return new_job

    def clear_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
            future = self._futures.pop(job_id, None)
        if future and not future.done():
            future.cancel()
        if job and job.file_path:
            try:
                job.file_path.unlink(missing_ok=True)
            except OSError:
                LOG.warning("Could not remove file for job %s", job_id, exc_info=True)
        job_dir = self._job_dir(job_id)
        if job_dir.exists():
            try:
                for child in job_dir.iterdir():
                    if child.is_file():
                        child.unlink(missing_ok=True)
                job_dir.rmdir()
            except OSError:
                LOG.warning("Could not remove directory for job %s", job_id, exc_info=True)
        return job is not None

    def cleanup_expired(self) -> None:
        """Remove jobs older than the retention window."""
        cutoff = datetime.utcnow() - self._retention
        for job in self.list_jobs():
            if job.completed_at and job.completed_at < cutoff:
                self.clear_job(job.job_id)

    # Internal helpers -------------------------------------------------

    def _job_dir(self, job_id: str) -> Path:
        path = self._root / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _run_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        job_dir = self._job_dir(job_id)
        try:
            with job._lock:
                job.status = JobStatus.PREPARING
                job.error = None
                job.progress = 0.0
                job.updated_at = datetime.utcnow()
            options = self._build_options(job, job_dir)
            LOG.debug("Starting yt-dlp for job %s", job_id)
            with yt_dlp.YoutubeDL(options) as downloader:
                try:
                    info = downloader.extract_info(job.url, download=False)
                except Exception:  # noqa: BLE001
                    info = None
                    LOG.debug("Metadata extraction failed for job %s", job_id, exc_info=True)
                if info:
                    with job._lock:
                        job.metadata.update(
                            {
                                "title": info.get("title"),
                                "uploader": info.get("uploader"),
                                "duration": info.get("duration"),
                                "view_count": info.get("view_count"),
                                "thumbnail": info.get("thumbnail"),
                                "ext": info.get("ext"),
                                "webpage_url": info.get("webpage_url"),
                            }
                        )
                if job.playlist_urls:
                    for entry_url in job.playlist_urls:
                        if job.cancel_requested:
                            raise DownloadCancelled
                        downloader.download([entry_url])
                else:
                    downloader.download([job.url])
            with job._lock:
                if job.status != JobStatus.CANCELLED:
                    job.status = JobStatus.COMPLETED
                    job.progress = 100.0
                    job.completed_at = job.completed_at or datetime.utcnow()
                    job.updated_at = datetime.utcnow()
        except DownloadCancelled:
            LOG.info("Job %s cancelled", job_id)
        except Exception as exc:  # noqa: BLE001
            with job._lock:
                job.status = JobStatus.FAILED
                job.error = _friendly_error_message(exc)
                job.metadata.setdefault("debug", {})
                if isinstance(job.metadata["debug"], dict):
                    job.metadata["debug"].update({"raw_error": str(exc)})
                job.updated_at = datetime.utcnow()
            LOG.exception("Job %s failed", job_id)

    def _build_options(self, job: DownloadJob, job_dir: Path) -> Dict[str, object]:
        def hook(data: Dict[str, object]) -> None:
            if job.cancel_requested:
                raise DownloadCancelled
            job.update_from_hook(data)

        output_template = str(job_dir / "%(title).120s [%(id)s].%(ext)s")
        options: Dict[str, object] = {
            "outtmpl": output_template,
            "progress_hooks": [hook],
            "paths": {"home": str(job_dir)},
            "retries": 5,
            "fragment_retries": 5,
            "skip_unavailable_fragments": True,
            "ignoreerrors": False,
            "quiet": True,
            "no_warnings": True,
            "source_address": "0.0.0.0",
            "geo_bypass": True,
            "geo_bypass_country": "US",
            "max_sleep_interval": 5,
            "sleep_interval": 1,
        }
        if job.format_id:
            options["format"] = job.format_id
        elif job.format_type == "audio":
            options["format"] = "bestaudio/best"
            options["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ]
        else:
            options["format"] = "bestvideo+bestaudio/best"
            options["merge_output_format"] = "mp4"
        rate_limit = self._rate_limit
        if rate_limit:
            options["ratelimit"] = rate_limit
        if job.cookie_file and job.cookie_file.exists():
            options["cookiefile"] = str(job.cookie_file)
        return options