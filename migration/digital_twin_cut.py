"""Reading a cut off the file for Pilot Testing (2026-10-28).

ffmpeg does the looking: scene changes (``select='gt(scene,0.30)'`` +
showinfo on a downscaled decode), one small JPEG per scene group, the
silences on the audio track, and the duration. Whisper, when it is
installed on the host, gives timestamped lines. None of this calls a
model; ``digital_twin_watch`` captions the frames and runs the room.

Everything here is fail-safe: a missing tool returns an empty part and
``analyze_cut`` says which parts it could not read, so the beat sheet
can fall back to the clock and Play never dead-ends. Frames live in a
temp directory the caller removes; nothing from the file is kept past
the captions.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

SCENE_THRESHOLD = 0.30
MIN_SCENE_S = 2.0
MAX_SCENES = 420
GRID_S = 3.0          # a beat grid inside long scenes (2 to 4 seconds; 3 in the middle)
FRAME_WIDTH = 512
FRAME_QUALITY = 6     # ffmpeg -q:v (2 best, 31 worst)
SILENCE_DB = -35
SILENCE_MIN_S = 1.5
DECODE_WIDTH = 320    # scene detection runs on a small decode

_PTS_RX = re.compile(r"pts_time:\s*([0-9.]+)")
_SIL_START_RX = re.compile(r"silence_start:\s*([0-9.]+)")
_SIL_END_RX = re.compile(r"silence_end:\s*([0-9.]+)")


def tools_available() -> dict:
    out = {'ffmpeg': bool(shutil.which('ffmpeg')), 'ffprobe': bool(shutil.which('ffprobe')), 'whisper': False}
    try:
        import whisper  # type: ignore  # noqa: F401
        out['whisper'] = True
    except Exception:
        out['whisper'] = False
    return out


def probe_duration(path: str) -> Optional[float]:
    exe = shutil.which('ffprobe')
    if not exe or not path or not os.path.isfile(path):
        return None
    try:
        r = subprocess.run([exe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                           capture_output=True, text=True, timeout=40)
        v = float((r.stdout or '').strip() or 0)
        return v if v > 1 else None
    except Exception:
        return None


def detect_scene_cuts(path: str, threshold: float = SCENE_THRESHOLD, timeout_s: int = 1800) -> Optional[list]:
    """Seconds where a new shot starts, from ffmpeg's scene score on a
    small decode. None when ffmpeg is missing or failed; [] when the
    file has no cuts above the threshold."""
    exe = shutil.which('ffmpeg')
    if not exe or not path or not os.path.isfile(path):
        return None
    vf = f"scale={DECODE_WIDTH}:-2,select='gt(scene,{threshold:.2f})',showinfo"
    try:
        r = subprocess.run([exe, '-hide_banner', '-nostats', '-i', path, '-an', '-vf', vf, '-f', 'null', '-'],
                           capture_output=True, text=True, timeout=timeout_s)
    except Exception:
        return None
    text = (r.stderr or '') + (r.stdout or '')
    cuts = []
    for m in _PTS_RX.finditer(text):
        try:
            t = float(m.group(1))
        except ValueError:
            continue
        if t > 0.2 and (not cuts or t - cuts[-1] >= 0.4):
            cuts.append(round(t, 3))
    if r.returncode != 0 and not cuts:
        return None
    return cuts


def detect_silences(path: str, timeout_s: int = 900) -> Optional[list]:
    """[[start, end], ...] on the audio track. None when unavailable."""
    exe = shutil.which('ffmpeg')
    if not exe or not path or not os.path.isfile(path):
        return None
    try:
        r = subprocess.run([exe, '-hide_banner', '-nostats', '-i', path, '-vn', '-af', f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN_S}", '-f', 'null', '-'],
                           capture_output=True, text=True, timeout=timeout_s)
    except Exception:
        return None
    text = (r.stderr or '') + (r.stdout or '')
    starts = [float(x) for x in _SIL_START_RX.findall(text)]
    ends = [float(x) for x in _SIL_END_RX.findall(text)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        out.append([round(s, 3), round(e, 3) if e is not None else None])
    return out


def scenes_from_cuts(cuts: Optional[list], duration_s: float, min_scene_s: float = MIN_SCENE_S, max_scenes: int = MAX_SCENES) -> list:
    """Scene segments from cut times: short shots merge into the one
    before them, and a very long list merges its shortest neighbours
    until it fits. A file with almost no cuts gets a 12 second grid so
    every stretch still carries its own caption."""
    dur = max(1.0, float(duration_s or 0))
    bounds = [0.0] + sorted(float(c) for c in (cuts or []) if 0 < float(c) < dur) + [dur]
    segs = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a <= 0:
            continue
        if segs and (b - a) < min_scene_s:
            segs[-1][1] = b
        else:
            segs.append([a, b])
    if len(segs) < 4:
        step = 12.0 if dur > 60 else 6.0
        segs = []
        t = 0.0
        while t < dur:
            segs.append([t, min(dur, t + step)])
            t += step
    while len(segs) > max_scenes:
        i = min(range(len(segs) - 1), key=lambda k: (segs[k][1] - segs[k][0]) + (segs[k + 1][1] - segs[k + 1][0]))
        segs[i][1] = segs[i + 1][1]
        del segs[i + 1]
    return [{'id': i, 't_start': round(a, 3), 't_end': round(b, 3)} for i, (a, b) in enumerate(segs)]


def frame_plan(scenes: list, max_frames: int) -> list:
    """Which scenes get a frame (one per caption group), spread evenly
    when there are more scenes than frames. Returns [(scene_id, t)]."""
    if not scenes:
        return []
    n = len(scenes)
    if n <= max_frames:
        picks = list(range(n))
    else:
        picks = sorted({int(round(i * (n - 1) / float(max_frames - 1))) for i in range(max_frames)})
    out = []
    for i in picks:
        s = scenes[i]
        span = float(s['t_end']) - float(s['t_start'])
        t = float(s['t_start']) + min(1.0, span * 0.4)
        out.append((int(s['id']), round(t, 3)))
    return out


def sample_frames(path: str, plan: list, dest_dir: str, width: int = FRAME_WIDTH, workers: int = 6, timeout_s: int = 40) -> list:
    """One JPEG per planned time. [{scene_id, t, path}] for the ones
    that landed. Never raises."""
    exe = shutil.which('ffmpeg')
    if not exe or not path or not os.path.isfile(path) or not plan:
        return []
    os.makedirs(dest_dir, exist_ok=True)

    def one(item):
        sid, t = item
        out = os.path.join(dest_dir, f"f{sid:05d}.jpg")
        try:
            r = subprocess.run([exe, '-hide_banner', '-loglevel', 'error', '-y', '-ss', f"{max(0.0, float(t)):.3f}", '-i', path, '-frames:v', '1',
                                '-vf', f"scale={width}:-2", '-q:v', str(FRAME_QUALITY), out], capture_output=True, text=True, timeout=timeout_s)
            if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 400:
                return {'scene_id': sid, 't': float(t), 'path': out}
        except Exception:
            return None
        return None
    got = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for res in pool.map(one, plan):
            if res:
                got.append(res)
    got.sort(key=lambda f: f['t'])
    return got


def extract_audio(path: str, dest: str, timeout_s: int = 900) -> bool:
    exe = shutil.which('ffmpeg')
    if not exe or not path or not os.path.isfile(path):
        return False
    try:
        r = subprocess.run([exe, '-hide_banner', '-loglevel', 'error', '-y', '-i', path, '-vn', '-ac', '1', '-ar', '16000', dest],
                           capture_output=True, text=True, timeout=timeout_s)
        return r.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 64
    except Exception:
        return False


def transcribe(path: str) -> Optional[list]:
    """Timestamped lines [{t, end, text}] through Whisper when it is
    installed (its own ffmpeg decode); None otherwise. Never raises."""
    try:
        import whisper  # type: ignore
    except Exception:
        return None
    if not path or not os.path.isfile(path):
        return None
    try:
        model = whisper.load_model(os.environ.get('DTIQ_WHISPER_MODEL') or 'base')
        result = model.transcribe(path, verbose=False)
    except Exception:
        return None
    out = []
    for seg in (result or {}).get('segments') or []:
        try:
            text = ' '.join(str(seg.get('text') or '').split())
            if text:
                out.append({'t': round(float(seg.get('start') or 0), 3), 'end': round(float(seg.get('end') or 0), 3), 'text': text[:220]})
        except (TypeError, ValueError):
            continue
    return out or None


def analyze_cut(path: str, *, duration_s: float = 0, max_frames: Optional[int] = None, scene_threshold: float = SCENE_THRESHOLD,
                work_dir: Optional[str] = None, progress: Optional[Callable] = None, want_transcript: bool = True) -> dict:
    """Everything the watch needs from the file, in one place. Returns
    {runtime_s, scenes, frames, transcript, silences, tools, notes,
    source}. ``source`` is 'file' when scenes or a transcript came off
    the file, else 'clock'. Frames sit under work_dir (the caller
    removes it)."""
    tools = tools_available()
    notes = []
    tell = (lambda m: progress(m)) if progress else (lambda m: None)
    dur = probe_duration(path) if tools['ffprobe'] else None
    if not dur:
        dur = float(duration_s or 0) or None
        if tools['ffprobe'] is False:
            notes.append('ffprobe missing: runtime from the stated length')
    out = {'runtime_s': dur, 'scenes': [], 'frames': [], 'transcript': None, 'silences': None, 'tools': tools, 'notes': notes, 'source': 'clock'}
    if not path or not os.path.isfile(path):
        notes.append('no file on disk')
        return out
    if not tools['ffmpeg']:
        notes.append('ffmpeg missing on this host: clock beat sheet')
    cuts = None
    if tools['ffmpeg']:
        tell('reading the cut: scene changes')
        cuts = detect_scene_cuts(path, threshold=scene_threshold)
        if cuts is None:
            notes.append('scene detection failed')
    if dur and (cuts is not None):
        out['scenes'] = scenes_from_cuts(cuts, dur)
        out['source'] = 'file'
    if tools['ffmpeg']:
        tell('reading the cut: the sound')
        out['silences'] = detect_silences(path)
    if tools['ffmpeg'] and out['scenes']:
        tell('reading the cut: frames')
        n_frames = int(max_frames or 0) or int(max(40, min(240, (dur or 600) / 12.0)))
        wd = work_dir or tempfile.mkdtemp(prefix='dtiq-cut-')
        out['work_dir'] = wd
        out['frames'] = sample_frames(path, frame_plan(out['scenes'], n_frames), os.path.join(wd, 'frames'))
        if not out['frames']:
            notes.append('no frames could be sampled')
    if want_transcript and tools['whisper']:
        tell('reading the cut: the words')
        tr = transcribe(path)
        if tr is None and tools['ffmpeg']:
            wav = os.path.join(out.get('work_dir') or work_dir or tempfile.mkdtemp(prefix='dtiq-cut-'), 'audio.wav')
            if extract_audio(path, wav):
                tr = transcribe(wav)
        out['transcript'] = tr
        if tr:
            out['source'] = 'file'
        else:
            notes.append('no transcript')
    elif want_transcript and not tools['whisper']:
        notes.append('whisper missing on this host: no transcript')
    return out


def cleanup(analysis: Optional[dict]):
    wd = (analysis or {}).get('work_dir')
    if wd and os.path.isdir(wd):
        shutil.rmtree(wd, ignore_errors=True)


__all__ = ['SCENE_THRESHOLD', 'MIN_SCENE_S', 'MAX_SCENES', 'GRID_S', 'tools_available', 'probe_duration', 'detect_scene_cuts', 'detect_silences',
           'scenes_from_cuts', 'frame_plan', 'sample_frames', 'extract_audio', 'transcribe', 'analyze_cut', 'cleanup']
