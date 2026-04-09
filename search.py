#!/usr/bin/env python3
"""
Sora Library Search CLI
Full-text search + filters over the indexed Sora video library.
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "sora.db"


def get_conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print("Database not found. Run indexer.py first.")
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def search(query: str, source: str | None = None, min_likes: int = 0,
           has_video: bool = False, limit: int = 20, sort: str = "relevance") -> None:
    conn = get_conn()

    if query:
        # FTS search
        sql = """
            SELECT v.*, rank
            FROM videos_fts fts
            JOIN videos v ON v.id = fts.id
            WHERE videos_fts MATCH ?
        """
        params: list = [query]
    else:
        sql = "SELECT *, 0 as rank FROM videos WHERE 1=1"
        params = []

    if source:
        sql += " AND v.source = ?" if query else " AND source = ?"
        params.append(source)
    if min_likes > 0:
        sql += " AND v.like_count >= ?" if query else " AND like_count >= ?"
        params.append(min_likes)
    if has_video:
        sql += " AND v.has_video = 1" if query else " AND has_video = 1"

    order = {
        "relevance": "rank" if query else "like_count DESC",
        "likes": "v.like_count DESC" if query else "like_count DESC",
        "views": "v.view_count DESC" if query else "view_count DESC",
        "date": "v.posted_at DESC" if query else "posted_at DESC",
        "duration": "v.duration_s DESC" if query else "duration_s DESC",
    }.get(sort, "rank" if query else "like_count DESC")

    sql += f" ORDER BY {order} LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()

    if not rows:
        print("No results found.")
        return

    print(f"\n{'ID':<45} {'Likes':>5} {'Views':>6} {'Dur':>5} {'Source':<10} Caption/Prompt")
    print("-" * 120)

    for row in rows:
        vid_id = row["id"][:43]
        likes = row["like_count"] or 0
        views = row["view_count"] or 0
        dur = f"{row['duration_s']:.0f}s" if row["duration_s"] else "?"
        source_name = (row["source"] or "?")[:8]
        text = (row["caption"] or row["prompt"] or "")[:50]
        has_mp4 = "V" if row["has_video"] else " "
        print(f"[{has_mp4}] {vid_id:<42} {likes:>5} {views:>6} {dur:>5} {source_name:<10} {text}")

    print(f"\n{len(rows)} results")
    conn.close()


def stats() -> None:
    conn = get_conn()

    total = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    with_video = conn.execute("SELECT COUNT(*) FROM videos WHERE has_video = 1").fetchone()[0]
    feed = conn.execute("SELECT COUNT(*) FROM videos WHERE source = 'v2_feed'").fetchone()[0]
    draft = conn.execute("SELECT COUNT(*) FROM videos WHERE source = 'v2_draft'").fetchone()[0]
    total_likes = conn.execute("SELECT SUM(like_count) FROM videos").fetchone()[0] or 0
    total_views = conn.execute("SELECT SUM(view_count) FROM videos").fetchone()[0] or 0
    total_size = conn.execute("SELECT SUM(file_size_bytes) FROM videos").fetchone()[0] or 0
    avg_dur = conn.execute("SELECT AVG(duration_s) FROM videos WHERE duration_s > 0").fetchone()[0] or 0

    gen_types = conn.execute("""
        SELECT generation_type, COUNT(*) as cnt
        FROM videos GROUP BY generation_type ORDER BY cnt DESC
    """).fetchall()

    print(f"\nSora Library Stats")
    print(f"{'=' * 40}")
    print(f"Total videos:     {total}")
    print(f"  Feed:           {feed}")
    print(f"  Draft:          {draft}")
    print(f"  With file:      {with_video}")
    print(f"Total likes:      {total_likes:,}")
    print(f"Total views:      {total_views:,}")
    print(f"Total size:       {total_size / 1e9:.1f} GB")
    print(f"Avg duration:     {avg_dur:.1f}s")
    print(f"\nGeneration types:")
    for row in gen_types:
        print(f"  {row['generation_type'] or 'unknown':<20} {row['cnt']:>5}")

    # Top cameos
    print(f"\nTop cameos:")
    all_cameos: dict[str, int] = {}
    for row in conn.execute("SELECT cameos FROM videos WHERE cameos != '[]'"):
        for name in json.loads(row["cameos"]):
            if name:
                all_cameos[name] = all_cameos.get(name, 0) + 1
    for name, count in sorted(all_cameos.items(), key=lambda x: -x[1])[:10]:
        print(f"  {name:<45} {count:>5}")

    conn.close()


def top(n: int = 20, by: str = "likes") -> None:
    conn = get_conn()
    col = "like_count" if by == "likes" else "view_count"
    rows = conn.execute(f"""
        SELECT id, caption, prompt, like_count, view_count, duration_s, permalink
        FROM videos ORDER BY {col} DESC LIMIT ?
    """, (n,)).fetchall()

    print(f"\nTop {n} by {by}:")
    print(f"{'#':>3} {'Likes':>6} {'Views':>7} {'Dur':>5}  Caption")
    print("-" * 90)
    for i, row in enumerate(rows, 1):
        text = (row["caption"] or row["prompt"] or "")[:55]
        dur = f"{row['duration_s']:.0f}s" if row["duration_s"] else "?"
        print(f"{i:>3} {row['like_count']:>6} {row['view_count']:>7} {dur:>5}  {text}")

    conn.close()


def play(video_id: str) -> None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if not row:
        # Try partial match
        row = conn.execute("SELECT * FROM videos WHERE id LIKE ?", (f"%{video_id}%",)).fetchone()

    if not row:
        print(f"Video not found: {video_id}")
        return

    print(f"\nID:       {row['id']}")
    print(f"Source:   {row['source']}")
    print(f"Likes:    {row['like_count']}  Views: {row['view_count']}")
    print(f"Duration: {row['duration_s']}s  ({row['width']}x{row['height']})")
    print(f"Type:     {row['generation_type']}")
    print(f"Cameos:   {row['cameos']}")
    print(f"Link:     {row['permalink']}")
    print(f"\nPrompt:\n{row['prompt']}")
    print(f"\nCaption:\n{row['caption']}")

    if row["video_path"] and Path(row["video_path"]).exists():
        print(f"\nOpening video...")
        subprocess.run(["open", row["video_path"]])
    else:
        print(f"\nNo local video file.")

    conn.close()


def keywords(n: int = 30) -> None:
    conn = get_conn()
    rows = conn.execute("""
        SELECT keyword, count, avg_likes, avg_views, top_video_id
        FROM prompt_analysis
        ORDER BY avg_likes DESC LIMIT ?
    """, (n,)).fetchall()

    print(f"\nTop {n} keywords by avg engagement:")
    print(f"{'Keyword':<25} {'Count':>5} {'Avg Likes':>10} {'Avg Views':>10}")
    print("-" * 55)
    for row in rows:
        print(f"{row['keyword']:<25} {row['count']:>5} {row['avg_likes']:>10.1f} {row['avg_views']:>10.1f}")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sora Library Search")
    sub = parser.add_subparsers(dest="command")

    # search
    sp = sub.add_parser("find", help="Full-text search")
    sp.add_argument("query", nargs="?", default="")
    sp.add_argument("--source", choices=["v2_feed", "v2_draft"])
    sp.add_argument("--min-likes", type=int, default=0)
    sp.add_argument("--video-only", action="store_true")
    sp.add_argument("--limit", "-n", type=int, default=20)
    sp.add_argument("--sort", choices=["relevance", "likes", "views", "date", "duration"], default="relevance")

    # stats
    sub.add_parser("stats", help="Library statistics")

    # top
    sp = sub.add_parser("top", help="Top videos")
    sp.add_argument("-n", type=int, default=20)
    sp.add_argument("--by", choices=["likes", "views"], default="likes")

    # play
    sp = sub.add_parser("play", help="View video details + open")
    sp.add_argument("id", help="Video ID (partial match ok)")

    # keywords
    sp = sub.add_parser("keywords", help="Top prompt keywords by engagement")
    sp.add_argument("-n", type=int, default=30)

    args = parser.parse_args()

    if args.command == "find":
        search(args.query, args.source, args.min_likes, args.video_only, args.limit, args.sort)
    elif args.command == "stats":
        stats()
    elif args.command == "top":
        top(args.n, args.by)
    elif args.command == "play":
        play(args.id)
    elif args.command == "keywords":
        keywords(args.n)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
