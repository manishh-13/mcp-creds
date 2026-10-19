"""Draw the app icon (gradient tile + key) and write icon.png, icon.icns (macOS) and icon.ico (Windows)."""
import shutil, subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).parent
S = 2048  # supersample, then scale down

def lerp(a, b, t): return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

def gradient(size):
    stops = [(0.0, (110, 231, 183)), (0.5, (125, 211, 252)), (1.0, (167, 139, 250))]
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
                if t <= t1:
                    px[x, y] = lerp(c0, c1, (t - t0) / (t1 - t0)); break
    return img

def draw():
    small = gradient(256).resize((S, S), Image.BICUBIC)
    # macOS icon grid: content tile ~ 80% of canvas with rounded corners
    pad = int(S * 0.1); tile = S - 2 * pad; r = int(tile * 0.225)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([pad, pad, pad + tile, pad + tile], r, fill=255)
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([pad, pad + 30, pad + tile, pad + tile + 30], r, fill=(0, 0, 0, 110))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(40)))
    canvas.paste(small, (0, 0), mask)
    # key glyph (same geometry as the in-app mark, 24-unit grid)
    d = ImageDraw.Draw(canvas)
    u = tile / 24 * 0.78; ox = pad + (tile - 24 * u) / 2 + u * 0.4; oy = pad + (tile - 24 * u) / 2 - u * 0.4
    P = lambda x, y: (ox + x * u, oy + y * u)
    w = int(2.5 * u); ink = (7, 8, 12, 255)
    cx, cy = P(8, 15); rr = 4 * u
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=ink, width=w)
    for a, b in [((10.8, 12.2), (20, 3)), ((16, 7), (19, 10)), ((13.5, 9.5), (15.5, 11.5))]:
        d.line([P(*a), P(*b)], fill=ink, width=w)
        for pt in (P(*a), P(*b)):
            d.ellipse([pt[0] - w / 2, pt[1] - w / 2, pt[0] + w / 2, pt[1] + w / 2], fill=ink)
    return canvas.resize((1024, 1024), Image.LANCZOS)

def main():
    icon = draw(); icon.save(HERE / "icon.png")
    icon.save(HERE / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    if sys.platform == "darwin" and shutil.which("iconutil"):
        iset = HERE / "icon.iconset"; iset.mkdir(exist_ok=True)
        for s in (16, 32, 128, 256, 512):
            icon.resize((s, s), Image.LANCZOS).save(iset / f"icon_{s}x{s}.png")
            icon.resize((s * 2, s * 2), Image.LANCZOS).save(iset / f"icon_{s}x{s}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iset), "-o", str(HERE / "icon.icns")], check=True)
        shutil.rmtree(iset)
    print("icons written to", HERE)

if __name__ == "__main__":
    main()
