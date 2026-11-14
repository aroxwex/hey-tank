"""Hey Tank logosu: koyu kare, ince cerceve, acik 'T' ve yesil 'dinliyor' noktasi.
Tepsi ve exe ikonu icin tek kaynak."""
from PIL import Image, ImageDraw

TILE = "#171717"
EDGE = "#3A3A3A"
FG = "#EDEDED"
DOT = "#4ADE80"


def draw(size=256):
    s = 1024  # buyuk ciz, kucult -> puruzsuz kenar
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r, b = int(s * 0.24), int(s * 0.03)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=r, fill=EDGE)
    d.rounded_rectangle((b, b, s - 1 - b, s - 1 - b), radius=r - b, fill=TILE)
    rr = int(s * 0.035)
    d.rounded_rectangle((s * 0.24, s * 0.25, s * 0.76, s * 0.39), radius=rr, fill=FG)   # ust cubuk
    d.rounded_rectangle((s * 0.43, s * 0.25, s * 0.57, s * 0.77), radius=rr, fill=FG)   # govde
    cx, cy, cr = s * 0.73, s * 0.72, s * 0.075
    d.ellipse((cx - cr, cy - cr, cx + cr, cy + cr), fill=DOT)
    return img.resize((size, size), Image.LANCZOS)
