"""assets/icon.ico ve icon.png uretir (derleme sirasinda calisir)."""
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
from icon_draw import draw

out = root / "assets"
out.mkdir(exist_ok=True)
big = draw(256)
big.save(out / "icon.png")
big.save(out / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
print("Ikon uretildi:", out)
