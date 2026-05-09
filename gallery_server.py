#!/usr/bin/env python3
"""
Sora Gallery Web Server
Browsable video gallery at sora.thirstyai.live
"""
import json
import sqlite3
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, request, jsonify, send_file, send_from_directory, abort
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = {"mp4", "mov", "webm", "mkv"}

PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "sora.db"
STATIC_DIR = PROJECT_DIR / "static"
SORA_BACKUP = Path(os.environ.get("SORA_BACKUP_DIR", os.path.expanduser("~/Desktop/SoraBackup")))

app = Flask(__name__, static_folder=str(STATIC_DIR))


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/")
def index():
    return send_file(str(STATIC_DIR / "index.html"))


@app.route("/api/videos")
def api_videos():
    conn = get_db()
    query = request.args.get("q", "")
    source = request.args.get("source", "")
    sort = request.args.get("sort", "likes")
    min_likes = int(request.args.get("min_likes", 0))
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 48))
    video_only = request.args.get("video_only", "true") == "true"
    starred = request.args.get("starred", "") == "true"
    offset = (page - 1) * per_page

    if query:
        base = """
            FROM videos_fts fts
            JOIN videos v ON v.id = fts.id
            WHERE videos_fts MATCH ?
        """
        params: list = [query]
        prefix = "v."
    else:
        base = "FROM videos v WHERE 1=1"
        params = []
        prefix = "v."

    if source:
        base += f" AND {prefix}source = ?"
        params.append(source)
    if min_likes > 0:
        base += f" AND {prefix}like_count >= ?"
        params.append(min_likes)
    if video_only:
        base += f" AND {prefix}has_video = 1"
    if starred:
        base += f" AND {prefix}starred = 1"

    # Count
    count_sql = f"SELECT COUNT(*) {base}"
    total = conn.execute(count_sql, params).fetchone()[0]

    # Sort
    order_map = {
        "likes": f"{prefix}like_count DESC",
        "views": f"{prefix}view_count DESC",
        "date": f"{prefix}posted_at DESC",
        "duration": f"{prefix}duration_s DESC",
        "relevance": "rank" if query else f"{prefix}like_count DESC",
    }
    order = order_map.get(sort, f"{prefix}like_count DESC")

    select_sql = f"""
        SELECT {prefix}id, {prefix}source, {prefix}prompt, {prefix}caption,
               {prefix}generation_type, {prefix}width, {prefix}height,
               {prefix}duration_s, {prefix}has_video, {prefix}like_count,
               {prefix}view_count, {prefix}share_count, {prefix}remix_count,
               {prefix}cameos, {prefix}permalink, {prefix}posted_at_human,
               {prefix}starred, {prefix}notes
        {base}
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    params.extend([per_page, offset])
    rows = conn.execute(select_sql, params).fetchall()

    videos = []
    for row in rows:
        videos.append({
            "id": row["id"],
            "source": row["source"],
            "prompt": row["prompt"],
            "caption": row["caption"],
            "generation_type": row["generation_type"],
            "width": row["width"],
            "height": row["height"],
            "duration_s": row["duration_s"],
            "has_video": bool(row["has_video"]),
            "like_count": row["like_count"],
            "view_count": row["view_count"],
            "share_count": row["share_count"],
            "remix_count": row["remix_count"],
            "cameos": json.loads(row["cameos"]) if row["cameos"] else [],
            "permalink": row["permalink"],
            "posted_at_human": row["posted_at_human"],
            "starred": bool(row["starred"]),
            "notes": row["notes"],
        })

    conn.close()
    return jsonify({
        "videos": videos,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    })


@app.route("/api/videos/<video_id>/star", methods=["POST"])
def star_video(video_id: str):
    conn = get_db()
    data = request.get_json() or {}
    starred = 1 if data.get("starred", True) else 0
    conn.execute("UPDATE videos SET starred = ? WHERE id = ?", (starred, video_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/videos/<video_id>/notes", methods=["POST"])
def update_notes(video_id: str):
    conn = get_db()
    data = request.get_json() or {}
    conn.execute("UPDATE videos SET notes = ? WHERE id = ?", (data.get("notes", ""), video_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/stats")
def api_stats():
    conn = get_db()
    stats = {
        "total": conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0],
        "with_video": conn.execute("SELECT COUNT(*) FROM videos WHERE has_video = 1").fetchone()[0],
        "feed": conn.execute("SELECT COUNT(*) FROM videos WHERE source = 'v2_feed'").fetchone()[0],
        "draft": conn.execute("SELECT COUNT(*) FROM videos WHERE source = 'v2_draft'").fetchone()[0],
        "total_likes": conn.execute("SELECT SUM(like_count) FROM videos").fetchone()[0] or 0,
        "total_views": conn.execute("SELECT SUM(view_count) FROM videos").fetchone()[0] or 0,
        "starred": conn.execute("SELECT COUNT(*) FROM videos WHERE starred = 1").fetchone()[0],
    }
    conn.close()
    return jsonify(stats)


@app.route("/api/cameos")
def api_cameos():
    conn = get_db()
    rows = conn.execute("SELECT cameos FROM videos WHERE cameos != '[]'").fetchall()
    cameo_stats: dict[str, dict] = {}
    for row in rows:
        for name in json.loads(row["cameos"]):
            if name:
                if name not in cameo_stats:
                    cameo_stats[name] = {"name": name, "count": 0}
                cameo_stats[name]["count"] += 1
    result = sorted(cameo_stats.values(), key=lambda x: -x["count"])
    conn.close()
    return jsonify(result)


@app.route("/api/sources")
def api_sources():
    conn = get_db()
    rows = conn.execute("""
        SELECT source, COUNT(*) as count
        FROM videos GROUP BY source ORDER BY count DESC
    """).fetchall()
    conn.close()
    return jsonify([{"source": r["source"], "count": r["count"]} for r in rows])


@app.route("/api/keywords")
def api_keywords():
    conn = get_db()
    n = int(request.args.get("n", 30))
    sort = request.args.get("sort", "avg_likes")
    rows = conn.execute(f"""
        SELECT keyword, count, avg_likes, avg_views
        FROM prompt_analysis
        ORDER BY {sort} DESC LIMIT ?
    """, (n,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/video/<video_id>")
def serve_video(video_id: str):
    """Serve video file by ID — checks v2_feed, v2_draft, and cameos."""
    # Check main dirs first
    for source_dir in ["v2_feed", "v2_draft"]:
        video_path = SORA_BACKUP / source_dir / "videos" / f"{video_id}.mp4"
        if video_path.exists():
            return send_file(str(video_path), mimetype="video/mp4", conditional=True)

    # Check cameos subdirs
    cameos_dir = SORA_BACKUP / "cameos"
    if cameos_dir.exists():
        for cameo_dir in cameos_dir.iterdir():
            if not cameo_dir.is_dir():
                continue
            video_path = cameo_dir / "videos" / f"{video_id}.mp4"
            if video_path.exists():
                return send_file(str(video_path), mimetype="video/mp4", conditional=True)

    abort(404)


@app.route("/upload-zone")
def upload_zone():
    return send_file(str(STATIC_DIR / "upload-zone.html"))


@app.route("/casino")
def casino():
    return send_file(str(STATIC_DIR / "casino.html"))


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "video" not in request.files:
        return jsonify({"error": "No video file provided"}), 400

    file = request.files["video"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"File type '{ext}' not allowed. Use mp4, mov, webm, or mkv."}), 400

    video_id = str(uuid.uuid4())
    prompt = request.form.get("prompt", "").strip()
    caption = request.form.get("caption", "").strip()
    width = int(request.form.get("width", 0)) or None
    height = int(request.form.get("height", 0)) or None

    dest_dir = SORA_BACKUP / "v2_draft" / "videos"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{video_id}.mp4"
    file.save(str(dest_path))

    file_size = dest_path.stat().st_size
    now_human = datetime.now(timezone.utc).strftime("%b %d, %Y")

    conn = get_db()
    conn.execute(
        """
        INSERT INTO videos
            (id, source, prompt, caption, generation_type, width, height,
             has_video, video_path, posted_at, posted_at_human,
             like_count, view_count, share_count, remix_count,
             cameos, tags, topic_labels, file_size_bytes)
        VALUES (?, 'v2_draft', ?, ?, 'upload', ?, ?,
                1, ?, ?, ?, 0, 0, 0, 0, '[]', '[]', '[]', ?)
        """,
        (video_id, prompt or None, caption or None, width, height,
         str(dest_path), datetime.now(timezone.utc).timestamp(), now_human, file_size),
    )
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "id": video_id, "path": f"/video/{video_id}"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5060, debug=True)
