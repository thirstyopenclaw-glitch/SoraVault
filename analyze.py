#!/usr/bin/env python3
"""
Sora Prompt Analyzer
Mine prompts for patterns, top-performing templates, and reusable structures.
"""
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

DB_PATH = Path(__file__).parent / "sora.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def analyze_cameo_performance() -> None:
    conn = get_db()
    rows = conn.execute("""
        SELECT cameos, AVG(like_count) as avg_likes, AVG(view_count) as avg_views,
               COUNT(*) as count, SUM(like_count) as total_likes
        FROM videos
        WHERE cameos != '[]'
        GROUP BY cameos
        HAVING count >= 5
        ORDER BY avg_likes DESC
    """).fetchall()

    print("\n=== Cameo Performance ===")
    print(f"{'Cameos':<55} {'Count':>5} {'Avg♥':>7} {'Avg👁':>8} {'Total♥':>7}")
    print("-" * 90)
    for r in rows[:20]:
        names = ", ".join(json.loads(r["cameos"]))[:53]
        print(f"{names:<55} {r['count']:>5} {r['avg_likes']:>7.1f} {r['avg_views']:>8.1f} {r['total_likes']:>7}")
    conn.close()


def analyze_prompt_patterns() -> None:
    conn = get_db()
    rows = conn.execute("""
        SELECT prompt, caption, like_count, view_count
        FROM videos WHERE prompt != '' OR caption != ''
    """).fetchall()

    # Pattern categories
    patterns = {
        "ad/promo ending": re.compile(r"(last \d+ seconds|transition.*ad|advertisement)", re.I),
        "@mention cameo": re.compile(r"@\w+"),
        "riverwalk/downtown": re.compile(r"(riverwalk|downtown|san antonio)", re.I),
        "yard cup": re.compile(r"(yard.?cup|@yardcup)", re.I),
        "margarita": re.compile(r"margarita", re.I),
        "cinematic/dramatic": re.compile(r"(cinematic|dramatic|epic|hyper.?real)", re.I),
        "POV style": re.compile(r"(pov|first.?person|point of view)", re.I),
        "action/motion": re.compile(r"(running|dancing|walking|riding|jumping)", re.I),
        "animal": re.compile(r"(duck|whale|cat|kitty|shark|dog|creature)", re.I),
        "refill deal": re.compile(r"(refill|half off)", re.I),
        "thirsty aztec brand": re.compile(r"(thirsty aztec|@thirsty)", re.I),
    }

    pattern_stats: dict[str, dict] = {k: {"count": 0, "likes": 0, "views": 0} for k in patterns}

    for row in rows:
        text = f"{row['prompt']} {row['caption']}"
        for name, pat in patterns.items():
            if pat.search(text):
                pattern_stats[name]["count"] += 1
                pattern_stats[name]["likes"] += (row["like_count"] or 0)
                pattern_stats[name]["views"] += (row["view_count"] or 0)

    print("\n=== Prompt Pattern Analysis ===")
    print(f"{'Pattern':<25} {'Count':>6} {'Avg♥':>8} {'Avg👁':>9} {'Total♥':>8}")
    print("-" * 60)
    for name, stats in sorted(pattern_stats.items(), key=lambda x: -x[1]["likes"] / max(x[1]["count"], 1)):
        if stats["count"] > 0:
            avg_l = stats["likes"] / stats["count"]
            avg_v = stats["views"] / stats["count"]
            print(f"{name:<25} {stats['count']:>6} {avg_l:>8.1f} {avg_v:>9.1f} {stats['likes']:>8}")


def analyze_prompt_length() -> None:
    conn = get_db()
    rows = conn.execute("""
        SELECT
            CASE
                WHEN LENGTH(prompt) < 50 THEN 'short (<50)'
                WHEN LENGTH(prompt) < 150 THEN 'medium (50-150)'
                WHEN LENGTH(prompt) < 400 THEN 'long (150-400)'
                ELSE 'very long (400+)'
            END as bucket,
            COUNT(*) as count,
            AVG(like_count) as avg_likes,
            AVG(view_count) as avg_views
        FROM videos
        WHERE prompt != ''
        GROUP BY bucket
        ORDER BY avg_likes DESC
    """).fetchall()

    print("\n=== Prompt Length vs Performance ===")
    print(f"{'Length':<20} {'Count':>6} {'Avg♥':>8} {'Avg👁':>9}")
    print("-" * 45)
    for r in rows:
        print(f"{r['bucket']:<20} {r['count']:>6} {r['avg_likes']:>8.1f} {r['avg_views']:>9.1f}")
    conn.close()


def analyze_generation_type() -> None:
    conn = get_db()
    rows = conn.execute("""
        SELECT generation_type,
               COUNT(*) as count,
               AVG(like_count) as avg_likes,
               AVG(view_count) as avg_views,
               AVG(duration_s) as avg_dur
        FROM videos
        GROUP BY generation_type
        ORDER BY avg_likes DESC
    """).fetchall()

    print("\n=== Generation Type Performance ===")
    print(f"{'Type':<20} {'Count':>6} {'Avg♥':>8} {'Avg👁':>9} {'Avg Dur':>8}")
    print("-" * 55)
    for r in rows:
        print(f"{(r['generation_type'] or 'unknown'):<20} {r['count']:>6} {r['avg_likes']:>8.1f} {r['avg_views']:>9.1f} {(r['avg_dur'] or 0):>7.1f}s")
    conn.close()


def top_prompt_templates() -> None:
    """Find the most reusable prompt structures."""
    conn = get_db()
    rows = conn.execute("""
        SELECT id, prompt, caption, like_count, view_count
        FROM videos
        WHERE like_count >= 20 AND prompt != ''
        ORDER BY like_count DESC
        LIMIT 30
    """).fetchall()

    print("\n=== Top Performing Prompt Templates ===")
    print("(prompts with 20+ likes, ranked by engagement)\n")
    for i, r in enumerate(rows, 1):
        text = r["prompt"][:120] if r["prompt"] else r["caption"][:120]
        print(f"  {i:>2}. [{r['like_count']:>3}♥ {r['view_count']:>5}👁] {text}")
    conn.close()


def duration_analysis() -> None:
    conn = get_db()
    rows = conn.execute("""
        SELECT
            CASE
                WHEN duration_s <= 10 THEN '≤10s'
                WHEN duration_s <= 15 THEN '11-15s'
                WHEN duration_s <= 20 THEN '16-20s'
                ELSE '20s+'
            END as bucket,
            COUNT(*) as count,
            AVG(like_count) as avg_likes,
            AVG(view_count) as avg_views
        FROM videos
        WHERE duration_s > 0
        GROUP BY bucket
        ORDER BY avg_likes DESC
    """).fetchall()

    print("\n=== Duration vs Performance ===")
    print(f"{'Duration':<12} {'Count':>6} {'Avg♥':>8} {'Avg👁':>9}")
    print("-" * 38)
    for r in rows:
        print(f"{r['bucket']:<12} {r['count']:>6} {r['avg_likes']:>8.1f} {r['avg_views']:>9.1f}")
    conn.close()


def main() -> None:
    print("Sora Prompt Analysis Report")
    print("=" * 50)
    analyze_generation_type()
    analyze_cameo_performance()
    analyze_prompt_patterns()
    analyze_prompt_length()
    duration_analysis()
    top_prompt_templates()
    print("\n" + "=" * 50)
    print("Done.")


if __name__ == "__main__":
    main()
