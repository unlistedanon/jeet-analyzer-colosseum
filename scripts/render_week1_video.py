"""Render the alias-only Eternal Week 1 presentation with local Windows tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Iterable, Mapping
from urllib.error import URLError
from urllib.request import urlopen
import wave


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "frontend" / "src" / "video" / "week1-video.json"
SRT_PATH = ROOT / "competition" / "eternal-week1" / "week1-video.srt"
BUILD_ROOT = ROOT / "build" / "eternal-week1" / "video"
SEGMENTS_DIR = BUILD_ROOT / "narration-segments"
FRAMES_DIR = BUILD_ROOT / "frames"
REVIEW_DIR = BUILD_ROOT / "review-frames"
NARRATION_PATH = BUILD_ROOT / "week1-narration.wav"
VIDEO_PATH = BUILD_ROOT / "jeet-analyzer-week1.mp4"
QUALITY_PATH = BUILD_ROOT / "quality-report.json"
PUBLIC_AUDIO_PATH = ROOT / "frontend" / "public" / "demo" / "week1-narration.wav"
DEMO_RESULT_PATH = ROOT / "build" / "eternal-week1" / "flagship-result.json"
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
MEDIA_HELPER = ROOT / "scripts" / "week1_video_media.ps1"
BASE58 = re.compile(r"(?<![A-Za-z0-9])[1-9A-HJ-NP-Za-km-z]{32,100}(?![A-Za-z0-9])")


class VideoBuildError(RuntimeError):
    """Raised when the deterministic local video cannot be produced safely."""


def load_video_plan(path: Path = PLAN_PATH) -> dict[str, Any]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("schema") != "jeet-analyzer.week1-video.v1":
        raise VideoBuildError("unsupported Week 1 video schema")
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != 7:
        raise VideoBuildError("Week 1 video must contain exactly seven scenes")
    total = 0.0
    for scene in scenes:
        captions = scene.get("captions")
        if not isinstance(captions, list) or not captions:
            raise VideoBuildError("every scene must contain captions")
        caption_total = sum(float(item["duration_seconds"]) for item in captions)
        if caption_total != float(scene["duration_seconds"]):
            raise VideoBuildError(f"caption duration mismatch in scene {scene.get('id')}")
        total += caption_total
    if total != float(plan.get("total_duration_seconds")) or not 90 <= total <= 110:
        raise VideoBuildError("video duration must remain between 90 and 110 seconds")
    text = json.dumps(plan, sort_keys=True)
    if BASE58.search(text) or re.search(r"(?i)(?:[A-Z]:\\Users\\|/Users/|/home/)", text):
        raise VideoBuildError("video plan contains a private identifier or personal path")
    return plan


def caption_timeline(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    for scene_index, scene in enumerate(plan["scenes"]):
        for caption_index, caption in enumerate(scene["captions"]):
            duration = float(caption["duration_seconds"])
            timeline.append({
                "scene": scene_index,
                "caption": caption_index,
                "start": cursor,
                "end": cursor + duration,
                "duration": duration,
                "text": str(caption["text"]),
            })
            cursor += duration
    return timeline


def render_srt(plan: Mapping[str, Any]) -> str:
    blocks = []
    for index, caption in enumerate(caption_timeline(plan), 1):
        blocks.append(
            f"{index}\n{_srt_time(caption['start'])} --> {_srt_time(caption['end'])}\n{caption['text']}"
        )
    return "\n\n".join(blocks) + "\n"


def _srt_time(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _run(command: list[str], cwd: Path = ROOT, *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _powershell(action: str, **arguments: Path) -> subprocess.CompletedProcess[str]:
    command = [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(MEDIA_HELPER), "-Action", action, "-ConfigPath", str(PLAN_PATH)]
    for key, value in arguments.items():
        command.extend([f"-{key}", str(value)])
    return _run(command, capture=True)


def build_narration(plan: Mapping[str, Any]) -> dict[str, Any]:
    SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    for stale in SEGMENTS_DIR.glob("segment-*.wav"):
        stale.unlink()
    completed = _powershell("Narrate", OutputDirectory=SEGMENTS_DIR)
    voice = next((line.split("=", 1)[1] for line in completed.stdout.splitlines() if line.startswith("VOICE=")), "UNKNOWN LOCAL VOICE")
    timeline = caption_timeline(plan)
    segment_paths = sorted(SEGMENTS_DIR.glob("segment-*.wav"))
    if len(segment_paths) != len(timeline):
        raise VideoBuildError("local narration did not produce every caption segment")
    NARRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    durations: list[float] = []
    output: wave.Wave_write | None = None
    try:
        output = wave.open(str(NARRATION_PATH), "wb")
        expected_params = None
        for path, caption in zip(segment_paths, timeline, strict=True):
            with wave.open(str(path), "rb") as source:
                params = source.getparams()
                comparable = (params.nchannels, params.sampwidth, params.framerate, params.comptype)
                if expected_params is None:
                    expected_params = comparable
                    output.setparams(params)
                elif comparable != expected_params:
                    raise VideoBuildError("local narration segments use inconsistent wave formats")
                frames = source.readframes(params.nframes)
                duration = params.nframes / params.framerate
                durations.append(duration)
                target_frames = round(float(caption["duration"]) * params.framerate)
                if params.nframes > target_frames:
                    raise VideoBuildError(
                        f"narration segment exceeds its caption slot: {path.name} {duration:.2f}s > {caption['duration']:.2f}s"
                    )
                output.writeframes(frames)
                padding = target_frames - params.nframes
                output.writeframes(b"\0" * padding * params.nchannels * params.sampwidth)
    finally:
        if output is not None:
            output.close()
    PUBLIC_AUDIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(NARRATION_PATH, PUBLIC_AUDIO_PATH)
    return {"voice": voice, "segment_durations": durations, "duration_seconds": _wave_duration(NARRATION_PATH)}


def _wave_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / source.getframerate()


def _find_edge() -> Path:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise VideoBuildError("Microsoft Edge is not installed in a supported location")


def _server_ready() -> bool:
    try:
        with urlopen("http://127.0.0.1:5173/?demo=week1-video", timeout=2) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def _start_server() -> subprocess.Popen[str] | None:
    if _server_ready():
        return None
    node = shutil.which("node")
    vite = ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    if not node or not vite.is_file():
        raise VideoBuildError("local Node/Vite dependencies are unavailable")
    process = subprocess.Popen(
        [node, str(vite), "--host", "127.0.0.1", "--port", "5173"],
        cwd=ROOT / "frontend",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(60):
        if _server_ready():
            return process
        if process.poll() is not None:
            raise VideoBuildError("local Vite server exited before the video route became ready")
        time.sleep(0.25)
    process.terminate()
    raise VideoBuildError("local video route did not become ready")


def capture_frames(plan: Mapping[str, Any]) -> list[Path]:
    edge = _find_edge()
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    for stale in FRAMES_DIR.glob("frame-*.png"):
        stale.unlink()
    profile = BUILD_ROOT / "edge-profile"
    profile.mkdir(parents=True, exist_ok=True)
    captured = []
    for index, caption in enumerate(caption_timeline(plan), 1):
        destination = FRAMES_DIR / f"frame-{index:03d}.png"
        url = f"http://127.0.0.1:5173/?demo=week1-video&capture=1&scene={caption['scene']}&caption={caption['caption']}"
        _run([
            str(edge), "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
            "--disable-features=msEdgeFirstRunExperience", f"--user-data-dir={profile}",
            "--window-size=1920,1080", "--force-device-scale-factor=1", "--virtual-time-budget=1500",
            "--run-all-compositor-stages-before-draw", f"--screenshot={destination}", url,
        ])
        if not destination.is_file() or destination.stat().st_size < 10_000:
            raise VideoBuildError(f"page-only frame capture failed: {destination.name}")
        captured.append(destination)
    return captured


def encode_video() -> dict[str, str]:
    completed = _powershell("Encode", FramesDirectory=FRAMES_DIR, AudioPath=NARRATION_PATH, VideoPath=VIDEO_PATH)
    values = {}
    for line in completed.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    if values.get("TRANSCODE_RESULT") != "None" or not VIDEO_PATH.is_file():
        raise VideoBuildError("Windows MediaComposition did not emit a successful MP4")
    return values


def representative_frames(plan: Mapping[str, Any], frames: list[Path]) -> list[Path]:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    timeline = caption_timeline(plan)
    review_times = [5, 25, 45, 65, 85, float(plan["total_duration_seconds"]) - 0.1]
    selected = []
    for seconds in review_times:
        index = next((index for index, row in enumerate(timeline) if row["start"] <= seconds < row["end"]), len(timeline) - 1)
        destination = REVIEW_DIR / ("final.png" if seconds == review_times[-1] else f"{int(seconds):03d}s.png")
        shutil.copy2(frames[index], destination)
        selected.append(destination)
    return selected


def audit_public_inputs(plan: Mapping[str, Any]) -> None:
    result = json.loads(DEMO_RESULT_PATH.read_text(encoding="utf-8"))
    combined = json.dumps({"plan": plan, "result": result}, sort_keys=True)
    if BASE58.search(combined):
        raise VideoBuildError("public video inputs contain a blockchain identifier")
    if result.get("mint") != "[REDACTED TOKEN]" or result.get("common_control") != "NOT_PROVEN":
        raise VideoBuildError("public demo redaction or common-control truth changed")
    if re.search(r"(?i)(?:[A-Z]:\\Users\\|[A-Z]:\\josh420-devnet\\|https?://[^\s]+[?&](?:api[_-]?key|token|secret)=)", combined):
        raise VideoBuildError("public video inputs contain a private path or authenticated URL")


def inspect_video(plan: Mapping[str, Any], narration: Mapping[str, Any], media: Mapping[str, str], reviews: Iterable[Path]) -> dict[str, Any]:
    payload = VIDEO_PATH.read_bytes()
    report = {
        "schema": "jeet-analyzer.week1-video-quality.v1",
        "path": VIDEO_PATH.relative_to(ROOT).as_posix(),
        "duration_seconds": float(media["DURATION_SECONDS"]),
        "width": int(media["WIDTH"]),
        "height": int(media["HEIGHT"]),
        "video_codec": "H.264" if b"avc1" in payload else "UNVERIFIED",
        "audio_codec": "AAC" if b"mp4a" in payload else "UNVERIFIED",
        "audio_present": b"soun" in payload and b"mp4a" in payload,
        "file_size_bytes": VIDEO_PATH.stat().st_size,
        "narration_voice": narration["voice"],
        "narration_duration_seconds": narration["duration_seconds"],
        "representative_frames": [path.relative_to(ROOT).as_posix() for path in reviews],
        "provider_calls": 0,
        "privacy_audit": "PASS",
    }
    if not 90 <= report["duration_seconds"] <= 110 or (report["width"], report["height"]) != (1920, 1080):
        raise VideoBuildError("rendered video duration or dimensions are outside the required contract")
    if report["video_codec"] != "H.264" or not report["audio_present"]:
        raise VideoBuildError("rendered MP4 is missing H.264 video or AAC audio")
    QUALITY_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the local alias-only Eternal Week 1 video")
    parser.add_argument("--skip-video", action="store_true", help="Generate narration and subtitles without encoding MP4")
    args = parser.parse_args(argv)
    plan = load_video_plan()
    _run([sys.executable, str(ROOT / "scripts" / "demo_flagship.py")])
    SRT_PATH.write_text(render_srt(plan), encoding="utf-8")
    audit_public_inputs(plan)
    narration = build_narration(plan)
    if args.skip_video:
        print(f"NARRATION={NARRATION_PATH.relative_to(ROOT).as_posix()}")
        print(f"SUBTITLES={SRT_PATH.relative_to(ROOT).as_posix()}")
        print("PROVIDER_CALLS=0")
        return 0
    server = _start_server()
    try:
        frames = capture_frames(plan)
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
    media = encode_video()
    reviews = representative_frames(plan, frames)
    report = inspect_video(plan, narration, media, reviews)
    print(f"VIDEO={VIDEO_PATH.relative_to(ROOT).as_posix()}")
    print(f"NARRATION={NARRATION_PATH.relative_to(ROOT).as_posix()}")
    print(f"SUBTITLES={SRT_PATH.relative_to(ROOT).as_posix()}")
    print(f"DURATION_SECONDS={report['duration_seconds']:.3f}")
    print(f"RESOLUTION={report['width']}x{report['height']}")
    print(f"VIDEO_CODEC={report['video_codec']}")
    print(f"AUDIO_CODEC={report['audio_codec']}")
    print(f"AUDIO_PRESENT={str(report['audio_present']).lower()}")
    print(f"PRIVACY_AUDIT={report['privacy_audit']}")
    print("PROVIDER_CALLS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
