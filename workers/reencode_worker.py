from pathlib import Path
from .base_worker import BaseWorker
from ..core.reencode import reencode_file


class ReencodeWorker(BaseWorker):
    def __init__(
        self,
        files: list,
        target_fmt: str,
        quality_args: list,
        output_dir: Path | None,
        delete_originals: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.files = files
        self.target_fmt = target_fmt
        self.quality_args = quality_args
        self.output_dir = output_dir
        self.delete_originals = delete_originals

    def run(self):
        results = {"done": 0, "errors": [], "skipped": 0}
        total = len(self.files)
        for i, src in enumerate(self.files):
            if self._cancelled:
                break
            self._progress(i, total, str(src.name))
            ok, detail = reencode_file(
                src, self.output_dir, self.target_fmt,
                self.quality_args, self.delete_originals,
            )
            if ok:
                results["done"] += 1
                self._log(f"[OK] {src.name} → {detail}")
            else:
                results["errors"].append(f"{src.name}: {detail}")
                self._log(f"[ERR] {src.name}: {detail}")
        self.finished.emit(results)
