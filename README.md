# Music Manager

A desktop application for managing a local music library. Built with Python and PyQt6.

---

## Features

### Library
Browse your music directory as a tree. Files are scanned for metadata tags (title, artist, album, duration). You can batch-edit tags, rename files, and reorganise the directory structure from within the app.

### Download
Paste a URL from YouTube, SoundCloud, Bandcamp, or any yt-dlp-supported source. The file downloads to a staging directory where you can review and edit the metadata before committing it to the library.

### Sync
Push or pull your library to a destination. Two modes are supported:

- **Folder** — any locally accessible path (external drive, mounted share, etc.)
- **Server** — SMB/NAS over the network (requires pysmb)

Optional deletion of files that no longer exist at the source.

### Duplicates
Detects duplicate tracks using acoustic fingerprinting via Chromaprint (`fpcalc`). Fingerprints are cached against file modification times so re-scans only process new or changed files. Matches are grouped by similarity above a configurable threshold (default 0.9). Auto-select picks which copy to keep based on format quality and configurable priority directories.

Format preference order: FLAC > WAV > M4A / MP3 > OGG / Opus.

### Re-encode
Batch-converts audio files to a target format using FFmpeg. Supports MP3, FLAC, M4A (AAC), OGG (Vorbis), Opus, and WAV with per-format quality presets. Shows a quality warning when a conversion would result in bitrate loss. Optionally deletes originals after conversion.

### Playlists
Creates and edits M3U playlists. Tracks can be added from the library browser, reordered, and removed. Unresolved paths (tracks that have moved) can be auto-resolved by matching filenames against the current library.

### Settings
Configures the music directory, skip directories, library folder structure, sync credentials, fingerprint cache location and threshold, download staging directory, and re-encode defaults.

---

## Requirements

### Python packages

| Package | Required for |
|---------|-------------|
| PyQt6 | All (UI) |
| mutagen | All (tag reading/writing) |
| pysmb | Sync — Server (SMB) mode only |

Install with pip:

```
pip install PyQt6 mutagen pysmb
```

### External tools

| Tool | Required for |
|------|-------------|
| yt-dlp | Download tab |
| fpcalc (Chromaprint) | Duplicates tab |
| ffmpeg | Re-encode tab |

These must be on your `PATH`.

**Fedora/RHEL:**
```
sudo dnf install chromaprint ffmpeg
pip install yt-dlp
```

**Debian/Ubuntu:**
```
sudo apt install libchromaprint-tools ffmpeg
pip install yt-dlp
```

**macOS (Homebrew):**
```
brew install chromaprint ffmpeg yt-dlp
```

**Windows:**

- ffmpeg: download from [ffmpeg.org](https://ffmpeg.org/download.html) and add the `bin` folder to your `PATH`, or install via winget:
  ```
  winget install Gyan.FFmpeg
  ```
- fpcalc: download the Chromaprint binary from [acoustid.org/chromaprint](https://acoustid.org/chromaprint) and place `fpcalc.exe` somewhere on your `PATH`.
- yt-dlp:
  ```
  pip install yt-dlp
  ```

---

## Running

```
python main.py
```

Run from the project root. No install step is required.

On first launch, a setup wizard walks through the minimum configuration: music directory, folder structure, and optional sync destination.

---

## Configuration

Settings are stored at:

```
~/.config/musicmanager/config.json
```

Cache files:

```
~/.cache/musicmanager/fingerprints.json   # acoustic fingerprint cache
~/.cache/musicmanager/staging/            # download staging directory (default)
~/.cache/musicmanager/library_cache.json  # library scan cache
```

The SMB password is stored obfuscated (XOR + Base64) in the config file. It is not encrypted.

---

## Supported Audio Formats

`.mp3`, `.flac`, `.m4a`, `.ogg`, `.opus`, `.wav`

Tag formats: ID3v2 (MP3), Vorbis comments (FLAC, OGG, Opus), MP4 atoms (M4A).
