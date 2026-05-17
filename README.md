# Muse Content Viewer

Static site version of the Muse Content Viewer for browsing AI News articles and XHS posts.

## Build

```bash
python build.py [--data-dir ~/.muse-dev] [--output-dir .]
```

Reads SQLite databases from the data directory and outputs:
- `data/articles.json` — AI News articles
- `data/xhs-posts.json` — XHS posts with image metadata
- `data/meta.json` — build timestamp and counts
- `media/images/` — XHS post images (resized if Pillow is available)

## Local preview

```bash
python3 -m http.server 8895
```

Open http://localhost:8895 in a browser.

## Deploy

Hosted via GitHub Pages on the `main` branch.
