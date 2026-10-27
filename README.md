# ComfyUI Mobile

A self-hosted mobile dashboard for [ComfyUI](https://github.com/comfyanonymous/ComfyUI) on Windows. Watch the queue, browse and download outputs, edit and run workflows, and schedule jobs from your phone.

It runs as a small Python server next to ComfyUI, uses only the Python standard library, and listens on `127.0.0.1:8765`. You reach it from your phone over a private network such as [Tailscale](https://tailscale.com/).

> [!WARNING]
> This app can queue GPU jobs and download files from your PC. **Never** port-forward it or expose it to the internet without strong access control. See [Security](#security).

## Features

- **Live queue**: running and waiting jobs, node and step progress, sampler ETA and live previews.
- **History**: recent jobs with elapsed time, image and video previews, and downloads for single files, ZIPs or the job JSON.
- **Output browser**: browse your ComfyUI output folders with fast cached thumbnails, video previews and downloads.
- **Media assets**: ComfyUI's Generated and Imported lists, served from disk so they still load while ComfyUI is busy.
- **Run & schedule**: open a workflow, edit its prompts, LoRAs, images and media inputs, then run it now or schedule it once or daily. UI workflows are converted to API format automatically.
- **Prompt tools** (optional): rewrite prompts or chat with a local [Ollama](https://ollama.com/) model.
- **Version badge and Restart button**: shows which version is running and loads updated code without touching the PC.

## Requirements

- Windows 10 or 11 with ComfyUI running locally (default `http://127.0.0.1:8188`)
- [Python](https://www.python.org/downloads/) 3.9 or newer, with the `py` launcher or `python` on PATH. No pip packages are needed.
- [FFmpeg](https://ffmpeg.org/download.html) (recommended): needed for thumbnails and video posters
- [Tailscale](https://tailscale.com/download) on the PC and the phone (recommended for phone access)
- [Ollama](https://ollama.com/) (optional): only for the prompt tools

## Installation

### 1. Get the code

```bat
git clone https://github.com/YOUR-NAME/YOUR-REPO.git ComfyUI-Mobile
cd ComfyUI-Mobile
```

Or download the ZIP from GitHub (**Code → Download ZIP**) and extract it.

### 2. Configure your folders

Copy the example config and edit the paths to match your ComfyUI install:

```bat
copy config.example.json config.json
notepad config.json
```

```json
{
  "workflow_dirs": ["C:/ComfyUI_windows_portable/ComfyUI/user/default/workflows"],
  "converted_workflow_dir": "C:/ComfyUI_windows_portable/ComfyUI/user/default/workflows/converted",
  "auto_convert_workflows": true,
  "asset_dirs": ["C:/ComfyUI_windows_portable/ComfyUI/input"],
  "output_dirs": ["C:/ComfyUI_windows_portable/ComfyUI/output"],
  "ffmpeg_path": "C:/ffmpeg/bin/ffmpeg.exe"
}
```

Use forward slashes (or doubled backslashes). See [Configuration](#configuration) for every option.

### 3. Start the monitor

Make sure ComfyUI is running, then double-click **`start_monitor.bat`**, or run it from a terminal:

```bat
start_monitor.bat
```

Open <http://127.0.0.1:8765> on the PC to check it works. The startup banner shows which folders and FFmpeg it found.

### 4. Open it on your phone (Tailscale)

1. Install Tailscale on the PC and the phone, and sign in to the same account on both.
2. Run **`start_with_tailscale.bat`**. It starts the monitor and shares port 8765 privately with `tailscale serve`.
3. On the phone, open the `https://<pc-name>.<tailnet>.ts.net` address that `tailscale serve status` prints. Add it to your home screen for one-tap access.

Use Tailscale **Serve**, not Funnel, and don't open ports on your router. Cloudflare Tunnel launchers are also included; see the [user guide](docs/USER_GUIDE.md#remote-access-launchers).

### 5. Optional: capture open ComfyUI tabs

Copy the `ComfyUI_MobileBridge` folder into `ComfyUI/custom_nodes/` and restart ComfyUI, then refresh ComfyUI in the PC browser. Workflows open in ComfyUI's tabs now appear in **Run & schedule**. You don't need this if you only run workflow files from `workflow_dirs`.

## Configuration

`config.json` keys (all optional, but folder browsing needs `output_dirs`):

| Key | Purpose |
| --- | --- |
| `workflow_dirs` | Folders holding workflow JSON (UI or API format) to run and schedule. |
| `converted_workflow_dir` | Where converted UI workflows are saved. Defaults to `converted/` in the first workflow folder. |
| `auto_convert_workflows` | Convert new or changed UI workflows automatically (about every 30 s). Default `true`. |
| `asset_dirs` | Folders of images you can pick as workflow inputs. |
| `output_dirs` | Folders shown in the output browser; also used for thumbnails. |
| `input_dir` | ComfyUI's input folder. Defaults to the `input` folder next to each output folder. |
| `ffmpeg_path` | Path to `ffmpeg.exe`. If empty, FFmpeg is looked up on PATH. |
| `ollama_url`, `ollama_model` | Ollama server and default model for the prompt tools. |

Environment variables: `COMFY_URL` (default `http://127.0.0.1:8188`), `MONITOR_PORT` (default `8765`), `HISTORY_LIMIT` (default `60`), `OLLAMA_URL`.

## Updating

```bat
git pull
```

Then tap **⟳ Restart** next to the version badge in the app, or close the monitor window and run `start_monitor.bat` again. If you changed `ComfyUI_MobileBridge`, copy it into `custom_nodes` again and restart ComfyUI.

Your `config.json`, chats, schedules and workflow snapshots are not tracked by git, so updates leave them alone. Changes between versions are listed in [CHANGELOG.md](CHANGELOG.md).

## Security

- The monitor binds to `127.0.0.1` only. Reach it remotely through Tailscale Serve, or a Cloudflare Tunnel protected by Cloudflare Access.
- Anyone who can open the page can queue jobs on your GPU and download your outputs, so keep access private.
- The file browser is read-only and limited to the configured folders. Path traversal, hidden files and symlinks are blocked.
- Actions that change things (running jobs, uploads, restart) need a per-session token embedded in the page, and cross-site requests are rejected.

## Troubleshooting

| Problem | Check |
| --- | --- |
| Output browser is empty | Set `output_dirs` in `config.json`, then restart the monitor. |
| No thumbnails, or slow folders | The startup banner must show FFmpeg. Thumbnails are cached in `.thumbs/` (safe to delete). |
| Slow on the phone | Run `tailscale ping <phone-name>` on the PC. `via 192.168.x.x` means direct; `via DERP` means a slow relay. |
| Workflow missing in Run & schedule | Put the JSON in a `workflow_dirs` folder, or install the bridge and refresh ComfyUI. |
| Video shows a poster but won't play | The phone's browser can't decode that codec; download the file instead. H.264 MP4 works best. |

More troubleshooting and a guide to every tab are in the **[user guide](docs/USER_GUIDE.md)**.

## Development

Run the tests (the two browser UI tests also need `pip install playwright` and Chromium):

```bat
py -3 -m unittest discover -s tests
```

### Releasing

The version lives in `VERSION` and is shown in the app. Write your changes under `## [Unreleased]` in `CHANGELOG.md`, then run:

```bat
py -3 release.py patch   :: bug fixes      6.2.0 -> 6.2.1
py -3 release.py minor   :: new features   6.2.0 -> 6.3.0
py -3 release.py major   :: big changes    6.2.0 -> 7.0.0
```

It bumps the version, dates the changelog, commits and creates a `vX.Y.Z` tag. Push with `git push --follow-tags`.

### Project layout

| Path | Contents |
| --- | --- |
| `comfy_monitor.py` | Entry point: HTTP routes, page, scheduler, workflow editing |
| `monitor_core.py` | ComfyUI queue/history access, downloads and ZIPs, base page |
| `live_monitor.py` | ComfyUI WebSocket progress and live previews |
| `media_browser.py` | Output browsing, thumbnails and the background thumbnail warmer |
| `workflow_convert.py` | UI-to-API workflow conversion |
| `ollama_chat.py` | Ollama chat and prompt rewriting |
| `*.js` | Front-end for each tab |
| `ComfyUI_MobileBridge/` | Optional ComfyUI extension that shares open tabs |
| `start_*.bat` | Windows launchers |
| `tests/` | Integration and browser tests |
