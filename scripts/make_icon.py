"""Generate the menu bar icon. Run once when you tweak the design.

Output is a 44x44 black-on-transparent PNG (template image — macOS auto-inverts
for dark mode). Drop the PNG in assets/.
"""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "menubar_icon.png"
OUT.parent.mkdir(exist_ok=True)

SIZE = 44  # @2x for a 22pt menu bar
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

BLACK = (0, 0, 0, 255)

# Mic capsule
d.rounded_rectangle((15, 6, 29, 26), radius=7, fill=BLACK)

# Curved bracket under the capsule (drawn as an arc with thickness)
d.arc((10, 14, 34, 30), start=0, end=180, fill=BLACK, width=2)

# Stem
d.rectangle((21, 29, 23, 36), fill=BLACK)

# Base
d.rectangle((15, 36, 29, 38), fill=BLACK)

img.save(OUT)
print(f"wrote {OUT}")
