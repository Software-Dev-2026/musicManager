"""Re-encoding must never overwrite another file or delete an original it didn't safely replace (C5, M25)."""
import subprocess
import threading
import time

import pytest

from conftest import make_audio, mod, needs_ffmpeg

r = mod("core.reencode")

pytestmark = needs_ffmpeg


def test_same_stem_collision_skips_and_keeps_original(tmp_path):
    make_audio(tmp_path / "song.flac"); make_audio(tmp_path / "song.wav", secs=2)
    assert r.reencode_file(tmp_path / "song.flac", None, "mp3", ["-q:a", "0"], delete_original=True)[0]
    ok, detail = r.reencode_file(tmp_path / "song.wav", None, "mp3", ["-q:a", "0"], delete_original=True)
    assert not ok and "already exists" in detail
    assert (tmp_path / "song.wav").exists()


def test_existing_target_is_never_overwritten(tmp_path):
    make_audio(tmp_path / "song.flac")
    existing = tmp_path / "song.mp3"
    existing.write_bytes(b"someone else's file")
    ok, _ = r.reencode_file(tmp_path / "song.flac", None, "mp3", ["-q:a", "0"], delete_original=True)
    assert not ok
    assert existing.read_bytes() == b"someone else's file"
    assert (tmp_path / "song.flac").exists()


def test_output_dir_keeps_folder_structure(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "A/01 Intro.flac"); make_audio(lib / "B/01 Intro.flac", secs=2)
    for a in ("A", "B"):
        assert r.reencode_file(lib / a / "01 Intro.flac", out, "mp3", ["-q:a", "0"], True, source_root=lib)[0]
    assert (out / "A/01 Intro.mp3").exists() and (out / "B/01 Intro.mp3").exists()


def test_truncated_output_is_rejected_and_original_kept(tmp_path, monkeypatch):
    make_audio(tmp_path / "x.flac", secs=3)
    real = r.build_ffmpeg_cmd
    monkeypatch.setattr(r, "build_ffmpeg_cmd", lambda s, d, f, q: real(s, d, f, q + ["-t", "0.5"]))
    ok, detail = r.reencode_file(tmp_path / "x.flac", None, "mp3", ["-q:a", "0"], delete_original=True)
    assert not ok and "verification" in detail
    assert (tmp_path / "x.flac").exists() and not (tmp_path / "x.mp3").exists()


def test_untagged_wav_output_passes_verification(tmp_path):
    """mutagen file objects are falsy when untagged; that must not fail verification."""
    make_audio(tmp_path / "a.mp3", art=True)
    assert r.reencode_file(tmp_path / "a.mp3", None, "wav", ["-acodec", "pcm_s16le"])[0]


def test_cancel_kills_ffmpeg_and_leaves_no_partial_file(tmp_path):
    make_audio(tmp_path / "long.wav", secs=3000)
    flag = {"c": False}
    threading.Timer(0.15, lambda: flag.__setitem__("c", True)).start()
    t = time.time()
    res = r.reencode_file(tmp_path / "long.wav", None, "flac", [], True, cancel_fn=lambda: flag["c"])
    assert res == (False, "Cancelled") and time.time() - t < 5
    assert (tmp_path / "long.wav").exists()
    assert [p.name for p in tmp_path.iterdir()] == ["long.wav"]


@pytest.mark.parametrize("target", ["mp3", "flac", "m4a", "ogg", "opus", "wav"])
def test_every_format_works_with_cover_art_and_mono(tmp_path, target):
    src = make_audio(tmp_path / ("art.flac" if target == "mp3" else "art.mp3"), art=True)
    ok, detail = r.reencode_file(src, None, target, r.QUALITY_PRESETS[target][0][1])
    assert ok, detail
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", detail],
                             capture_output=True, text=True).stdout.split()
    assert "audio" in streams
    assert not list(tmp_path.glob(".*reencodetmp"))
