"""Build the shareable `deliverables/` folder from the approved sets listed in a selection manifest.

  python scripts/package_deliverables.py                      # uses deliverables/selection.json
  python scripts/package_deliverables.py --manifest my.json --data data

The manifest names, per set, either a whole job (`"job": "job-..."` -> every card's CURRENT image) or explicit cards
(`"cards": ["<card_id>", ...]` -> each card's current image). Nothing is generated and no network/API is used: images
are copied from `data/artifacts` as the app produced them. Output per set: `NN_cat<C>_<subject>.png`, `set.json`
(category/subject metadata, no prompts, no keys), plus `previews/<set>.png` contact sheets, `README.md` and a static
offline `index.html` gallery. Sets with no selection are listed as pending instead of being guessed.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "card"


def load_cards(db: sqlite3.Connection, entry: dict) -> list[sqlite3.Row]:
    base = "SELECT card_id, job_id, position, category, object_name, object_description, artifact_id FROM cards"
    if entry.get("job"):
        return db.execute(base + " WHERE job_id=? AND status='done' ORDER BY variant_id, position", (entry["job"],)).fetchall()
    rows = []
    for cid in entry.get("cards", []):
        r = db.execute(base + " WHERE card_id=? AND status='done'", (cid,)).fetchone()
        if r is None:
            raise SystemExit(f"card not found or not done: {cid}")
        rows.append(r)
    return rows


def contact_sheet(images: list[tuple[Path, str]], out: Path, thumb: int = 256) -> None:
    cols = 5
    rows = (len(images) + cols - 1) // cols
    pad, lab = 12, 22
    sheet = Image.new("RGB", (cols * (thumb + pad) + pad, rows * (thumb + lab + pad) + pad), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    for i, (p, label) in enumerate(images):
        x, y = pad + (i % cols) * (thumb + pad), pad + (i // cols) * (thumb + lab + pad)
        with Image.open(p) as im:
            sheet.paste(im.convert("RGB").resize((thumb, thumb), Image.LANCZOS), (x, y))
        d.text((x + 2, y + thumb + 4), label[:42], fill=(220, 220, 225))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, optimize=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(ROOT / "deliverables" / "selection.json"))
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "deliverables"))
    a = ap.parse_args()
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    data, out = Path(a.data), Path(a.out)
    db = sqlite3.connect(data / "collection_studio.sqlite3")
    db.row_factory = sqlite3.Row
    built, pending = [], []
    for entry in manifest["sets"]:
        key, title = entry["key"], entry["title"]
        if not (entry.get("job") or entry.get("cards")):
            pending.append(entry)
            continue
        rows = load_cards(db, entry)
        folder = out / key
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        items, sheet_imgs = [], []
        for n, r in enumerate(rows, 1):
            src = data / "artifacts" / f"{r['artifact_id']}.png"
            if not src.is_file():
                raise SystemExit(f"missing image file for {r['card_id']}: {src.name}")
            meta = json.loads((data / "artifacts" / f"{r['artifact_id']}.json").read_text(encoding="utf-8"))
            if meta.get("is_mock"):
                raise SystemExit(f"{r['card_id']} is a MOCK image; refusing to package it")
            name = f"{n:02d}_cat{r['category']}_{slug(r['object_name'])}.png"
            shutil.copyfile(src, folder / name)
            sheet_imgs.append((folder / name, f"{n:02d} | cat {r['category']} | {r['object_name']}"))
            items.append({"file": name, "position": n, "category": r["category"], "subject": r["object_name"],
                          "description": r["object_description"], "artifact_id": r["artifact_id"],
                          "source_card": r["card_id"], "model": meta.get("provider", {}).get("label"),
                          "size": f"{meta['image']['width']}x{meta['image']['height']}"})
        (folder / "set.json").write_text(json.dumps({"collection": manifest.get("collection", "Movie"), "set": title,
                                                      "cards": items}, indent=2, ensure_ascii=False), encoding="utf-8")
        contact_sheet(sheet_imgs, out / "previews" / f"{key}.png")
        built.append((entry, items))

    md = [f"# {manifest.get('collection', 'Movie')} collection: final sets", "",
          "Real images produced by the app (Gemini Nano Banana 2.1, 512x512). Nothing here was generated by this script.", ""]
    htm = ["<!doctype html><meta charset=utf-8><title>Final sets</title><style>body{font:15px system-ui;background:#18181c;"
           "color:#eee;margin:24px}img{width:200px;height:200px;border-radius:8px}figure{margin:6px;display:inline-block;"
           "width:200px;vertical-align:top}figcaption{font-size:12px;color:#bbb}</style><h1>Final sets</h1>"]
    for entry, items in built:
        md += [f"## {entry['title']}: {len(items)} cards", "", f"![{entry['title']}](previews/{entry['key']}.png)", "",
               "| # | Category | Subject |", "|---|---|---|"]
        md += [f"| {i['position']} | {i['category']} | [{i['subject']}]({entry['key']}/{i['file']}) |" for i in items] + [""]
        htm.append(f"<h2>{html.escape(entry['title'])}</h2>")
        htm += [f"<figure><img src='{entry['key']}/{i['file']}' alt=''><figcaption>{i['position']:02d} | cat {i['category']}"
                f"<br>{html.escape(i['subject'])}</figcaption></figure>" for i in items]
    for entry in pending:
        md += [f"## {entry['title']}: PENDING", "", f"_{entry.get('note', 'No approved selection recorded yet.')}_", ""]
        htm.append(f"<h2>{html.escape(entry['title'])}: pending</h2><p>{html.escape(entry.get('note', ''))}</p>")
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.md").write_text("\n".join(md), encoding="utf-8")
    (out / "index.html").write_text("\n".join(htm), encoding="utf-8")
    print(f"built: {[e['key'] for e, _ in built]}; pending: {[e['key'] for e in pending]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
