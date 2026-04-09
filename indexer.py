#!/usr/bin/env python3
"""
Sora Library Indexer
Parses manifest.json + prompt .md files into a searchable SQLite database.
"""
import json
import sqlite3
import os
import re
from pathlib import Path
from datetime import datetime, timezone

SORA_BACKUP = Path(os.environ.get("SORA_BACKUP_DIR", os.path.expanduser("~/Desktop/SoraBackup")))
PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "sora.db"


def create_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS videos (
            id TEXT PRIMARY KEY,
            source TEXT,              -- v2_feed or v2_draft
            prompt TEXT,              -- from .md file
            caption TEXT,             -- from manifest (post text)
            generation_type TEXT,     -- video_gen, editor_stitch, etc
            generation_id TEXT,
            width INTEGER,
            height INTEGER,
            duration_s REAL,
            has_video INTEGER,        -- 1 if .mp4 exists locally
            has_watermark_clean INTEGER DEFAULT 0,
            video_path TEXT,
            prompt_path TEXT,
            permalink TEXT,
            posted_at REAL,
            posted_at_human TEXT,
            like_count INTEGER DEFAULT 0,
            view_count INTEGER DEFAULT 0,
            share_count INTEGER DEFAULT 0,
            remix_count INTEGER DEFAULT 0,
            cameos TEXT,              -- JSON array of cameo names
            tags TEXT,                -- JSON array
            topic_labels TEXT,        -- JSON array
            file_size_bytes INTEGER,
            starred INTEGER DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(
            id, prompt, caption, cameos, tags,
            content='videos',
            content_rowid='rowid'
        );

        CREATE TRIGGER IF NOT EXISTS videos_ai AFTER INSERT ON videos BEGIN
            INSERT INTO videos_fts(rowid, id, prompt, caption, cameos, tags)
            VALUES (new.rowid, new.id, new.prompt, new.caption, new.cameos, new.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS videos_ad AFTER DELETE ON videos BEGIN
            INSERT INTO videos_fts(videos_fts, rowid, id, prompt, caption, cameos, tags)
            VALUES ('delete', old.rowid, old.id, old.prompt, old.caption, old.cameos, old.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS videos_au AFTER UPDATE ON videos BEGIN
            INSERT INTO videos_fts(videos_fts, rowid, id, prompt, caption, cameos, tags)
            VALUES ('delete', old.rowid, old.id, old.prompt, old.caption, old.cameos, old.tags);
            INSERT INTO videos_fts(rowid, id, prompt, caption, cameos, tags)
            VALUES (new.rowid, new.id, new.prompt, new.caption, new.cameos, new.tags);
        END;

        CREATE TABLE IF NOT EXISTS prompt_analysis (
            keyword TEXT PRIMARY KEY,
            count INTEGER,
            avg_likes REAL,
            avg_views REAL,
            top_video_id TEXT
        );
    """)


def parse_prompt_md(path: Path) -> dict:
    """Parse a prompt .md file with YAML-like frontmatter."""
    text = path.read_text(errors="replace")
    result = {"id": None, "source": None, "type": None, "created_at": None, "prompt": ""}

    # Split frontmatter
    parts = text.split("---")
    if len(parts) >= 3:
        frontmatter = parts[1]
        body = "---".join(parts[2:]).strip()
        for line in frontmatter.strip().splitlines():
            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip()
                val = val.strip()
                if key in result:
                    result[key] = val if val and val != "None" else None
        result["prompt"] = body
    return result


def index_local_files(conn: sqlite3.Connection) -> dict:
    """Index all .md prompt files from v2_feed and v2_draft."""
    prompts = {}  # id -> {prompt, source, video_path, prompt_path}

    for source_dir in ["v2_feed", "v2_draft"]:
        prompt_dir = SORA_BACKUP / source_dir / "prompts"
        video_dir = SORA_BACKUP / source_dir / "videos"

        if not prompt_dir.exists():
            continue

        for md_file in prompt_dir.iterdir():
            if not md_file.name.endswith(".md"):
                continue

            parsed = parse_prompt_md(md_file)
            file_id = parsed["id"] or md_file.stem
            video_file = video_dir / f"{file_id}.mp4"

            prompts[file_id] = {
                "prompt": parsed["prompt"],
                "source": source_dir,
                "has_video": video_file.exists(),
                "video_path": str(video_file) if video_file.exists() else None,
                "prompt_path": str(md_file),
                "file_size_bytes": video_file.stat().st_size if video_file.exists() else 0,
            }

    # Also check prompt_onlys
    prompt_only_dir = SORA_BACKUP / "v2_draft" / "prompt_onlys"
    if prompt_only_dir.exists():
        for md_file in prompt_only_dir.iterdir():
            if md_file.name.endswith(".md"):
                parsed = parse_prompt_md(md_file)
                file_id = parsed["id"] or md_file.stem
                if file_id not in prompts:
                    prompts[file_id] = {
                        "prompt": parsed["prompt"],
                        "source": "v2_draft_prompt_only",
                        "has_video": False,
                        "video_path": None,
                        "prompt_path": str(md_file),
                        "file_size_bytes": 0,
                    }

    # Index cameos folder — each subfolder has prompts/ and videos/
    cameos_dir = SORA_BACKUP / "cameos"
    if cameos_dir.exists():
        for cameo_dir in cameos_dir.iterdir():
            if not cameo_dir.is_dir() or cameo_dir.name.startswith("."):
                continue
            cameo_name = cameo_dir.name
            prompt_dir = cameo_dir / "prompts"
            video_dir = cameo_dir / "videos"

            if not prompt_dir.exists():
                continue

            for md_file in prompt_dir.iterdir():
                if not md_file.name.endswith(".md"):
                    continue

                parsed = parse_prompt_md(md_file)
                file_id = parsed["id"] or md_file.stem
                video_file = video_dir / f"{file_id}.mp4"

                if file_id not in prompts:
                    prompts[file_id] = {
                        "prompt": parsed["prompt"],
                        "source": f"cameo_{cameo_name}",
                        "has_video": video_file.exists(),
                        "video_path": str(video_file) if video_file.exists() else None,
                        "prompt_path": str(md_file),
                        "file_size_bytes": video_file.stat().st_size if video_file.exists() else 0,
                    }
                elif not prompts[file_id]["has_video"] and video_file.exists():
                    # Existing entry has no video but cameo dir does — update
                    prompts[file_id]["has_video"] = True
                    prompts[file_id]["video_path"] = str(video_file)
                    prompts[file_id]["file_size_bytes"] = video_file.stat().st_size

    print(f"  Found {len(prompts)} local prompt files")
    return prompts


def index_manifest(conn: sqlite3.Connection, local_prompts: dict) -> None:
    """Merge manifest.json data with local file data."""
    manifest_path = SORA_BACKUP / "manifest.json"
    if not manifest_path.exists():
        print("  No manifest.json found, using local files only")
        return

    with open(manifest_path) as f:
        manifest = json.load(f)

    print(f"  Manifest has {len(manifest)} items")

    rows = []
    seen_ids = set()

    for item in manifest:
        post = item.get("post")
        if not post:
            continue

        post_id = post.get("id", "")
        if not post_id or post_id in seen_ids:
            continue
        seen_ids.add(post_id)

        att = post["attachments"][0] if post.get("attachments") else {}
        cameo_profiles = post.get("cameo_profiles") or []
        cameo_names = [c.get("display_name") for c in cameo_profiles if c and c.get("display_name")]

        posted_at = post.get("posted_at")
        posted_at_human = None
        if posted_at:
            try:
                posted_at_human = datetime.fromtimestamp(posted_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            except (OSError, ValueError):
                pass

        # Merge with local file data
        local = local_prompts.pop(post_id, {})

        # Determine source from local data or infer
        source = local.get("source", "v2_feed")

        rows.append((
            post_id,
            source,
            local.get("prompt", ""),
            post.get("text", ""),
            att.get("generation_type"),
            att.get("generation_id"),
            att.get("width"),
            att.get("height"),
            att.get("duration_s"),
            1 if local.get("has_video") else 0,
            0,  # has_watermark_clean
            local.get("video_path"),
            local.get("prompt_path"),
            post.get("permalink"),
            posted_at,
            posted_at_human,
            post.get("like_count", 0),
            post.get("view_count", 0),
            post.get("share_count", 0),
            post.get("remix_count", 0),
            json.dumps(cameo_names),
            json.dumps(att.get("tags", [])),
            json.dumps(post.get("topic_labels", [])),
            local.get("file_size_bytes", 0),
        ))

    # Add remaining local-only prompts (not in manifest)
    for file_id, local in local_prompts.items():
        rows.append((
            file_id,
            local.get("source", "unknown"),
            local.get("prompt", ""),
            "",  # no caption from manifest
            None, None, None, None, None,
            1 if local.get("has_video") else 0,
            0,
            local.get("video_path"),
            local.get("prompt_path"),
            None, None, None,
            0, 0, 0, 0,
            "[]", "[]", "[]",
            local.get("file_size_bytes", 0),
        ))

    conn.executemany("""
        INSERT OR REPLACE INTO videos (
            id, source, prompt, caption, generation_type, generation_id,
            width, height, duration_s, has_video, has_watermark_clean,
            video_path, prompt_path, permalink, posted_at, posted_at_human,
            like_count, view_count, share_count, remix_count,
            cameos, tags, topic_labels, file_size_bytes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    print(f"  Indexed {len(rows)} total videos")


def build_prompt_analysis(conn: sqlite3.Connection) -> None:
    """Analyze prompt keywords and their engagement correlation."""
    conn.execute("DELETE FROM prompt_analysis")

    rows = conn.execute("""
        SELECT id, prompt, caption, like_count, view_count
        FROM videos WHERE prompt != '' OR caption != ''
    """).fetchall()

    keyword_stats: dict[str, dict] = {}
    stop_words = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "it", "this", "that", "are", "was",
        "be", "has", "had", "have", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "can", "into", "as", "her", "his",
        "she", "he", "they", "them", "their", "its", "i", "you", "we", "my",
        "your", "our", "not", "no", "so", "if", "then", "than", "just", "also",
        "very", "too", "more", "most", "all", "each", "every", "any", "some",
        "one", "two", "three", "four", "five", "last", "first", "new", "same",
    }

    for vid_id, prompt, caption, likes, views in rows:
        text = f"{prompt} {caption}".lower()
        words = re.findall(r"@?\w+", text)
        unique_words = set(w for w in words if len(w) > 2 and w not in stop_words)

        for word in unique_words:
            if word not in keyword_stats:
                keyword_stats[word] = {"count": 0, "total_likes": 0, "total_views": 0, "best_id": None, "best_likes": 0}
            stats = keyword_stats[word]
            stats["count"] += 1
            stats["total_likes"] += (likes or 0)
            stats["total_views"] += (views or 0)
            if (likes or 0) > stats["best_likes"]:
                stats["best_likes"] = likes or 0
                stats["best_id"] = vid_id

    analysis_rows = []
    for keyword, stats in keyword_stats.items():
        if stats["count"] >= 3:  # only keywords used 3+ times
            analysis_rows.append((
                keyword,
                stats["count"],
                stats["total_likes"] / stats["count"],
                stats["total_views"] / stats["count"],
                stats["best_id"],
            ))

    conn.executemany(
        "INSERT INTO prompt_analysis VALUES (?, ?, ?, ?, ?)",
        analysis_rows,
    )
    conn.commit()
    print(f"  Analyzed {len(analysis_rows)} keywords (3+ uses)")


def main() -> None:
    print("Sora Library Indexer")
    print("=" * 40)

    if DB_PATH.exists():
        DB_PATH.unlink()
        print("  Cleared existing database")

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    print("\n1. Creating schema...")
    create_db(conn)

    print("\n2. Scanning local files...")
    local_prompts = index_local_files(conn)

    print("\n3. Merging manifest data...")
    index_manifest(conn, local_prompts)

    print("\n4. Building prompt analysis...")
    build_prompt_analysis(conn)

    # Summary stats
    total = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    with_video = conn.execute("SELECT COUNT(*) FROM videos WHERE has_video = 1").fetchone()[0]
    with_prompt = conn.execute("SELECT COUNT(*) FROM videos WHERE prompt != ''").fetchone()[0]
    total_size = conn.execute("SELECT SUM(file_size_bytes) FROM videos").fetchone()[0] or 0
    top_liked = conn.execute(
        "SELECT id, caption, like_count FROM videos ORDER BY like_count DESC LIMIT 5"
    ).fetchall()

    print(f"\n{'=' * 40}")
    print(f"Total indexed:    {total}")
    print(f"With video file:  {with_video}")
    print(f"With prompt text: {with_prompt}")
    print(f"Total video size: {total_size / 1e9:.1f} GB")
    print(f"\nTop 5 by likes:")
    for vid_id, caption, likes in top_liked:
        print(f"  {likes:>5} likes | {caption[:60]}")

    conn.close()
    print(f"\nDatabase: {DB_PATH}")


if __name__ == "__main__":
    main()
