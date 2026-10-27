# ComfyUI Mobile v6 — Windows dashboard

ComfyUI Mobile v6 is a **self-hosted Windows monitoring and workflow dashboard**. It uses Python 3.9+ and the standard library. It listens only on `127.0.0.1:8765` and should be reached from your phone using **private Tailscale Serve** (recommended), or a **Cloudflare Tunnel protected with Cloudflare Access**. It can queue GPU jobs and download your files: **never port-forward it or expose it without strong access control.**

## Changes from v5: Home, Queue, and reliable navigation

- **Fixed inert navigation:** tab switching now comes from an inline, self-contained navigation module, not the external scheduler/outputs scripts. It continues to switch tabs even if those extra scripts fail to load. Each tab has its own visible state, larger touch targets, an active indicator, and optional bookmark hashes (`#home`, `#queue`, `#plan`, `#outputs`). External scripts have updated `?v=6` URLs to avoid old browser caches.
- **New landing page — Home:** running, waiting and recent-history counts at the top; details/previews/step progress of the **currently running job** beneath; then the **Output Browser**, including its same folder/media/download controls. When idle, Home says the queue is idle.
- **New Queue & history tab:** all running and pending jobs, plus **compact recent history**. Recent jobs start collapsed with a short status and elapsed time when ComfyUI recorded real start/finish timestamps; tapping a row expands the job **inline** (images/videos, MP4 download, and the Files disclosure containing ZIP and JSON). History previews do not load until their row is expanded. The history JSON export remains at the top of this tab.
- **Run & schedule:** the v5 workflow planner is retained, including open-tab snapshots, API-format JSON workflows, editable LoRA/prompt/image controls, PC/generated/phone image selection, immediate queuing and schedule controls.
- **Output browser tab:** a dedicated view of the **same Output Browser** shown below Home. It is moved between tabs without duplicating file-browser state, and navigation does not reset the open folder. It refreshes when sufficiently stale (or with the explicit Refresh button), avoiding unnecessary remote requests.
- **Preserved:** live queue telemetry, best-effort live previews and sampler ETA, FFmpeg video thumbnails, streaming and Downloads, and separate local, Tailscale and two Cloudflare Windows BAT launchers.

## Changes in this update

- **Open-tab jobs now run fully.** The bridge's `graphToPrompt()` snapshot left out cg-use-everywhere links (VAE, SAMPLER, SIGMAS), so ComfyUI dropped every output that needed them and finished in ~0 s. The bridge now also sends the UI graph and the monitor adds those links. Refresh the ComfyUI browser tab after updating `ComfyUI_MobileBridge/web/monitor_bridge.js`. If ComfyUI still drops an output, Queue now shows which one and why.
- **LoRA sliders** (-0.2 to 1, exact value box beside) with on/off for Pixaroma LoRA Loader rows, rgthree Power Lora Loader rows and plain LoraLoader strengths.
- **Output settings** card: megapixels (e.g. ResolutionSelector) and constant nodes titled with length/duration/seconds (e.g. "Video Length (seconds)").
- **🗂️ Assets** tab and picker sources mirror ComfyUI's Media Assets panel: **Generated** (output) and **Imported** (input), via ComfyUI's `/api/assets`.
- **Total progress** like ComfyUI's queue badge: "Total: 38%" over the nodes the job runs, plus the running node's percentage.

## 1. Update or install on Windows

1. **Stop your old monitor** and retain `config.json`, `snapshots.json` and `schedules.json` (if those data files exist). Leave ComfyUI itself running.
2. Extract the v6 ZIP into a new folder such as `D:/ComfyUI-Mobile-v6/`. **Copy the retained data files into the new folder** if updating, or merge your old `config.json` instead of replacing it. Do not overwrite current schedules accidentally.
3. Ensure Python **3.9+** is installed, with the `py` launcher or `python` available. The app needs no external Python modules. Editing the supplied `config.json` is mandatory for folder browsing. Use forward slashes:

   ```json
   {
     "workflow_dirs": ["C:/ComfyUI_windows_portable/ComfyUI/user/default/workflows"],
     "converted_workflow_dir": "C:/ComfyUI_windows_portable/ComfyUI/user/default/workflows/converted",
     "auto_convert_workflows": true,
     "asset_dirs": ["C:/ComfyUI_windows_portable/ComfyUI/input", "D:/AIAssets"],
     "output_dirs": ["C:/ComfyUI_windows_portable/ComfyUI/output"],
     "ffmpeg_path": "C:/ffmpeg/bin/ffmpeg.exe"
   }
   ```

   These are **examples**, not discovered PC locations. `workflow_dirs` may hold API-format or normal UI/canvas workflow JSON: UI workflows are converted to API format (using ComfyUI's `/object_info`, so ComfyUI must be running) and saved under `converted_workflow_dir` (default: a `converted` subfolder of the first workflow folder; files from other folders go to `converted/<folder name>/`). With `auto_convert_workflows` on, new or changed UI workflows are converted within about 30 seconds of being added. Use `asset_dirs` for source images you want to choose as inputs, `output_dirs` for folders visible in Home/Output Browser and for locally generated video posters. FFmpeg is optional (empty `ffmpeg_path` will search your PATH); poster extraction requires it.
4. Run **`start_monitor.bat`**, then open `http://127.0.0.1:8765` on your Windows PC. Test Home, Queue & history, Run & schedule and Output browser. If the browser still shows v5, refresh the page fully or remove the old pinned website shortcut.
5. **Optional:** copy `ComfyUI_MobileBridge` into your ComfyUI `custom_nodes` folder and restart ComfyUI. Then refresh its PC browser and open each tab you want captured. This bridge supplies the currently visible open workflow to the mobile planner. For API-format workflows loaded from disk, the bridge isn't necessary.

## 2. Remote launchers — pick one

| BAT file | Function |
| --- | --- |
| **`start_with_tailscale.bat`** | **Recommended:** launches the monitor, then private Tailscale Serve for port 8765. Install Tailscale on both the Windows PC and phone; connect them to the same authorized tailnet. View the `.ts.net` HTTPS address shown by `tailscale serve status` on the phone. You do **not** need to buy a domain. |
| `start_with_cloudflare_named.bat` | Starts monitor + an existing named Cloudflare Tunnel. Route the hostname to `http://127.0.0.1:8765` and protect it with Cloudflare Access before publishing. A configured domain is generally required. |
| `start_with_cloudflare_quick.bat` | Starts monitor + temporary email-protected Quick Tunnel. Session expiry may trigger repeated authentication. Not recommended for everyday use. |
| `start_monitor.bat` | Starts only the locally bound dashboard. |

**Tailscale Serve, not Funnel.** Do not open ports on your router. Unlike early read-only versions, v6 can submit workflows, so remote security matters even more.

## 3. Navigation and how to use it

| Tab | What you see |
| --- | --- |
| 🏠 **Home** | Counts → **current running job** with node, steps, percentage, elapsed, current-sampler ETA and preview (when reported) → **Output Browser** below. The **See full queue** button switches to the Queue tab. |
| 📋 **Queue & history** | All running/waiting jobs at the top. Below, one-line recent-history rows showing elapsed execution duration when available. Tap a row to expand its image/video previews and downloads right there. A main **Download MP4** button appears when present; job ZIP, JSON and all individual downloads are nested under **Files · tap to download individually**. |
| 🗓️ **Run & schedule** | Captured open workflows or workflow JSON files (UI files marked ⇄ are auto-converted; **Re-convert** refreshes one), editable prompts/LoRA controls, Load Image inputs with a preview of the current picture, and the **Fantastic H3 Media Loader** item list (add pictures/videos/audio from the phone, PC assets or recent outputs; switch items on/off, reorder, remove, choose soundtrack mode). Run now, once/daily schedule and schedule list. |
| 📂 **Output browser** | The same browse/download panel from Home, shown on its own screen. Folder selections are shared between Home and this tab. |
| 💬 **Prompts** | A simple chat with the local Ollama: pick a model, attach images (vision models, marked 👁) or text files with 📎, and reopen or delete earlier conversations from **☰ History**. Conversations are saved on the PC in `chats/`. |

### ✨ Generate (prompt rewriting with Ollama)

Every prompt field in **Run & schedule** has a **✨ Generate** button. It opens a panel showing the current prompt; describe the change you want (e.g. "make it night time with rain") and Ollama rewrites the full prompt. Edit the result if needed, then **Use this prompt** to put it into the field, or **Modify this result** to keep iterating. Nothing is queued until you press Queue now.

Ollama must be running on the PC. The defaults are `http://127.0.0.1:11434` and `goonsai/qwen2.5-3B-goonsai-nsfw-100k:latest`; override them with `"ollama_url"` and `"ollama_model"` in `config.json`. Rewrites use a short editing system prompt and a lower `repeat_penalty` (1.05) than that model's default (1.3), which otherwise mangles text copied from the original prompt. The chat tab uses each model's own defaults, so long replies can stop at the model's `num_predict` limit (256 for the default model).

### Recent job elapsed times

ComfyUI history may include timestamps in `status.messages` under `execution_start` and `execution_success` (or error/interruption). v6 calculates the elapsed seconds from these **recorded events**. If timestamps are missing, the row shows **Time unavailable**. It does **not** fake elapsed duration using the wall clock or assume a run duration from the queue order. Running-job elapsed time is separately tracked by ComfyUI websocket events; if the monitor joins late it may say **since detected**.

### Queue telemetry and previews

ComfyUI may send step/total progress, percent, runtime, current-sampler ETA and an image preview to the monitor WebSocket. These are **best effort**. The percentage is **sampling-node progress, not the entire workflow**; an end-to-end ETA is not generally available. The monitor also attempts intermediate saved output or input images when appropriate. Some ComfyUI versions restrict binary preview frames to the original submitting client.

### Video playback/downloads

FFmpeg extracts thumbnails from recognized output files under configured `output_dirs`. The poster is loaded before the video starts where available. Browser-native playback still requires a supported codec (usually H.264 MP4 works best on Android). Original files are untouched; download and play in a native application if a browser cannot decode them. Downloads up to 128 MiB prefer an authenticated browser fetch; larger downloads use a browser-managed context and may still be affected by Cloudflare login issues. ZIPs are limited to 120 files / 2 GiB.

## 4. Troubleshooting

| Problem | Fix/check |
| --- | --- |
| Tabs still don't respond | Stop v5, start the v6 launcher from the **v6 extracted folder**. On mobile, hard refresh or clear the old website shortcut/cache. v6 has **four** top navigation buttons. Verify the local URL first. |
| Home missing Output Browser files | Configure `output_dirs` in `config.json`, restart the monitor. No directory is auto-discovered. |
| No elapsed on some completed jobs | Those particular ComfyUI history records may not include both start and finish timestamps; this is intentional, not an estimate bug. |
| Recent jobs appear minimized | Intentional: tap the recent row on **Queue & history** to view its video/image, MP4 button and the nested Files downloads. |
| MP4 thumbnail unavailable | Install FFmpeg, configure `ffmpeg_path` or PATH, and ensure the video resides within `output_dirs`. Generated files must be complete. |
| Video cannot play even with thumbnail | A poster doesn't guarantee a supported mobile video codec; download the original. |
| Queue ETA or preview missing | The workflow/node may not emit progress, or ComfyUI may restrict previews to the original client; whole-workflow ETAs aren't supplied. |
| Workflow absent from schedule tab | Open/refresh ComfyUI PC tab with the optional bridge installed, or place a workflow JSON (UI or API format) in `workflow_dirs`. |
| Converted workflow shows warnings | A node pack used by the workflow isn't installed, or a Use Everywhere node uses group/colour/regex rules the converter ignores. Install the pack, then tap **Re-convert**. |
| Output browser or Media Assets slow / tab reloads | Make sure FFmpeg is found (startup banner): tiles use small cached thumbnails from `.thumbs/` and fall back to full-size originals without it. Check the phone reaches the PC directly with `tailscale ping <phone>` ("via 192.168.x.x" = direct; "via DERP" = slow relay). |
| Cloudflare repeatedly asks you to log in | Use Tailscale Serve or a named tunnel protected by Cloudflare Access. The monitor cannot resolve external Cloudflare session expiry. |

## 5. Security and limitations

- The monitor is **not** read-only overall: workflow runner/scheduler can submit GPU jobs. Only permit trusted clients via Tailscale or authenticated Cloudflare Access; no direct public exposure.
- The file browser is read-only and constrained to configured roots; traversal paths, dotfiles, symlinks and unknown extensions are blocked. This doesn't make a publicly exposed workflow runner safe.
- Thumbnails and video posters require FFmpeg. They are cached in `.thumbs/` (capped at 20,000 files, safe to delete). Image uploads are limited to 40 MiB. Advanced custom nodes, LoRA stacks, nested workflow subgraphs, linked text inputs and unusual video output nodes may need tailored parsing.
- Scheduled jobs require the monitor, ComfyUI and PC to remain running. Every-24-hour recurrence is not timezone/DST aware. The browser must be online when accessing remote pages, not for previously stored scheduled execution.
- Python app's internal `/api/state` polling is normally every five seconds. Output browsing refreshes when necessary rather than on every tab switch.

## 6. Versions and git

The app version lives in `VERSION` and is shown next to the title (tap-and-hold/hover shows the git build) and at `/api/version`. A `+dirty` or `+N-gHASH` suffix means the running code has changes that are not in a release yet. History is in `CHANGELOG.md`; each release is a git tag (`git tag` lists them, `git checkout v6.0.0` goes back to one).

To release: note your changes under `## [Unreleased]` in `CHANGELOG.md`, then run

```
py -3 release.py patch   # fixes        6.1.0 -> 6.1.1
py -3 release.py minor   # new features 6.1.0 -> 6.2.0
py -3 release.py major   # big changes  6.1.0 -> 7.0.0
```

It bumps `VERSION`, dates the changelog, commits everything and tags `vX.Y.Z`. Then tap **⟳ Restart** next to the version in the app (or close the monitor window and run `start_monitor.bat` again) to serve the new code. Your chats, schedules, snapshots and thumbnail cache are git-ignored.

## 7. Testing and source files

- **15 Python integration/browser tests passed**, including all existing v4/v5 API tests, duration parsing, output path restrictions, queue telemetry, FFmpeg thumbnail extraction when available, and a simulated Chromium mobile UI test.
- The mobile test verified Home → Queue → Planner → Output Browser → Home, shared browser location, collapsed/revealed recent downloads, and that navigation still responds **without the optional external JS files**.
- Tests used a **mock ComfyUI API** and a headless Chromium test browser. This has **not been verified with your specific Windows ComfyUI install or Android connection**.

Core files: `comfy_monitor.py` (routes/scheduler/page), `monitor_core.py` (ComfyUI history/streaming/job UI), `live_monitor.py` (progress/preview WebSocket), `media_browser.py` (safe output browsing/posters), `schedule.js` and `outputs.js` (planner/browser UI), `ComfyUI_MobileBridge/` (optional open-workflow capture) and four BAT launchers. Source tests live under `tests/`. `config.json` is your local configuration, and `schedules.json`/`snapshots.json` are created as you use the application.

Useful documentation: [ComfyUI server API](https://docs.comfy.org/development/comfyui-server/comms_routes) · [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve) · [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) · [FFmpeg](https://ffmpeg.org/download.html)
