#!/usr/bin/env python3
"""Build script for Muse Content Viewer static site.

Reads SQLite databases and outputs JSON data files + copies images.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Data loading (same logic as muse_content_viewer/viewer.py)
# ---------------------------------------------------------------------------


def load_articles(db_path: Path) -> list[dict]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id, source, source_label, title, url, content, author,
                      published_at, category, impact, urgency,
                      content_value_score, muse_relevance, muse_value_score,
                      career_relevance, career_value_score,
                      project_relevance, project_value_score,
                      classification_reasoning,
                      llm_summary, llm_evaluation, personal_insights, deep_dive,
                      expert_review, translated_content,
                      scraped_at, classified_at, enriched_at, exported_at, synced_at
               FROM articles ORDER BY published_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def load_xhs_posts(db_path: Path) -> list[dict]:
    """Load XHS posts with enrichment + metadata merged; JSON columns parsed."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row

        post_rows = conn.execute(
            """SELECT * FROM posts ORDER BY first_seen_at DESC LIMIT 500"""
        ).fetchall()

        if not post_rows:
            return []

        try:
            enrich_rows = conn.execute("SELECT * FROM post_enrichment").fetchall()
            enrich_by_id: dict[str, dict] = {r["red_id"]: dict(r) for r in enrich_rows}
        except sqlite3.OperationalError as e:
            if "no such table" not in str(e).lower():
                print(f"[build] WARNING loading post_enrichment: {e}", file=sys.stderr)
            enrich_by_id = {}

        try:
            meta_rows = conn.execute("SELECT * FROM post_metadata").fetchall()
            meta_by_id: dict[str, dict] = {r["red_id"]: dict(r) for r in meta_rows}
        except sqlite3.OperationalError as e:
            if "no such table" not in str(e).lower():
                print(f"[build] WARNING loading post_metadata: {e}", file=sys.stderr)
            meta_by_id = {}

    _json_map = [
        ("author_json", "author", {}),
        ("engagement_json", "engagement", {}),
        ("metadata_json", "metadata", {}),
        ("images_json", "images", []),
        ("tags_json", "tags", []),
    ]
    _skip_keys = {"id", "red_id"}

    posts = []
    for row in post_rows:
        d = dict(row)
        rid = d.get("red_id", "")
        for k, v in enrich_by_id.get(rid, {}).items():
            if k not in _skip_keys:
                d[k] = v
        for k, v in meta_by_id.get(rid, {}).items():
            if k not in _skip_keys:
                d[k] = v
        for col, key, default in _json_map:
            raw = d.pop(col, None)
            try:
                d[key] = json.loads(raw) if raw else default
            except (json.JSONDecodeError, TypeError):
                d[key] = default
        posts.append(d)
    return posts


def load_xhs_posts_with_images(db_path: Path) -> list[dict]:
    """Load all XHS posts with batch image data attached."""
    posts = load_xhs_posts(db_path)
    if not posts:
        return posts

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            img_rows = conn.execute(
                """SELECT note_id, image_index, original_url, local_path
                   FROM images ORDER BY note_id, image_index"""
            ).fetchall()
        except sqlite3.OperationalError as e:
            if "no such table" not in str(e).lower():
                print(f"[build] WARNING loading images: {e}", file=sys.stderr)
            img_rows = []

    images_by_note: dict[str, list[dict]] = {}
    for row in img_rows:
        d = dict(row)
        images_by_note.setdefault(d["note_id"], []).append(d)

    for post in posts:
        post["db_images"] = images_by_note.get(post["red_id"], [])

    return posts


# ---------------------------------------------------------------------------
# Image copy with optional resize
# ---------------------------------------------------------------------------


def copy_images(src_dir: Path, dst_dir: Path) -> tuple[int, int]:
    """Copy images preserving {note_id}/image_{idx}.jpg structure.

    Returns (copied_count, skipped_count).
    """
    if not src_dir.exists():
        print(f"[build] Image source not found: {src_dir}", file=sys.stderr)
        return 0, 0

    # Try to import Pillow for resizing
    try:
        from PIL import Image
        has_pillow = True
        print("[build] Pillow available — resizing images to max 800px width, quality 80")
    except ImportError:
        has_pillow = False
        print("[build] Pillow not available — copying images as-is")

    copied = 0
    skipped = 0

    for note_dir in sorted(src_dir.iterdir()):
        if not note_dir.is_dir():
            continue
        out_note_dir = dst_dir / note_dir.name
        out_note_dir.mkdir(parents=True, exist_ok=True)

        for img_file in sorted(note_dir.iterdir()):
            if not img_file.is_file():
                continue
            suffix = img_file.suffix.lower()
            if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
                skipped += 1
                continue

            out_path = out_note_dir / img_file.name

            if has_pillow:
                try:
                    img = Image.open(img_file)
                    if img.width > 800:
                        ratio = 800 / img.width
                        new_size = (800, int(img.height * ratio))
                        img = img.resize(new_size, Image.LANCZOS)
                    # Convert to RGB if needed (e.g. RGBA PNGs)
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    img.save(out_path, "JPEG", quality=80)
                    copied += 1
                except Exception as e:
                    print(f"[build] WARNING: failed to process {img_file}: {e}", file=sys.stderr)
                    shutil.copy2(img_file, out_path)
                    copied += 1
            else:
                shutil.copy2(img_file, out_path)
                copied += 1

    return copied, skipped


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Muse Content Viewer static site")
    parser.add_argument(
        "--data-dir",
        default=os.path.expanduser("~/.muse-dev"),
        help="Muse data directory (default: ~/.muse-dev)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Output directory (default: current directory)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)

    ai_news_db = data_dir / "ai_news_collector" / "ai-news.db"
    xhs_db = data_dir / "red_collector" / "xhs.db"
    images_src = data_dir / "red_collector" / "images"

    data_out = output_dir / "data"
    media_out = output_dir / "media" / "images"
    data_out.mkdir(parents=True, exist_ok=True)
    media_out.mkdir(parents=True, exist_ok=True)

    # --- Load AI News ---
    if ai_news_db.exists():
        print(f"[build] Loading AI News from {ai_news_db}")
        articles = load_articles(ai_news_db)
        print(f"[build] Loaded {len(articles)} articles")
    else:
        print(f"[build] AI News DB not found at {ai_news_db} — skipping")
        articles = []

    # --- Load XHS Posts ---
    if xhs_db.exists():
        print(f"[build] Loading XHS posts from {xhs_db}")
        xhs_posts = load_xhs_posts_with_images(xhs_db)
        print(f"[build] Loaded {len(xhs_posts)} XHS posts")
    else:
        print(f"[build] XHS DB not found at {xhs_db} — skipping")
        xhs_posts = []

    # --- Write JSON data ---
    articles_path = data_out / "articles.json"
    with open(articles_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False)
    print(f"[build] Wrote {articles_path} ({articles_path.stat().st_size / 1024:.1f} KB)")

    xhs_path = data_out / "xhs-posts.json"
    with open(xhs_path, "w", encoding="utf-8") as f:
        json.dump(xhs_posts, f, ensure_ascii=False)
    print(f"[build] Wrote {xhs_path} ({xhs_path.stat().st_size / 1024:.1f} KB)")

    # --- Write meta ---
    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "articles_count": len(articles),
        "xhs_posts_count": len(xhs_posts),
        "data_dir": str(data_dir),
    }
    meta_path = data_out / "meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[build] Wrote {meta_path}")

    # --- Copy images ---
    print(f"[build] Copying images from {images_src} to {media_out}")
    copied, skipped = copy_images(images_src, media_out)
    print(f"[build] Images: {copied} copied, {skipped} skipped")

    # --- Summary ---
    print()
    print("=== Build Complete ===")
    print(f"  Articles: {len(articles)}")
    print(f"  XHS Posts: {len(xhs_posts)}")
    print(f"  Images copied: {copied}")
    print(f"  Output: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
