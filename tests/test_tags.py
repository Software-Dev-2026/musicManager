"""Tag edits must only change what the user asked for (C6, M20, M22, L7)."""
import pytest

from conftest import make_audio, mod, needs_ffmpeg

metadata = mod("core.metadata")


def tag(p, k):
    import music_tag
    return str(music_tag.load_file(str(p))[k])


@needs_ffmpeg
def test_normalize_keeps_correct_tags_and_fixes_wrong_ones(tmp_path):
    from mutagen.id3 import ID3, TXXX

    lib = tmp_path
    make_audio(lib / "AC_DC/Back in Black/01.mp3", title="Hells Bells", artist="AC/DC", album="Back in Black")
    make_audio(lib / "Various Artists/Hits/02.mp3", title="X", artist="Queen", albumartist="Various Artists",
               album="Hits", compilation=True)
    make_audio(lib / "R.E.M/Out of Time/03.flac", title="Y", artist="R.E.M.", album="Out of Time")
    make_audio(lib / "Artist/Album/CD1/04.mp3", title="Z", artist="Artist", album="Album")
    make_audio(lib / "Artist/loose.mp3", title="L", artist="Artist", album="Single")
    make_audio(lib / "Artist/Album/Foo Bar.opus", artist="Artist", album="Album")
    w = make_audio(lib / "Right/Alb/w.mp3", title="W", artist="Wrong", album="Alb")
    t = ID3(w)
    t.add(TXXX(desc="replaygain_track_gain", text="-6.5 dB"))
    t.add(TXXX(desc="purl", text="http://yt"))
    t.save()

    metadata.batch_normalize(lib, set(), {".mp3", ".flac", ".opus"})

    assert tag(lib / "AC_DC/Back in Black/01.mp3", "artist") == "AC/DC"
    assert tag(lib / "R.E.M/Out of Time/03.flac", "artist") == "R.E.M."
    assert tag(lib / "Various Artists/Hits/02.mp3", "artist") == "Queen"
    assert tag(lib / "Various Artists/Hits/02.mp3", "compilation") in ("True", "1")
    assert tag(lib / "Artist/Album/CD1/04.mp3", "album") == "Album"
    assert tag(lib / "Artist/loose.mp3", "album") == "Single"
    assert tag(lib / "Artist/Album/Foo Bar.opus", "title") == "Foo Bar"
    assert tag(w, "artist") == "Right" and tag(w, "albumartist") == "Right"
    keys = set(ID3(w).keys())
    assert "TXXX:replaygain_track_gain" in keys and "TXXX:purl" not in keys


@needs_ffmpeg
def test_write_tags_never_invents_zeros(tmp_path):
    from mutagen.id3 import ID3, TDRC

    p = make_audio(tmp_path / "t.mp3")
    form = {"title": "T", "artist": "A", "albumartist": "", "album": "", "genre": "", "comment": "",
            "tracknumber": 0, "discnumber": 0, "year": 0, "compilation": False}
    assert metadata.write_tags(str(p), form)
    r = metadata.read_tags(str(p))
    assert r["tracknumber"] == r["discnumber"] == r["year"] == ""
    metadata.write_tags(str(p), dict(form, tracknumber=5, year=1999))
    assert metadata.read_tags(str(p))["tracknumber"] == "5"
    metadata.write_tags(str(p), dict(form, tracknumber=0, year=1999))
    assert metadata.read_tags(str(p))["tracknumber"] == ""
    t = ID3(p)
    t.add(TDRC(encoding=3, text="2001-05-03"))
    t.save()
    shown_year = int(metadata.read_tags(str(p))["year"])
    metadata.write_tags(str(p), dict(form, title="New", year=shown_year))
    assert str(ID3(p)["TDRC"].text[0]) == "2001-05-03", "an unrelated edit must keep the full date"


def test_write_tags_reports_failure(tmp_path):
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio")
    assert metadata.write_tags(str(bad), {"title": "x"}) is False


@pytest.mark.parametrize("raw,want", [
    ("Foo. ", "Foo"), ("AC/DC", "AC_DC"), ("R.E.M.", "R.E.M"), ("...", "Unknown"),
    ("CON", "CON_"), ("nul.album", "nul.album_"), ("Coney", "Coney"), ("  ", "Unknown"),
])
def test_sanitize_name(raw, want):
    assert metadata.sanitize_name(raw) == want


@needs_ffmpeg
def test_organize_never_overwrites(tmp_path):
    make_audio(tmp_path / "Inbox/a/song.mp3", artist="Q", album="J", title="one")
    make_audio(tmp_path / "Inbox/b/song.mp3", artist="Q", album="J", title="two")
    moved, skipped, errors = metadata.batch_organize(tmp_path, set(), {".mp3"})
    assert moved == 1 and skipped == 1 and any("already exists" in e for e in errors)
    assert len(list(tmp_path.rglob("song.mp3"))) == 2
