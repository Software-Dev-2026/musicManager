from .base_worker import BaseWorker
from ..core.downloader import download


class DownloadWorker(BaseWorker):
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

    def run(self):
        def progress_cb(pct: float):
            self._progress(int(pct), 100, f"{pct:.1f}%")

        def log_cb(line: str):
            self._log(line)

        try:
            rc = download(
                self.url, self.output_dir, self.fmt,
                self.embed_thumbnail, self.add_metadata,
                progress_cb, log_cb,
                self.output_template,
            )
            self.finished.emit({"returncode": rc, "url": self.url})
        except Exception as e:
            self.error.emit(str(e))
