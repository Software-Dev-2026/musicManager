from .base_worker import BaseWorker
from ..core.downloader import download


class DownloadWorker(BaseWorker):
    description = "download"

    def __init__(
        self, url: str, output_dir: str, fmt: str,
        embed_thumbnail: bool, add_metadata: bool,
        output_template: str = "%(uploader)s/%(title)s.%(ext)s",
        parent=None,
    ):
        super().__init__(parent)
        self.url = url
        self.output_dir = output_dir
        self.fmt = fmt
        self.embed_thumbnail = embed_thumbnail
        self.add_metadata = add_metadata
        self.output_template = output_template
        self._process = None

    def cancel(self):
        super().cancel()
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def _on_start(self, process):
        self._process = process
        if self._cancelled:  # cancelled before yt-dlp started
            process.terminate()

    def run(self):
        def progress_cb(pct: float):
            self.progress.emit(int(pct), 100, f"{pct:.1f}%")

        def log_cb(line: str):
            self.log.emit(line)

        try:
            rc = download(
                self.url, self.output_dir, self.fmt,
                self.embed_thumbnail, self.add_metadata,
                progress_cb, log_cb,
                self.output_template,
                on_start=self._on_start,
            )
            self.finished.emit({"returncode": rc, "url": self.url, "cancelled": self._cancelled})
        except Exception as e:
            self.error.emit(str(e))
