from pathlib import Path
from .base_worker import BaseWorker
from ..core import deleted_log
from ..core.reencode import reencode_file


class ReencodeWorker(BaseWorker):
    description = "re-encode"

    def __init__(
        self,
        files: list,
        target_fmt: str,
        quality_args: list,
        output_dir: Path | None,
        delete_originals: bool,
        source_root: Path | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.files = files
        self.target_fmt = target_fmt
        self.quality_args = quality_args
        self.output_dir = output_dir
        self.delete_originals = delete_originals
        self.source_root = source_root

    def run(self):
        results = {"done": 0, "errors": [], "skipped": 0, "cancelled": False}
        total = len(self.files)
        for i, src in enumerate(self.files):
            if self._cancelled:
                results["cancelled"] = True
                break
            self.progress.emit(i, total, str(src.name))
            ok, detail = reencode_file(
                src, self.output_dir, self.target_fmt,
                self.quality_args, self.delete_originals,
                self.source_root, cancel_fn=lambda: self._cancelled,
            )
            if ok:
                if self.delete_originals and not src.exists():
                    try:
                        # The song now lives at a new path/extension — other players need to know
                        deleted_log.record_moves({src: Path(detail)}, "Re-encoded")
                    except OSError:
                        pass
                results["done"] += 1
                self.log.emit(f"[OK] {src.name} → {detail}")
            else:
                results["errors"].append(f"{src.name}: {detail}")
                self.log.emit(f"[ERR] {src.name}: {detail}")
        self.finished.emit(results)
