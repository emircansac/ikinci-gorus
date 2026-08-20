#!/usr/bin/env python3
"""Kaynak logodaki sahte damalı zemini gerçek alfa kanalına çevirir.

Girdi:  static/brand/logo-source.png  (opak, damalı arka plan)
Çıktı:  static/brand/logo-full.png    (ikon + yazı, şeffaf zemin)
        static/brand/logo-icon.png    (yalnızca ikon, kare)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "static" / "brand" / "logo-source.png"
OUT_FULL = ROOT / "static" / "brand" / "logo-full.png"
OUT_ICON = ROOT / "static" / "brand" / "logo-icon.png"

ICON_SIZE = 256
PAD = 12
ALPHA_KEEP = 24


def _alpha_for(r: int, g: int, b: int) -> int:
    mx = max(r, g, b)
    mn = min(r, g, b)
    chroma = mx - mn
    sat = chroma / mx if mx else 0.0
    warm = r - b
    # Damalı zemin: neredeyse gri, düşük saturasyon.
    if sat < 0.12 and warm < 22:
        return 0
    if warm < 14 or r < 18:
        return 0
    # Çekirdek: parlak turuncu — tam opak.
    if sat >= 0.35 and mx >= 110:
        return 255
    # Glow: alfa parlaklıkla düşer; koyu damalı kareler siyah kabuk olmasın.
    strength = (mx / 255.0) * max(sat, warm / 200.0)
    return max(0, min(255, int(strength * 340)))


def knock_out_checker(im: Image.Image) -> Image.Image:
    rgba = im.convert("RGBA")
    w, h = rgba.size
    px = rgba.load()
    for y in range(h):
        for x in range(w):
            r, g, b, _ = px[x, y]
            px[x, y] = (r, g, b, _alpha_for(r, g, b))
    return rgba


def bbox_opaque(im: Image.Image, thresh: int = ALPHA_KEEP) -> tuple[int, int, int, int] | None:
    w, h = im.size
    px = im.load()
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            if px[x, y][3] >= thresh:
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    if max_x < 0:
        return None
    return min_x, min_y, max_x + 1, max_y + 1


def row_occupancy(im: Image.Image, thresh: int = ALPHA_KEEP) -> list[int]:
    w, h = im.size
    px = im.load()
    counts = []
    for y in range(h):
        n = 0
        for x in range(w):
            if px[x, y][3] >= thresh:
                n += 1
        counts.append(n)
    return counts


def find_icon_text_split(counts: list[int], box: tuple[int, int, int, int]) -> int | None:
    """İkon (üst) ile yazı (alt, daha geniş) arasındaki occupancy sıçraması."""
    _, y0, _, y1 = box
    if y1 - y0 < 20:
        return None
    best_jump = 0
    split = None
    look = 4
    # Glow ikonun altına taşar; yazı aniden daha genişler.
    lo = y0 + (y1 - y0) // 3
    hi = y1 - 8
    for y in range(lo, hi):
        jump = counts[min(y1 - 1, y + look)] - counts[max(y0, y - look)]
        if jump > best_jump:
            best_jump = jump
            split = y
    if split is None or best_jump < 40:
        return None
    return split


def crop_padded(im: Image.Image, box: tuple[int, int, int, int], pad: int = PAD) -> Image.Image:
    x0, y0, x1, y1 = box
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(im.width, x1 + pad)
    y1 = min(im.height, y1 + pad)
    return im.crop((x0, y0, x1, y1))


def make_square(im: Image.Image, size: int = ICON_SIZE) -> Image.Image:
    w, h = im.size
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - w) // 2, (side - h) // 2), im)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"kaynak yok: {SRC}")
    knocked = knock_out_checker(Image.open(SRC))
    full_box = bbox_opaque(knocked)
    if full_box is None:
        raise SystemExit("opak piksel bulunamadı — eşik çok agresif olabilir")
    full = crop_padded(knocked, full_box)
    OUT_FULL.parent.mkdir(parents=True, exist_ok=True)
    full.save(OUT_FULL, "PNG")

    counts = row_occupancy(knocked)
    split = find_icon_text_split(counts, full_box)
    if split is None:
        raise SystemExit("ikon/yazı boşluğu bulunamadı")
    icon_box = bbox_opaque(knocked.crop((0, 0, knocked.width, split)))
    if icon_box is None:
        raise SystemExit("ikon bbox boş")
    icon = crop_padded(knocked, icon_box)
    make_square(icon).save(OUT_ICON, "PNG")

    def report(path: Path) -> None:
        im = Image.open(path)
        extrema = im.getextrema()
        amin, amax = extrema[3] if im.mode == "RGBA" else (255, 255)
        print(f"{path.name}: {im.size} mode={im.mode} alpha={amin}-{amax}")

    report(OUT_FULL)
    report(OUT_ICON)
    print(f"split_row={split} full_box={full_box}")


if __name__ == "__main__":
    main()
