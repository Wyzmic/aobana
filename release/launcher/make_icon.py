"""Render static/aobana.svg into aobana.ico: headless Chrome draws the SVG at 1024 px, Pillow
downsamples each icon size from that one master. Beside it, the installer's pictures
(wizard-panel-*.png, wizard-small-*.png), drawn from the same SVG.

    python release/launcher/make_icon.py [out.ico]
    python release/launcher/make_icon.py --unix release/unix    aobana.png and aobana.icns

Needs Chrome (or Edge) and Pillow in the Python that runs it - the dev Python, not the bundle.
"""
import os
import subprocess
import sys
import tempfile

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SVG = os.path.join(ROOT, "static", "aobana.svg")
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def browser():
    for path in BROWSERS:
        if os.path.exists(path):
            return path
    sys.exit("make_icon: no Chrome or Edge found")


def render(svg_path, png, w, h):
    url = "file:///" + svg_path.replace("\\", "/")
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([browser(), "--headless", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=1", f"--user-data-dir={profile}",
                        f"--window-size={w},{h}", "--default-background-color=00000000",
                        f"--screenshot={png}", url],
                       check=True, capture_output=True, timeout=60)
    img = Image.open(png).convert("RGBA")
    if img.size != (w, h):
        sys.exit(f"make_icon: Chrome rendered {img.size}, expected {w}x{h}")
    return img


def render_master(png):
    return render(SVG, png, 1024, 1024)


SMALL_SIZES = (58, 71, 85, 103, 112, 129, 147)
PANEL_SIZES = ((202, 386), (269, 515), (336, 643), (403, 772), (430, 824), (498, 953), (534, 1022))


def flower_only():
    text = open(SVG, encoding="utf-8").read()
    body = text[text.index("/>", text.index("<rect")) + 2:text.rindex("</svg>")]
    return body


def panel_svg(path):
    w, h = 1068, 2044
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#26303d"/><stop offset="1" stop-color="#161b23"/>
    </linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="#2b72e7" stop-opacity="0.35"/>
      <stop offset="1" stop-color="#2b72e7" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="{w}" height="{h}" fill="url(#bg)"/>
  <circle cx="{w / 2}" cy="{h * 0.40}" r="{w * 0.52}" fill="url(#glow)"/>
  <svg x="{w * 0.14}" y="{h * 0.40 - w * 0.36}" width="{w * 0.72}" height="{w * 0.72}"
       viewBox="150 180 724 724">{flower_only()}</svg>
  <text x="{w / 2}" y="{h * 0.66}" text-anchor="middle" fill="#ffffff"
        font-family="Segoe UI Variable Display, Segoe UI, sans-serif" font-weight="600"
        font-size="{w * 0.135}" letter-spacing="{w * 0.004}">Aobana</text>
  <rect x="{w * 0.42}" y="{h * 0.69}" width="{w * 0.16}" height="{w * 0.008}" rx="{w * 0.004}"
        fill="#f5cb43"/>
</svg>"""
    open(path, "w", encoding="utf-8").write(svg)
    return w, h


def flower_svg(path):
    body = flower_only()
    for fill in ('fill="#dee4ec"', 'fill="#ffffff"'):
        body = body.replace(fill, fill + ' stroke="#9aa7b8" stroke-width="14" stroke-linejoin="round"')
    open(path, "w", encoding="utf-8").write(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="150 180 724 724">'
        + body + "</svg>")


def wizard_images(out_dir):
    with tempfile.TemporaryDirectory() as tmp:
        svg = os.path.join(tmp, "panel.svg")
        w, h = panel_svg(svg)
        panel = render(svg, os.path.join(tmp, "panel.png"), w, h)
        svg = os.path.join(tmp, "flower.svg")
        flower_svg(svg)
        flower = render(svg, os.path.join(tmp, "flower.png"), 1024, 1024)
    names = []
    for pw, ph in PANEL_SIZES:
        name = os.path.join(out_dir, f"wizard-panel-{pw}.png")
        panel.convert("RGB").resize((pw, ph), Image.LANCZOS).save(name)
        names.append(name)
    for s in SMALL_SIZES:
        name = os.path.join(out_dir, f"wizard-small-{s}.png")
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        inner = round(s * 0.78)
        f = flower.resize((inner, inner), Image.LANCZOS)
        img.paste(f, ((s - inner) // 2, (s - inner) // 2), f)
        img.save(name)
        names.append(name)
    return names


def main(out):
    with tempfile.TemporaryDirectory() as tmp:
        master = render_master(os.path.join(tmp, "master.png"))
    frames = [master.resize((s, s), Image.LANCZOS) for s in SIZES]
    frames[-1].save(out, format="ICO", sizes=[(s, s) for s in SIZES],
                    append_images=frames[:-1])
    base = os.path.splitext(out)[0]
    sheet = Image.new("RGBA", (sum(SIZES) + 10 * len(SIZES), 256), (255, 255, 255, 255))
    x = 0
    for f in frames:
        sheet.paste(f, (x, 0), f)
        x += f.width + 10
    sheet.save(base + "_sizes.png")
    zoom = Image.new("RGBA", (16 * 8 + 24 * 8 + 32 * 8 + 40, 32 * 8), (255, 255, 255, 255))
    x = 0
    for f in frames:
        if f.width in (16, 24, 32):
            big = f.resize((f.width * 8, f.height * 8), Image.NEAREST)
            zoom.paste(big, (x, 0), big)
            x += big.width + 20
    zoom.save(base + "_zoom.png")
    print(f"make_icon: {out} ({', '.join(map(str, SIZES))})")
    names = wizard_images(os.path.dirname(os.path.abspath(out)))
    print(f"make_icon: {len(names)} installer pictures beside it")


def unix_icons(out_dir):
    with tempfile.TemporaryDirectory() as tmp:
        master = render_master(os.path.join(tmp, "master.png"))
    png = os.path.join(out_dir, "aobana.png")
    master.resize((512, 512), Image.LANCZOS).save(png, optimize=True)
    icns = os.path.join(out_dir, "aobana.icns")
    master.save(icns, format="ICNS")
    print(f"make_icon: {png}, {icns}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--unix"]:
        unix_icons(sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "release", "unix"))
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "aobana.ico"))
