#!/usr/bin/env python3
"""
Sora Best-Of Compilation Tool
Create highlight reels from starred or top-performing videos.
"""
import argparse
import json
import sqlite3
import subprocess
import tempfile
from pathlib import Path

DB_PATH = Path(__file__).parent / "sora.db"
OUTPUT_DIR = Path(__file__).parent / "reels"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_videos(mode: str, limit: int, min_likes: int) -> list[dict]:
    conn = get_db()

    if mode == "starred":
        rows = conn.execute("""
            SELECT id, video_path, caption, prompt, like_count, duration_s
            FROM videos WHERE starred = 1 AND has_video = 1
            ORDER BY like_count DESC LIMIT ?
        """, (limit,)).fetchall()
    elif mode == "top":
        rows = conn.execute("""
            SELECT id, video_path, caption, prompt, like_count, duration_s
            FROM videos WHERE has_video = 1 AND like_count >= ?
            ORDER BY like_count DESC LIMIT ?
        """, (min_likes, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT id, video_path, caption, prompt, like_count, duration_s
            FROM videos WHERE has_video = 1
            ORDER BY like_count DESC LIMIT ?
        """, (limit,)).fetchall()

    conn.close()
    return [dict(r) for r in rows if r["video_path"] and Path(r["video_path"]).exists()]


def compile_reel(videos: list[dict], output_name: str, max_duration: float = 60.0) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    output_path = OUTPUT_DIR / f"{output_name}.mp4"

    if not videos:
        print("No videos to compile.")
        return

    # Build ffmpeg concat file
    total_dur = 0.0
    selected = []
    for v in videos:
        dur = v["duration_s"] or 10.0
        if total_dur + dur > max_duration and selected:
            break
        selected.append(v)
        total_dur += dur

    print(f"Compiling {len(selected)} clips ({total_dur:.0f}s total)")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for v in selected:
            # Escape single quotes in path for ffmpeg
            safe_path = v["video_path"].replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")
        concat_file = f.name

    # Get target resolution from first video
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    print(f"Running ffmpeg...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr[-500:]}")
        return

    size = output_path.stat().st_size / 1e6
    print(f"\nReel saved: {output_path}")
    print(f"Size: {size:.1f} MB")
    print(f"Clips: {len(selected)}")

    # Print clip list
    for i, v in enumerate(selected, 1):
        text = (v["caption"] or v["prompt"] or "")[:50]
        print(f"  {i}. [{v['like_count']}♥] {text}")

    Path(concat_file).unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile Sora highlight reels")
    parser.add_argument("--mode", choices=["starred", "top", "all"], default="top")
    parser.add_argument("--name", default="highlight_reel", help="Output filename")
    parser.add_argument("--limit", "-n", type=int, default=10, help="Max clips")
    parser.add_argument("--min-likes", type=int, default=50, help="Min likes for 'top' mode")
    parser.add_argument("--max-duration", type=float, default=60.0, help="Max total duration in seconds")
    parser.add_argument("--list", action="store_true", help="Just list clips, don't compile")

    args = parser.parse_args()
    videos = get_videos(args.mode, args.limit, args.min_likes)

    if args.list:
        print(f"\n{len(videos)} clips ({args.mode} mode):")
        for i, v in enumerate(videos, 1):
            text = (v["caption"] or v["prompt"] or "")[:60]
            print(f"  {i}. [{v['like_count']:>3}♥ {v['duration_s'] or 0:.0f}s] {text}")
        return

    compile_reel(videos, args.name, args.max_duration)


if __name__ == "__main__":
    main()
