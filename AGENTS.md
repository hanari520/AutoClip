# AutoClip Agent Context

## Project Snapshot

AutoClip is a Windows-first Python project for Bilibili live-recording clip production. It turns recording folders into upload-ready clips with preprocessing, ASR subtitles, highlight analysis, subtitle burn-in, cover generation, upload manifests, batch upload status, and BililiveRecorder webhook notifications.

Primary stack:

- Python 3.10+
- FFmpeg / ffprobe for remuxing, cutting, burn-in, thumbnails, and media probing
- FunASR, Whisper, and Belle for speech recognition
- PyQt6 for desktop GUI
- `http.server` + REST + SSE for the web console
- Pillow / fonttools for cover generation
- `bilibili-api` for Bilibili login and upload

The workspace is the repository root (wherever AutoClip is cloned) and the shell is usually Windows PowerShell or Git Bash.

## High-Value Entry Points

- `core/auto_pipeline.py`: one-click pipeline scheduler.
- `core/convert_flv_to_mp4.py`: step 1 FLV to MP4 stream-copy preprocessing.
- `core/burn_danmu_video.py`: optional same-name MP4 + XML danmu burn-in.
- `core/generate_subtitles.py`: step 2 subtitle generation and ASR routing.
- `core/generate_clips_combined.py`: step 3/4 highlight analysis and clip generation.
- `core/burn_subtitles.py`: step 5 ASS creation and subtitle burn-in.
- `core/generate_covers.py` and `core/generate_covers_new.py`: step 6 cover generation.
- `utils/bilibili_login.py`: Bilibili login and credential checks.
- `utils/bilibili_uploader.py`: Bilibili upload and batch status handling.
- `bilirecorder_webhook_server.py`: BililiveRecorder Webhook v2 to ServerChan bridge.
- `gui_app_mobile.py`: main PyQt6 GUI business logic.
- `gui_app_light.py`: light-theme desktop wrapper that reuses `gui_app_mobile.py`.
- `web_console.py`: base web backend and embedded frontend.
- `web_console_light.py`: light-theme web wrapper that reuses base backend routes.
- `docs/AutoClip_技术复刻文档.md`: architecture and feature documentation.
- `archive/`: archived unused scripts (`gui_app.py`, `config_builder.py`, `config_web.py`, `pc_console_gui.py`) with `README.md` for restore.

## Common Commands

Install baseline dependencies:

```powershell
python -m pip install -r configs\requirements.txt
```

Run Light GUI:

```powershell
launchers\start_gui_light.bat
```

Run Light Web console:

```powershell
launchers\start_web_light.bat
```

Run the full pipeline:

```powershell
python core\auto_pipeline.py -i "录播目录"
```

Inspect planned pipeline steps:

```powershell
python core\auto_pipeline.py -i "录播目录" --dry-run
```

Run selected steps:

```powershell
python core\auto_pipeline.py -i "录播目录" --start 5 --stop 6
```

Validate Python syntax for touched files:

```powershell
python -m py_compile path\to\file.py
```

## Machine-Specific Paths And Environment Variables

No machine-specific absolute paths are hardcoded in the code. External tools and local roots resolve in this order: `shutil.which()` / filesystem check first, then the environment variables below, else graceful degradation.

| Environment variable | Purpose | Used by |
|---|---|---|
| `AUTOCLIP_FFMPEG_BIN` | FFmpeg `bin` directory (prepended to PATH when it exists) | step 1-6 scripts, GUI, web console, qwen3 transcriber/worker |
| `AUTOCLIP_BILILIVE_TOOLS_BIN` | biliLive-tools bin directory containing `DanmakuFactory.exe` | danmu burn-in (step 1-3) |
| `AUTOCLIP_MANAGED_PYTHON` | Separate Python (3.13, ctranslate2-compatible) for the Belle ASR subprocess; falls back to `sys.executable` | Belle transcriber |
| `AUTOCLIP_RECORDING_ROOTS` | Semicolon-separated recording root dirs (fallback when `gui_config.json` has no `recording_roots`) | web console |
| `AUTOCLIP_ALLOWED_MEDIA_ROOTS` | Semicolon-separated extra allowed roots for ASR input/output path validation | qwen3 ASR worker |

Related conventions:

- Recording roots for the web console live in gitignored `configs/gui_config.json` under `recording_roots` (list).
- Fonts referenced by streamer configs may be bare filenames; they resolve against `assets/font/` in the repo.
- Portable FunASR runtime, when assembled, lives at `funasr/runtime/python/python.exe` (repo-relative, gitignored).
- Never hardcode a new absolute local path into tracked code; add or reuse an environment variable.

## Safety Boundaries

Never commit, upload, print, or summarize secrets from local config files. Treat these as private by default:

- `configs/gui_config.json`
- `configs/serverchan_config.json`
- `configs/bilirecorder_cookie_source.txt`
- any `configs/*cookie*.json` or `configs/*cookie*.txt`
- `.env` and `.env.*`
- `sessions/`, runtime logs, token caches, and JSONL status files

Do not add generated media or runtime artifacts to Git:

- `models/`
- `funasr/runtime/`
- `funasr/workspace/`
- `sessions/`
- `thumbnails/`
- `backups/`
- `*.mp4`, `*.flv`, `*.mkv`, `*.mov`, `*.avi`
- `*.wav`, `*.mp3`, `*.aac`
- `*.srt`, `*.ass`
- `*.jsonl`, `*.log`, `*.pid`, `*.tmp`

When creating temporary packages for external upload, keep them outside the repo or remove them before finishing. If a package must be created inside the repo briefly, verify `git status --short` afterward.

## Editing Rules

- Read the target source file and one nearby pattern before editing.
- Prefer existing project patterns over new abstractions.
- Keep changes focused; avoid unrelated refactors.
- Use narrow patches in files with Chinese comments or unusual encoding, because broad hunks can fail or corrupt context.
- Preserve current user changes in the working tree. Do not reset or checkout files unless explicitly asked.
- For GUI redesigns, prefer wrapper/subclass entrypoints over rewriting `gui_app_mobile.py`.
- For web UI changes, remember `web_console.py` embeds frontend HTML/JS directly; `web_console_light.py` can patch or wrap it while reusing backend routes.
- For optional ASR backends, avoid eager imports that can break unrelated engine paths.

## UI And Workflow Conventions

- `gui_app_mobile.py` owns most desktop business logic: task threads, dialogs, upload flow, logs, config reads/writes.
- `gui_app_light.py` should focus on layout and presentation while reusing original behavior.
- `gui_light_theme.py` is the shared light theme/QSS location.
- Status UIs should inspect real artifacts instead of showing generic counters:
  - Step 1: FLV to same-name MP4
  - Step 2: video to matching SRT
  - Step 3: `Data_source.txt`
  - Step 4: `Data_source.txt` entries to cut MP4 files
  - Step 5: cut MP4 to `_subbed.mp4`
  - Step 6: `_subbed.mp4` to cover images
  - Step 7/upload: `upload_manifest.json` success/failure state
- Logs should expose enough progress to prove work is running, but avoid dumping full FFmpeg or ASR noise unless a failure occurs.

## Media Pipeline Notes

- Step 1 converts `.flv` to same-name `.mp4` with FFmpeg stream copy and may delete source FLV after successful conversion.
- Danmu burn-in matches same-name `.mp4` and `.xml`; existing `-弹幕版.mp4` outputs should be skipped unless overwrite is requested.
- Danmu burn-in uses DanmakuFactory to create ASS, CPU subtitle rendering, and can use NVENC for final H.264 encoding.
- Subtitle burn-in creates ASS from SRT, then uses FFmpeg. Hardware encoders may be selected when available, but ASS/subtitle rendering itself is CPU-bound.
- Keep FFmpeg logs concise: progress heartbeats and final result are usually enough; print tail details on failure.

## Upload Module Notes

- Batch upload should respect platform rate limits. Current intended batching is 5 submissions per group with a 5-minute cooldown.
- Preserve upload memory in `upload_manifest.json`: uploaded/not uploaded status, attempt count, BVID/result, and `last_error`.
- GUI and Web console should make completed, pending, skipped, and failed items visually clear.
- Do not treat a failed upload as a successful skip. Failed items should remain retryable and show the error.
- Already successful items should not be selected by default for repeat batch upload.

## BililiveRecorder Webhook Notes

- Default local webhook endpoint is `http://127.0.0.1:18010/webhook/mikufansrecorder`.
- Webhook path should remain configurable for future endpoint changes.
- ServerChan SENDKEY belongs in local config or environment variables, never in committed docs.
- Cookie/login health checks should use project login helpers where possible and should not print raw cookies.

## Verification Guidance

Use the smallest verification that proves the change:

- Python syntax-only changes: `python -m py_compile touched_file.py`.
- Pipeline behavior: run the narrow script with `--dry-run` when supported, or against a small sample folder if available.
- GUI layout changes: launch the relevant BAT or Python entrypoint when practical.
- Web console changes: start the relevant server and inspect the route/page if practical.
- Upload logic changes: validate manifest transitions and failure cases; avoid real submission unless explicitly requested.
- Git hygiene: run `git status --short` before finalizing if files were created, deleted, or generated.

If verification cannot be run because required media, credentials, or services are unavailable, state that clearly and explain what was checked instead.

