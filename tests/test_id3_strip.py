"""C1: cleaning tags must never cut audio out of an MP3."""
from conftest import audio_md5, make_audio, mod, needs_ffmpeg

metadata = mod("core.metadata")

pytestmark = needs_ffmpeg


def _with_stray_tag_and_fakes(tmp_path):
    from mutagen.id3 import ID3, TIT2, TPE1

    f = make_audio(tmp_path / "t.mp3", secs=5)
    t = ID3()
    t.add(TIT2(encoding=3, text="Title"))
    t.add(TPE1(encoding=3, text="Artist"))
    t.save(f, v2_version=3)
    data = f.read_bytes()
    prim = metadata._id3_block_size(data, 0, strict=False)
    stray_file = tmp_path / "stray.mp3"
    stray_file.write_bytes(b"")
    stray = ID3()
    stray.add(TIT2(encoding=3, text="Stray"))
    stray.save(stray_file, v2_version=4, padding=lambda info: 0)
    stray_bytes = stray_file.read_bytes()
    mid = len(data) // 2
    fake1 = b"ID3\x03\x00\x00\x91\x22\x33\x44"  # non-syncsafe size: random audio bytes
    fake2 = b"ID3\x04\x00\x00\x00\x00\x10\x00"  # syncsafe size but no real frame inside
    data = data[:prim] + stray_bytes + data[prim:mid] + fake1 + data[mid:mid + 5000] + fake2 + data[mid + 5000:]
    f.write_bytes(data)
    return f, stray_bytes, fake1, fake2


def test_clean_removes_only_the_stray_tag_and_keeps_audio_identical(tmp_path):
    f, stray, fake1, fake2 = _with_stray_tag_and_fakes(tmp_path)
    before_audio, before_size = audio_md5(f), f.stat().st_size
    assert metadata.clean_file_tags(str(f))
    assert audio_md5(f) == before_audio
    assert before_size - f.stat().st_size == len(stray)
    body = f.read_bytes()
    assert fake1 in body and fake2 in body, "bytes that merely look like ID3 headers must be kept"


def test_bytes_between_rejected_matches_are_kept(tmp_path):
    """The original loop dropped everything between rejected 'ID3' hits."""
    f, _, _, _ = _with_stray_tag_and_fakes(tmp_path)
    data = f.read_bytes()
    removed = []
    real_write = metadata._atomic_write
    metadata._atomic_write = lambda fp, chunks: removed.append(len(data) - sum(map(len, chunks)))
    try:
        metadata._strip_embedded_id3(str(f))
    finally:
        metadata._atomic_write = real_write
    assert removed and removed[0] < 100  # only the small stray tag


def test_no_matches_means_file_untouched(tmp_path):
    f = make_audio(tmp_path / "plain.mp3", secs=2)
    before = f.read_bytes()
    assert metadata._strip_embedded_id3(str(f)) is False
    assert f.read_bytes() == before


def test_write_is_atomic_no_temp_left(tmp_path):
    f, _, _, _ = _with_stray_tag_and_fakes(tmp_path)
    metadata.clean_file_tags(str(f))
    assert not list(tmp_path.glob("*.tagtmp"))
