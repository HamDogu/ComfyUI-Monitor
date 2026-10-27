"""Read-only, constrained output-folder browser and FFmpeg thumbnail renderer."""
from pathlib import Path
import hashlib
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time

OUTPUT_EXT = frozenset('.png .jpg .jpeg .webp .gif .bmp .avif .apng .tif .tiff .exr .hdr .mp4 .m4v .webm .mov .mkv .avi .mpg .mpeg .gif .wav .mp3 .ogg .flac .aac .m4a .json .txt .csv .zip .glb .gltf .obj .ply .stl .fbx .webp .srt'.split())
VIDEO_EXT = frozenset('.mp4 .webm .mov .m4v .mkv .avi .mpg .mpeg'.split())
IMAGE_EXT = frozenset('.png .jpg .jpeg .webp .gif .bmp .avif .apng .tif .tiff'.split())
THUMB_EXT = (IMAGE_EXT | VIDEO_EXT) - {'.exr', '.hdr'}
# Thumbnails persist on disk so they survive restarts and never need ComfyUI.
THUMB_DIR = Path(__file__).resolve().parent / '.thumbs'
THUMB_SIZE = 512
MAX_THUMBS = 20000
# Requests from the phone; the background warmer runs one extra FFmpeg of its own.
FFMPEG_LIMIT = threading.BoundedSemaphore(3)
# Thumbnails must never compete with ComfyUI for CPU time.
LOW_PRIORITY = (getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0) |
                getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def clean_rel(path, allow_empty=False):
    if not isinstance(path, str) or ('\\' in path or '\x00' in path) or len(path) > 1400:
        raise ValueError('Invalid path')
    if path == '' and allow_empty:
        return []
    parts = path.split('/')
    if any(segment in ('', '.', '..') or segment.startswith('.') for segment in parts):
        raise ValueError('Invalid folder or file path')
    return parts


def within(root, rel, folder=False):
    root = Path(root).expanduser().resolve()
    value = root.joinpath(*clean_rel(rel, allow_empty=folder))
    # A symlink inside the output folder must never escape its configured root.
    if not value.resolve().is_relative_to(root):
        raise ValueError('Output path is outside its configured root')
    if (folder and not value.is_dir()) or (not folder and not value.is_file()):
        raise FileNotFoundError('Output item does not exist')
    return value


def select_root(roots, idx):
    try:
        n = int(idx)
    except (ValueError, TypeError):
        raise ValueError('Invalid output root') from None
    if n < 0 or n >= len(roots):
        raise ValueError('Unknown output directory')
    root = Path(roots[n]).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('Output directory missing; update config.json')
    return root


def browse(roots, idx, path='', offset=0, limit=120):
    root = select_root(roots, idx)
    directory = within(root, path, True)
    try:
        offset = int(offset)
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValueError('Invalid page options') from None
    if not 0 <= offset <= 1000000 or not 1 <= limit <= 200:
        raise ValueError('Invalid page size')
    dirs, files = [], []
    # scandir avoids unnecessarily stat-ing every nested child folder.
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name.startswith('.') or entry.is_symlink():
                continue
            try:
                rel = (path + '/' if path else '') + entry.name
                if entry.is_dir(follow_symlinks=False):
                    dirs.append({'name': entry.name, 'path': rel, 'kind': 'folder'})
                elif entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in OUTPUT_EXT:
                    meta = entry.stat(follow_symlinks=False)
                    files.append({'name': entry.name, 'path': rel, 'kind': 'file',
                                  'media': file_kind(entry.name), 'size': meta.st_size,
                                  'modified': meta.st_mtime})
            except (OSError, ValueError):
                continue
    dirs.sort(key=lambda row: row['name'].lower())
    files.sort(key=lambda row: row['modified'], reverse=True)
    all_entries = dirs + files
    return {'roots': [{'index': i, 'label': Path(r).name or str(r)} for i, r in enumerate(roots)],
            'root': int(idx), 'path': path, 'count': len(all_entries), 'next_offset': offset + limit if offset + limit < len(all_entries) else None,
            'items': all_entries[offset:offset + limit]}


def file_kind(name):
    ext = Path(name).suffix.lower()
    return 'image' if ext in IMAGE_EXT else 'video' if ext in VIDEO_EXT else 'file'


def get_file(roots, idx, relative):
    root = select_root(roots, idx)
    file = within(root, relative)
    if file.suffix.lower() not in OUTPUT_EXT:
        raise ValueError('Output file type not allowed')
    return file


def related_job_file(roots, record_file):
    """Locate a ComfyUI /view output on disk, under configured output root only."""
    name = record_file.get('filename', '')
    sub = record_file.get('subfolder') or ''
    if record_file.get('type', 'output') != 'output':
        return None
    try:
        relative = '/'.join(clean_rel(sub, True) + clean_rel(name))
    except ValueError:
        return None
    for i in range(len(roots)):
        try:
            return get_file(roots, i, relative)
        except (ValueError, OSError):
            continue
    return None


def ffmpeg_executable(config):
    configured = config.get('ffmpeg_path', '')
    return configured if configured and Path(configured).is_file() else shutil.which('ffmpeg')


def thumb_path(file):
    stat = file.stat()
    key = f'{file}|{stat.st_mtime_ns}|{stat.st_size}'.encode('utf-8', 'surrogatepass')
    return THUMB_DIR / (hashlib.sha1(key).hexdigest() + '.jpg')


def cached_thumbnail(file):
    """The thumbnail bytes if already rendered, else None."""
    try:
        return thumb_path(file).read_bytes()
    except OSError:
        return None


def render_thumbnail(file, config, limit=FFMPEG_LIMIT, wait=6):
    """Render one small JPEG for an image or video (first frame) into the disk cache."""
    ffmpeg = ffmpeg_executable(config)
    if not ffmpeg:
        raise FileNotFoundError('FFmpeg unavailable; configure ffmpeg_path or install it on PATH')
    if file.suffix.lower() not in THUMB_EXT:
        raise ValueError('No thumbnail for this file type')
    target = thumb_path(file)
    if not limit.acquire(timeout=wait):
        raise TimeoutError('Thumbnail service is busy')
    try:
        if target.is_file():  # Rendered by another request while this one waited.
            return target.read_bytes()
        seek = ['-ss', '0.25'] if file.suffix.lower() in VIDEO_EXT else []
        command = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-threads', '2', *seek,
                   '-i', str(file), '-an', '-frames:v', '1',
                   '-vf', f"scale='min({THUMB_SIZE},iw)':'min({THUMB_SIZE},ih)':force_original_aspect_ratio=decrease",
                   '-f', 'image2pipe', '-vcodec', 'mjpeg', '-q:v', '5', 'pipe:1']
        try:
            done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=18,
                                  check=False, creationflags=LOW_PRIORITY)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise ValueError('FFmpeg thumbnail extraction timed out or could not start') from exc
        if done.returncode or not done.stdout.startswith(b'\xff\xd8') or len(done.stdout) > 3 * 1024 * 1024:
            raise ValueError('FFmpeg could not extract a frame (file may be incomplete or unsupported)')
        THUMB_DIR.mkdir(exist_ok=True)
        temp = target.with_suffix(f'.{threading.get_ident()}.tmp')
        temp.write_bytes(done.stdout)
        os.replace(temp, target)
        return done.stdout
    finally:
        limit.release()


def thumbnail(file, config):
    return cached_thumbnail(file) or render_thumbnail(file, config)


# Video posters predate image thumbnails; both now share the disk cache.
poster = thumbnail


def media_files(root, limit=5000):
    """Thumbnail-able files under root, newest first."""
    found = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for name in names:
            if name.startswith('.') or Path(name).suffix.lower() not in THUMB_EXT:
                continue
            path = Path(folder) / name
            try:
                if not path.is_symlink():
                    found.append((path.stat().st_mtime, path))
            except OSError:
                continue
    found.sort(key=lambda row: row[0], reverse=True)
    return [path for _, path in found[:limit]]


def prune_thumbnails():
    try:
        thumbs = sorted(THUMB_DIR.glob('*.jpg'), key=lambda f: f.stat().st_atime)
    except OSError:
        return
    for stale in thumbs[:max(0, len(thumbs) - MAX_THUMBS)]:
        try:
            stale.unlink()
        except OSError:
            pass


def warm_thumbnails(roots, config, interval=30):
    """Background loop: pre-render thumbnails for new outputs so folders open instantly."""
    own = threading.BoundedSemaphore(1)
    passes = 0
    while True:
        if ffmpeg_executable(config):
            for root in roots:
                for file in media_files(root) if Path(root).is_dir() else []:
                    try:
                        if not thumb_path(file).is_file():
                            render_thumbnail(file, config, limit=own, wait=60)
                    except (OSError, ValueError, TimeoutError):
                        continue  # Partly written or unsupported; retried next pass.
            if passes % 120 == 0:
                prune_thumbnails()
            passes += 1
        time.sleep(interval)


def mime(path):
    return mimetypes.guess_type(path.name)[0] or 'application/octet-stream'


def parse_range(header, size):
    """Return inclusive single byte range, or None if no Range requested."""
    if not header:
        return None
    matched = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
    if not matched or not any(matched.groups()):
        raise ValueError('Invalid byte range')
    a, b = matched.groups()
    if not a:
        length = int(b)
        if length <= 0 or size == 0:
            raise ValueError('Unsatisfiable range')
        return max(0, size - length), size - 1
    start = int(a)
    end = min(size - 1, int(b)) if b else size - 1
    if start >= size or end < start:
        raise ValueError('Unsatisfiable range')
    return start, end


def send_local(handler, path, download=False, head=False, cache_seconds=0):
    size = path.stat().st_size
    cache = f'private, max-age={cache_seconds}' if cache_seconds else 'no-store'
    try:
        range_value = parse_range(handler.headers.get('Range'), size)
    except ValueError:
        handler.send_response(416)
        handler.send_header('Content-Range', f'bytes */{size}')
        handler.send_header('Content-Length', '0')
        handler.base_headers()
        handler.end_headers()
        return
    start, end = range_value if range_value is not None else (0, size - 1)
    length = max(0, end - start + 1)
    handler.send_response(206 if range_value else 200)
    handler.send_header('Content-Type', mime(path))
    handler.base_headers(cache)
    handler.send_header('Accept-Ranges', 'bytes')
    if range_value:
        handler.send_header('Content-Range', f'bytes {start}-{end}/{size}')
    handler.send_header('Content-Length', str(length))
    if download:
        import monitor_core as core
        handler.send_header('Content-Disposition', core.disposition(path.name))
    handler.end_headers()
    if head or not length:
        return
    with path.open('rb') as stream:
        stream.seek(start)
        try:
            remain = length
            while remain:
                block = stream.read(min(256 * 1024, remain))
                if not block:
                    break
                handler.wfile.write(block)
                remain -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            pass
