"""Hey Tank logosu: acik gri yuvarlatilmis kare uzerinde koyu 'T'. Tepsi ve exe ikonu icin tek kaynak."""
from PIL import Image, ImageDraw

BG = "#EDEDED"
FG = "#111111"


def draw(size=256):
    s = 1024  # buyuk ciz, kucult -> puruzsuz kenar
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=BG)
    d.rectangle((s * 0.25, s * 0.27, s * 0.75, s * 0.39), fill=FG)   # ust cubuk
    d.rectangle((s * 0.44, s * 0.27, s * 0.56, s * 0.75), fill=FG)   # govde
    return img.resize((size, size), Image.LANCZOS)
