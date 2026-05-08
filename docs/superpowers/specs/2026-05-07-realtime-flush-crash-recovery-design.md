# Real-Time Disk Flush & Crash Recovery Design

## Overview

This design adds real-time disk flushing during recording and automatic crash recovery for the screen recorder application. FFmpeg will write directly to the final output file using fragmented MP4 format, ensuring data is persisted to disk immediately. If the application crashes, the recorded file remains playable and can be automatically repaired on next startup or on-demand via API/CLI.

## Recording Pipeline

### Current Flow

```
FFmpeg capture  → tmp/tmp.mkv
Audio capture   → tmp/tmp_0.wav
On stop: merge  → ScreenCaptures/final.mp4
Cleanup tmp/
```

### New Flow

```
FFmpeg capture  → ScreenCaptures/final.mp4 (fragmented MP4, real-time flush)
Audio capture   → tmp/tmp_0.wav
Recording marker → ScreenCaptures/.recording

On stop (no audio):
  remux final.mp4 → standard MP4 (-c:v copy, fast)
  delete .recording marker

On stop (with audio):
  merge final.mp4 + audio → standard MP4
  delete .recording marker

On crash:
  final.mp4 remains playable (fragmented MP4 property)
  .recording marker persists → auto-repair on next startup
```

## FFmpeg Parameter Changes

### Capture Command

Add to output options before the output filename:

```
-movflags +frag_keyframe+empty_moov -flush_packets 1
```

- `+frag_keyframe`: start a new fragment at each keyframe
- `+empty_moov`: write initialization metadata (moov atom) at file head
- `-flush_packets 1`: flush output buffer after each packet for immediate disk write

### Remux / Merge Command

Add to output options:

```
-movflags +faststart
```

- `+faststart`: move moov atom to file head for fast playback start

### Video Codec Selection in Merge

Simplified from current logic:

| Condition | Video codec |
|-----------|-------------|
| No audio, no webcam | `-c:v copy` (remux only) |
| Has audio, no webcam | `-c:v copy` (remux + add audio) |
| Has webcam overlay | Re-encode with current encoder |

This is a simplification: the current code re-encodes for HW encoders even without webcam, but with fragmented MP4 input the stream is already correctly encoded. Only webcam overlay requires re-encoding.

## Recording Marker File

On recording start, write `ScreenCaptures/.recording`:

```json
{
  "filename": "ScreenCapture_20260507_143000.mp4",
  "started_at": "2026-05-07T14:30:00",
  "encoder": "h264_qsv",
  "hwaccel": "qsv"
}
```

On normal stop, delete the marker. On startup, if marker exists, the previous session crashed.

## Crash Recovery

### Detection Mechanisms

1. **Marker file**: `.recording` exists → known crash, filename available
2. **FFprobe check**: `ffprobe -v error -show_format file.mp4` fails → file is broken
3. **Fragmented MP4 detection**: file plays but is fragmented format → needs remux to standard

### Repair Process

```
Detect broken/fragmented file
  → ffmpeg -i input.mp4 -c copy -movflags +faststart repaired_tmp.mp4
  → Success: replace original with repaired
  → Failure: rename original to .broken, log error
```

### Auto-Repair on Startup

1. Check for `.recording` marker in captures_dir
2. If exists, read filename and attempt repair
3. Scan all .mp4 files in captures_dir with ffprobe
4. Attempt repair on any file that fails probe or is fragmented
5. Log repair results
6. Delete `.recording` marker

### On-Demand Repair

#### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/repair` | POST | Scan and repair all broken files |
| `/api/files/{name}/repair` | POST | Repair a specific file |
| `/api/files/health` | GET | Check health status of all files |

#### CLI Commands

| Flag | Description |
|------|-------------|
| `--repair` | Scan and repair all broken files, then exit |
| `--repair-file FILE` | Repair a specific file, then exit |
| `--check-files` | Check file health status, then exit |

### Repair Result Structure

```json
{
  "repaired": ["ScreenCapture_20260507_143000.mp4"],
  "failed": [
    {"name": "ScreenCapture_20260507_150000.mp4", "error": "moov atom not found"}
  ],
  "healthy": ["ScreenCapture_20260507_120000.mp4"]
}
```

## API Changes

### New Endpoints

- `POST /api/repair` — scan and repair all broken files, returns repair result
- `POST /api/files/{name}/repair` — repair specific file
- `GET /api/files/health` — check all files health without repairing

### Modified Endpoints

- `POST /api/record/start` — response adds `output_path` field (real-time disk file path)
- `GET /api/status` — response adds `output_path` field
- `GET /api/files` — response adds `health` field per file: `healthy`, `fragmented`, or `broken`

## CLI Changes

### New Arguments

| Argument | Description |
|----------|-------------|
| `--repair` | Scan and repair all broken files, then exit |
| `--repair-file FILE` | Repair a specific file, then exit |
| `--check-files` | Check file health status without repairing, then exit |
| `--json` | Output results in JSON format (for scripting) |

### Exit Codes for Repair Commands

| Code | Meaning |
|------|---------|
| 0 | All files healthy or successfully repaired |
| 1 | Some files could not be repaired |
| 2 | All files failed repair or no files found |

## Async Implementation

### Python Version (threading)

- Repair tasks run in background threads, non-blocking for API
- `POST /api/repair` returns immediately with task info
- Repair progress tracked via engine state
- FFmpeg subprocess calls use existing `subprocess.Popen` pattern

### Rust Version (async/await)

- Repair tasks use `tokio::task::spawn_blocking` for FFmpeg calls
- API handlers are async functions
- Progress notification via existing `watch::Sender` channel

## Automation-Friendly Design

1. **Idempotent**: `/api/repair` called multiple times is safe; already-repaired files are skipped
2. **Structured responses**: all API endpoints return JSON with `ok` field and detailed results
3. **Exit codes**: CLI uses standard exit codes for scripting
4. **Silent by default**: CLI outputs minimal info; `--verbose` for details; `--log-file` for file logging
5. **JSON output**: `--json` flag for machine-parseable output

## Files to Modify

### Python

| File | Changes |
|------|---------|
| `python/recorder/cmd_builder.py` | Add fragmented MP4 params to capture cmd; add remux cmd builder; simplify merge codec logic |
| `python/recorder/engine.py` | Direct write to captures_dir; marker file management; auto-repair on init; repair methods |
| `python/recorder/repair.py` | New file: file health check and repair logic |
| `python/web/api.py` | New repair/health endpoints; modified start/status/files responses |
| `python/cli.py` | New --repair, --repair-file, --check-files, --json arguments |
| `python/tests/test_hwaccel.py` | Update existing tests for new cmd_builder params |
| `python/tests/test_repair.py` | New file: tests for repair functionality |

### Rust

| File | Changes |
|------|---------|
| `rust/src/main.rs` | Fragmented MP4 params; marker file; auto-repair; new API routes; new CLI args; repair logic |
| `rust/assets/app.js` | UI updates for file health status display |

## Error Handling

- Repair failure: original file preserved (renamed to `.broken`), error logged
- Marker file corruption: ignore marker, scan all files
- FFmpeg not found during repair: report error, skip file
- Concurrent repair requests: use lock to prevent duplicate repair of same file
