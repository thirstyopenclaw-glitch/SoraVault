#!/usr/bin/env python3
"""
Sora Vision Analysis Pipeline
Feeds videos to Claude Vision (Haiku) for scene description + Whisper for audio.
Supports pause/resume via SIGINT (Ctrl+C) — saves progress after each video.
"""
import anthropic
import base64
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "sora.db"
ANALYSIS_DIR = PROJECT_DIR / "analysis"
PROGRESS_FILE = PROJECT_DIR / "vision_progress.json"
LOG_FILE = PROJECT_DIR / "vision_pipeline.log"

# Config
FRAMES_PER_VIDEO = 5
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 500
BATCH_SIZE = 10  # save progress every N videos
RATE_LIMIT_DELAY = 0.5  # seconds between API calls

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE)),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# Graceful shutdown
shutdown_requested = False


def handle_signal(sig, frame):
    global shutdown_requested
    if shutdown_requested:
        log.warning("Force quit — progress saved up to last batch")
        sys.exit(1)
    log.info("Shutdown requested — finishing current video then saving progress...")
    shutdown_requested = True


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGHUP, signal.SIG_IGN)  # ignore hangup when terminal closes


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text())
    return {"completed": [], "failed": [], "started_at": None, "last_run": None}


def save_progress(progress: dict) -> None:
    progress["last_run"] = datetime.now(ZoneInfo("America/Chicago")).isoformat()
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


def extract_frames(video_path: str, n_frames: int = 5) -> list[str]:
    """Extract N evenly-spaced frames from a video, return as base64 JPEG."""
    frames = []
    with tempfile.TemporaryDirectory() as tmpdir:
        # Get duration
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
            capture_output=True, text=True,
        )
        try:
            duration = float(json.loads(probe.stdout)["format"]["duration"])
        except (KeyError, json.JSONDecodeError):
            duration = 10.0

        # Extract frames at even intervals
        interval = duration / (n_frames + 1)
        for i in range(n_frames):
            timestamp = interval * (i + 1)
            output = os.path.join(tmpdir, f"frame_{i}.jpg")
            subprocess.run(
                ["ffmpeg", "-ss", str(timestamp), "-i", video_path,
                 "-vframes", "1", "-q:v", "3", "-y", output],
                capture_output=True,
            )
            if os.path.exists(output):
                with open(output, "rb") as f:
                    frames.append(base64.standard_b64encode(f.read()).decode())
    return frames


def extract_audio_text(video_path: str) -> str:
    """Transcribe audio using Whisper (local)."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name

        # Extract audio
        subprocess.run(
            ["ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
             "-ar", "16000", "-ac", "1", "-y", wav_path],
            capture_output=True, timeout=30,
        )

        if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1000:
            return ""

        # Try whisper CLI
        result = subprocess.run(
            ["whisper", wav_path, "--model", "base", "--output_format", "txt",
             "--output_dir", tempfile.gettempdir(), "--language", "en"],
            capture_output=True, text=True, timeout=120,
        )

        txt_path = wav_path.replace(".wav", ".txt")
        if os.path.exists(txt_path):
            text = Path(txt_path).read_text().strip()
            os.unlink(txt_path)
            return text

        return ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    finally:
        if os.path.exists(wav_path):
            os.unlink(wav_path)


def analyze_video(client: anthropic.Anthropic, video_id: str, video_path: str,
                  prompt_text: str, caption: str) -> dict:
    """Send frames to Claude Vision for analysis."""
    frames = extract_frames(video_path, FRAMES_PER_VIDEO)
    if not frames:
        return {"error": "no frames extracted"}

    # Build message with images
    content = []
    content.append({
        "type": "text",
        "text": f"Analyze this Sora AI-generated video (shown as {len(frames)} sequential frames). "
                f"Original prompt: \"{prompt_text or caption or 'unknown'}\"\n\n"
                "Respond in JSON with these fields:\n"
                "- scene: one-line scene description\n"
                "- action: what's happening/moving\n"
                "- text_on_screen: any visible text (brand names, captions, etc)\n"
                "- mood: overall vibe/mood in 2-3 words\n"
                "- subjects: list of people/characters/objects featured\n"
                "- setting: location/environment\n"
                "- quality: rate 1-10 how good the generation looks\n"
                "- social_ready: true/false if this is good enough to post on social media as-is\n"
                "- best_platform: which platform this fits best (ig_reels, tiktok, x, all)\n"
                "- suggested_caption: a short engaging caption for posting",
    })

    for i, frame_b64 in enumerate(frames):
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": frame_b64,
            },
        })

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": content}],
    )

    text = response.content[0].text
    # Try to parse JSON from response
    try:
        # Find JSON in response
        if "{" in text:
            json_str = text[text.index("{"):text.rindex("}") + 1]
            return json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        pass

    return {"raw_response": text}


def ensure_analysis_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS video_analysis (
            video_id TEXT PRIMARY KEY,
            scene TEXT,
            action TEXT,
            text_on_screen TEXT,
            mood TEXT,
            subjects TEXT,
            setting TEXT,
            quality INTEGER,
            social_ready INTEGER,
            best_platform TEXT,
            suggested_caption TEXT,
            audio_transcript TEXT,
            raw_response TEXT,
            analyzed_at TEXT,
            FOREIGN KEY (video_id) REFERENCES videos(id)
        )
    """)
    conn.commit()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Sora Vision Analysis Pipeline")
    parser.add_argument("--limit", "-n", type=int, default=0, help="Max videos to process (0=all)")
    parser.add_argument("--skip-whisper", action="store_true", help="Skip audio transcription")
    parser.add_argument("--min-likes", type=int, default=0, help="Only analyze videos with N+ likes")
    parser.add_argument("--reset", action="store_true", help="Reset progress and start over")
    parser.add_argument("--status", action="store_true", help="Show pipeline status")
    args = parser.parse_args()

    ANALYSIS_DIR.mkdir(exist_ok=True)

    progress = load_progress()

    if args.status:
        done = len(progress["completed"])
        failed = len(progress["failed"])
        print(f"Completed:  {done}")
        print(f"Failed:     {failed}")
        print(f"Last run:   {progress.get('last_run', 'never')}")
        print(f"Started at: {progress.get('started_at', 'never')}")

        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM videos WHERE has_video = 1").fetchone()[0]
        analyzed = conn.execute("SELECT COUNT(*) FROM video_analysis").fetchone()[0] if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='video_analysis'"
        ).fetchone() else 0
        conn.close()
        print(f"Total videos: {total}")
        print(f"In DB:        {analyzed}")
        print(f"Remaining:    {total - done}")
        return

    if args.reset:
        progress = {"completed": [], "failed": [], "started_at": None, "last_run": None}
        save_progress(progress)
        print("Progress reset.")
        return

    # Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Set ANTHROPIC_API_KEY environment variable")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    conn = get_db()
    ensure_analysis_table(conn)

    # Get videos to process
    completed_set = set(progress["completed"])
    failed_set = set(progress["failed"])

    query = "SELECT id, video_path, prompt, caption FROM videos WHERE has_video = 1"
    params: list = []
    if args.min_likes > 0:
        query += " AND like_count >= ?"
        params.append(args.min_likes)
    query += " ORDER BY like_count DESC"

    all_videos = conn.execute(query, params).fetchall()
    pending = [v for v in all_videos if v["id"] not in completed_set and v["id"] not in failed_set]

    if args.limit > 0:
        pending = pending[:args.limit]

    total = len(pending)
    log.info(f"Pipeline: {total} videos to process, {len(completed_set)} already done")

    if not progress["started_at"]:
        progress["started_at"] = datetime.now(ZoneInfo("America/Chicago")).isoformat()

    processed = 0
    batch_count = 0

    for i, video in enumerate(pending):
        if shutdown_requested:
            log.info(f"Stopping gracefully after {processed} videos this session")
            break

        video_id = video["id"]
        video_path = video["video_path"]

        if not Path(video_path).exists():
            progress["failed"].append(video_id)
            continue

        try:
            log.info(f"[{i+1}/{total}] Analyzing {video_id}...")

            # Vision analysis
            analysis = analyze_video(client, video_id, video_path,
                                     video["prompt"], video["caption"])

            # Audio transcription
            audio_text = ""
            if not args.skip_whisper:
                try:
                    audio_text = extract_audio_text(video_path)
                except Exception as e:
                    log.warning(f"  Whisper failed: {e}")

            # Save to DB
            conn.execute("""
                INSERT OR REPLACE INTO video_analysis
                (video_id, scene, action, text_on_screen, mood, subjects,
                 setting, quality, social_ready, best_platform, suggested_caption,
                 audio_transcript, raw_response, analyzed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                video_id,
                str(analysis.get("scene", "")),
                str(analysis.get("action", "")),
                str(analysis.get("text_on_screen", "")),
                str(analysis.get("mood", "")),
                json.dumps(analysis.get("subjects", [])) if isinstance(analysis.get("subjects"), list) else str(analysis.get("subjects", "[]")),
                str(analysis.get("setting", "")),
                analysis.get("quality"),
                1 if analysis.get("social_ready") else 0,
                analysis.get("best_platform", ""),
                analysis.get("suggested_caption", ""),
                audio_text,
                json.dumps(analysis) if "raw_response" in analysis else "",
                datetime.now(ZoneInfo("America/Chicago")).isoformat(),
            ))

            # Save individual JSON
            analysis_file = ANALYSIS_DIR / f"{video_id}.json"
            analysis["audio_transcript"] = audio_text
            analysis_file.write_text(json.dumps(analysis, indent=2))

            progress["completed"].append(video_id)
            processed += 1
            batch_count += 1

            # Save progress every BATCH_SIZE
            if batch_count >= BATCH_SIZE:
                conn.commit()
                save_progress(progress)
                batch_count = 0
                log.info(f"  Progress saved ({len(progress['completed'])} total)")

            time.sleep(RATE_LIMIT_DELAY)

        except anthropic.RateLimitError:
            log.warning("Rate limited — waiting 60s...")
            time.sleep(60)
        except Exception as e:
            log.error(f"  Error on {video_id}: {e}")
            progress["failed"].append(video_id)

    # Final save
    conn.commit()
    save_progress(progress)
    conn.close()

    log.info(f"\nSession complete: {processed} processed, {len(progress['completed'])} total done")
    log.info(f"Progress saved to {PROGRESS_FILE}")


if __name__ == "__main__":
    main()
