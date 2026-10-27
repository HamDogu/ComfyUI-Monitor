# ComfyUI Mobile: user guide

Detailed notes on each tab, remote access, troubleshooting and known limitations. For installation see the [README](../README.md).

- [The tabs](#the-tabs)
- [Prompt rewriting with Ollama](#prompt-rewriting-with-ollama)
- [Workflow folders and conversion](#workflow-folders-and-conversion)
- [Queue progress, previews and elapsed times](#queue-progress-previews-and-elapsed-times)
- [Videos, thumbnails and downloads](#videos-thumbnails-and-downloads)
- [Remote access launchers](#remote-access-launchers)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [History before version tracking](#history-before-version-tracking)

## The tabs

Each tab has a bookmarkable address: `#home`, `#queue`, `#plan`, `#outputs`, `#assets`.

| Tab | What you see |
| --- | --- |
| 🏠 **Home** | Running, waiting and recent-history counts → the **current running job** with node, steps, percentage, elapsed time, current-sampler ETA and preview (when reported) → recent outputs → the **output browser**. **See full queue** switches to the Queue tab. When idle, Home says the queue is idle. |
| 📋 **Queue & history** | All running and waiting jobs, then one-line recent-history rows with elapsed time when available. Tap a row to expand its previews and downloads in place. A main **Download MP4** button appears when there is one; the job ZIP, JSON and individual files are under **Files · tap to download individually**. Previews don't load until a row is expanded. The history JSON export is at the top. |
| 🗓️ **Run & schedule** | Captured open workflows or workflow JSON files (UI files marked ⇄ are converted automatically; **Re-convert** refreshes one). Editable prompts, LoRA sliders (-0.2 to 1, with on/off) for Pixaroma, rgthree Power Lora Loader and plain LoraLoader rows, output settings (megapixels and nodes titled with length/duration/seconds), Load Image inputs with a preview, and the **Fantastic H3 Media Loader** item list (add pictures, videos and audio from the phone, PC assets or recent outputs; switch on/off, reorder, remove, pick soundtrack mode). Run now, schedule once or daily, and the schedule list. |
| 📂 **Output browser** | The same browse and download panel as on Home, on its own screen. The open folder is shared between Home and this tab. Grid or list view; images open full size when tapped; up to 4 videos autoplay muted. |
| 🗂️ **Assets** | ComfyUI's Media Assets lists: **Generated** (output) and **Imported** (input), newest first, with search. Files come from disk; if ComfyUI is busy, the list comes from cache or disk and says so. |
| 💬 **Prompts** | A chat with the local Ollama: pick a model, attach images (vision models, marked 👁) or text files with 📎, and reopen or delete earlier conversations from **☰ History**. Conversations are saved on the PC in `chats/`. |

The **version badge** next to the title shows the running version (hover or long-press for the exact git build; `+dirty` means uncommitted changes). **⟳ Restart** restarts the monitor on the PC to load updated code; ComfyUI and its queue are not affected.

## Prompt rewriting with Ollama

Every prompt field in **Run & schedule** has a **✨ Generate** button. It opens a panel showing the current prompt. Describe the change you want (for example "make it night time with rain") and Ollama rewrites the whole prompt. Edit the result if needed, then **Use this prompt** to put it in the field, or **Modify this result** to keep iterating. Nothing is queued until you press Queue now.

Ollama must be running on the PC. The defaults are `http://127.0.0.1:11434` and the model `goonsai/qwen2.5-3B-goonsai-nsfw-100k:latest`; change them with `ollama_url` and `ollama_model` in `config.json`. Rewrites use a short editing system prompt and a lower `repeat_penalty` (1.05) than that model's default (1.3), which otherwise mangles text copied from the original prompt. The chat tab uses each model's own defaults, so long replies can stop at the model's `num_predict` limit (256 for the default model).

## Workflow folders and conversion

`workflow_dirs` may hold API-format or normal UI (canvas) workflow JSON. UI workflows are converted to API format using ComfyUI's `/object_info`, so ComfyUI must be running. Converted files are saved under `converted_workflow_dir` (default: a `converted` subfolder of the first workflow folder; files from other folders go to `converted/<folder name>/`). With `auto_convert_workflows` on, new or changed UI workflows are converted within about 30 seconds.

**Open ComfyUI tabs** are captured by the optional `ComfyUI_MobileBridge` extension. ComfyUI's `graphToPrompt()` snapshot leaves out cg-use-everywhere links (VAE, SAMPLER, SIGMAS), so the bridge also sends the UI graph and the monitor adds those links back. After updating `ComfyUI_MobileBridge/web/monitor_bridge.js`, refresh the ComfyUI browser tab. If ComfyUI still drops an output, the Queue tab shows which one and why.

## Queue progress, previews and elapsed times

ComfyUI may send step/total progress, percentage, runtime, current-sampler ETA and an image preview over its WebSocket. These are **best effort**. The node percentage is **sampling-node progress, not the whole workflow**; the **Total** figure (like ComfyUI's queue badge) covers the nodes the job runs. An end-to-end ETA is not generally available. The monitor also tries intermediate saved outputs or input images where it can. Some ComfyUI versions only send binary preview frames to the client that submitted the job.

**Elapsed times** for finished jobs come from the timestamps ComfyUI records in `status.messages` (`execution_start` and `execution_success`, or error/interruption). If they are missing the row shows **Time unavailable**; the monitor never estimates a duration from the clock or queue order. Running-job elapsed time comes from WebSocket events; if the monitor joined late it says **since detected**.

## Videos, thumbnails and downloads

- Thumbnails (max 512 px JPEG) are made with FFmpeg for images and videos under `output_dirs` and the input folder, and cached in `.thumbs/` (capped at 20,000 files; safe to delete). A background task pre-renders thumbnails for new files at low CPU priority.
- Grid tiles load thumbnails, not originals. Tapping an image opens the full-size file.
- Only 4 grid videos play at once, and videos are unloaded when scrolled off screen to save phone memory.
- Browser playback needs a codec the phone supports (H.264 MP4 is safest on Android). Original files are never changed; download them to play in another app.
- Downloads up to 128 MiB use an authenticated browser fetch; larger ones use a normal browser download, which may still hit Cloudflare login problems. Job ZIPs are limited to 120 files / 2 GiB.

## Remote access launchers

| Launcher | What it does |
| --- | --- |
| **`start_with_tailscale.bat`** | **Recommended.** Starts the monitor, then private Tailscale Serve on port 8765. Install Tailscale on the PC and phone and join the same tailnet. Open the `.ts.net` HTTPS address shown by `tailscale serve status`. No domain needed. |
| `start_with_cloudflare_named.bat` | Starts the monitor and an existing named Cloudflare Tunnel. Route the hostname to `http://127.0.0.1:8765` and protect it with Cloudflare Access before publishing. Usually needs your own domain. |
| `start_with_cloudflare_quick.bat` | Starts the monitor and a temporary email-protected Quick Tunnel. Sessions can expire and ask you to log in repeatedly; not recommended for daily use. |
| `start_monitor.bat` | Starts only the local dashboard. Also restarts it when you tap **⟳ Restart**. |

Use Tailscale **Serve**, not Funnel, and don't open router ports. The app can submit workflows, so remote security matters.

## Troubleshooting

| Problem | Fix or check |
| --- | --- |
| Tabs don't respond | Make sure you're running this folder's launcher. On the phone, hard-refresh or remove an old home-screen shortcut. Check the local URL on the PC first. |
| Output browser is empty | Configure `output_dirs` in `config.json` and restart. No folder is discovered automatically. |
| Output browser or Assets slow, or the tab reloads | Make sure FFmpeg is found (startup banner); without it tiles fall back to full-size originals. Check the phone reaches the PC directly with `tailscale ping <phone>` (`via 192.168.x.x` = direct; `via DERP` = slow relay). |
| No elapsed time on some jobs | Those ComfyUI history records lack start or finish timestamps. This is intentional, not a bug. |
| Recent jobs look collapsed | Intentional: tap a row in **Queue & history** to see its previews and downloads. |
| Video thumbnail missing | Install FFmpeg, set `ffmpeg_path` or PATH, and check the video is inside `output_dirs` and finished writing. |
| Video won't play even with a thumbnail | The phone can't decode that codec; download the original. |
| Queue ETA or preview missing | The node may not report progress, or ComfyUI only sends previews to the original client. Whole-workflow ETAs aren't available. |
| Workflow missing from Run & schedule | Open or refresh the ComfyUI tab with the bridge installed, or put the JSON (UI or API format) in `workflow_dirs`. |
| Converted workflow shows warnings | A node pack the workflow uses isn't installed, or a Use Everywhere node uses group/colour/regex rules the converter ignores. Install the pack, then tap **Re-convert**. |
| Cloudflare keeps asking you to log in | Use Tailscale Serve or a named tunnel with Cloudflare Access. The monitor can't fix Cloudflare session expiry. |
| Restart button doesn't bring the app back | Check the monitor window on the PC. The app must have been started with `start_monitor.bat` (or `py -3 comfy_monitor.py`). |

## Limitations

- The app is **not** read-only: the runner and scheduler submit GPU jobs. Only allow trusted devices.
- Image uploads are limited to 40 MiB and media uploads to 1 GiB.
- Advanced custom nodes, nested subgraphs, linked text inputs and unusual video output nodes may need tailored parsing.
- Scheduled jobs need the monitor, ComfyUI and the PC to stay running. Daily recurrence is every 24 hours and doesn't adjust for daylight saving.
- The page polls `/api/state` every 5 seconds. The output browser only refreshes when stale or when you press Refresh.

## History before version tracking

Version numbers start at 6.0.0; later changes are in [CHANGELOG.md](../CHANGELOG.md). Earlier notable changes:

**v6 (from v5): Home, Queue and reliable navigation**
- Tab switching moved into a self-contained inline module, so it keeps working if other scripts fail to load. Bookmark hashes and larger touch targets were added.
- New **Home** landing page with counts, the current job and the output browser.
- New **Queue & history** tab with compact, expandable history rows.
- **Run & schedule** kept the v5 planner: open-tab snapshots, API-format workflows, editable LoRA/prompt/image controls, immediate queuing and scheduling.
- Dedicated **Output browser** tab sharing state with Home.

**Later v6 updates**
- Open-tab jobs run fully: cg-use-everywhere links are restored from the UI graph.
- LoRA sliders with on/off; output settings card for megapixels and duration nodes.
- **Assets** tab and picker sources mirroring ComfyUI's Media Assets panel.
- **Total progress** across the job's nodes, like ComfyUI's queue badge.
