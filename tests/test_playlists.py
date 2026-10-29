"""Playlists must survive loading, saving, resolving and library moves intact (M16–M18, M27, S2, S3)."""
import time

from conftest import make_audio, mod, needs_ffmpeg

pt = mod("ui.playlist_tab")
playlists = mod("core.playlists")
resolve = mod("core.resolve")
metadata = mod("core.metadata")
deleted_log = mod("core.deleted_log")


def touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


def test_parse_decodes_only_when_needed_and_saves_without_losing_entries(tmp_path):
    lib, other = tmp_path / "Music", tmp_path / "Elsewhere"
    for p in [lib / "A/song.mp3", lib / "A/100%AB.mp3", lib / "A/café.mp3", lib / "A/a b.mp3", other / "x.mp3"]:
        touch(p)
    pl = lib / "mix.m3u8"
    pl.write_text("#EXTM3U\n#EXTINF:1,100% Pure Love\nA/song.mp3\n#EXTINF:1,literal\nA/100%AB.mp3\n"
                  "#EXTINF:1,encoded\nA/caf%C3%A9.mp3\n#EXTINF:1,uri\n" + (lib / "A/a b.mp3").as_uri() + "\n"
                  f"#EXTINF:1,outside\n{other / 'x.mp3'}\n", encoding="utf-8")
    e = pt._parse_m3u(pl)
    assert all(x["exists"] for x in e)
    assert e[1]["path"].endswith("100%AB.mp3") and e[2]["path"].endswith("café.mp3")
    pt._write_m3u(pl, e)
    assert "../Elsewhere/x.mp3" in pl.read_text(), "entries outside the folder keep a usable path"
    assert len(pt._parse_m3u(pl)) == 5 and all(x["exists"] for x in pt._parse_m3u(pl))
    assert not list(lib.glob(".mix.m3u8.*")), "atomic write leaves no temp file"


def test_large_playlist_loads_quickly(tmp_path):
    lines = ["#EXTM3U"]
    for i in range(10000):
        touch(tmp_path / f"A{i % 100}/{i}.mp3")
        lines += [f"#EXTINF:1,t{i}", f"A{i % 100}/{i}.mp3"]
    pl = tmp_path / "big.m3u8"
    pl.write_text("\n".join(lines))
    t = time.perf_counter()
    entries = pt._parse_m3u(pl)
    assert len(entries) == 10000 and all(e["exists"] for e in entries)
    assert time.perf_counter() - t < 3, "10k entries should parse in well under a few seconds"


def test_update_playlists_keeps_style_endings_and_foreign_bytes(tmp_path):
    lib = tmp_path
    old, new = touch(lib / "Inbox/one.mp3"), lib / "Queen/Jazz/one.mp3"
    (lib / "Lists").mkdir()
    (lib / "Lists/rel.m3u8").write_text("#EXTM3U\n../Inbox/one.mp3\n")
    (lib / "abs.m3u").write_bytes(f"#EXTM3U\r\n{old}\r\n".encode())
    (lib / "win.m3u").write_text("Inbox\\one.mp3\n")
    (lib / "latin.m3u").write_bytes("#EXTINF:1,Björk\n".encode("latin-1") + b"Inbox/one.mp3\n")
    playlists.update_playlists(lib, {old: new})
    assert (lib / "Lists/rel.m3u8").read_text().splitlines()[1] == "../Queen/Jazz/one.mp3"
    assert (lib / "abs.m3u").read_bytes() == f"#EXTM3U\r\n{new}\r\n".encode()
    assert (lib / "win.m3u").read_text() == "Queen\\Jazz\\one.mp3\n"
    assert (lib / "latin.m3u").read_bytes().startswith("#EXTINF:1,Björk\n".encode("latin-1"))


@needs_ffmpeg
def test_organize_updates_playlists(tmp_path):
    make_audio(tmp_path / "Inbox/one.mp3", artist="Queen", album="Jazz", title="one")
    (tmp_path / "p.m3u8").write_text("Inbox/one.mp3\n")
    metadata.batch_organize(tmp_path, set(), {".mp3"})
    assert (tmp_path / "p.m3u8").read_text() == "Queen/Jazz/one.mp3\n"


@needs_ffmpeg
def test_resolve_does_not_substitute_other_songs(tmp_path):
    lib = tmp_path
    make_audio(lib / "Queen/Bohemian Rhapsody.mp3", title="Bohemian Rhapsody", artist="Queen")
    make_audio(lib / "B/Intro.mp3", title="Intro", artist="Band B")
    make_audio(lib / "Moved/renamed_file_01.mp3", title="Somebody to Love", artist="Queen")
    missing = [
        (0, {"path": "/old/Queen - Killer Queen.mp3", "title": "Queen - Killer Queen"}),
        (1, {"path": "/old/Intro.mp3", "title": "Band A - Intro"}),
        (2, {"path": "/old/Queen - Somebody to Love.mp3", "title": "Queen - Somebody to Love"}),
    ]
    r = {i: (p, why) for i, p, why in resolve.resolve_missing(missing, lib, set(), {".mp3"})}
    assert 0 not in r, "a different song by the same artist is not a match"
    assert 1 not in r, "the same title by a different artist is not a match"
    assert r[2][0].endswith("renamed_file_01.mp3")


def test_resolve_prefers_recorded_moves_then_extension_then_filename(tmp_path):
    lib = tmp_path / "lib"
    a = touch(lib / "A/re-encoded.opus")             # was A/re-encoded.mp3
    b = touch(lib / "Elsewhere/moved.mp3")           # was A/moved.mp3
    c = touch(lib / "Final/chained.mp3")             # logged A → B → Final
    deleted_log.record_moves({lib / "A/chained.mp3": lib / "B/chained.mp3"}, "test")
    deleted_log.record_moves({lib / "B/chained.mp3": c}, "test")
    missing = [(0, {"path": str(lib / "A/re-encoded.mp3"), "title": "x"}),
               (1, {"path": str(lib / "A/moved.mp3"), "title": "y"}),
               (2, {"path": str(lib / "A/chained.mp3"), "title": "z"})]
    r = {i: (p, why) for i, p, why in resolve.resolve_missing(missing, lib, set(), {".mp3", ".opus"})}
    assert r[0] == (str(a), "new extension")
    assert r[1] == (str(b), "same filename")
    assert r[2] == (str(c), "recorded move")
