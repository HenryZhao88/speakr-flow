"""Generate the menu bar icons. Run when you tweak the design.

Produces two PNGs in assets/:
  - menubar_icon.png            (black-on-transparent template — idle)
  - menubar_icon_recording.png  (red mic — shown while recording)
"""
from pathlib import Path
from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "assets"
ASSETS.mkdir(exist_ok=True)

SIZE = 44  # @2x for a 22pt menu bar


def draw_mic(color):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Mic capsule
    d.rounded_rectangle((15, 6, 29, 26), radius=7, fill=color)

    # Curved bracket
    d.arc((10, 14, 34, 30), start=0, end=180, fill=color, width=2)

    # Stem
    d.rectangle((21, 29, 23, 36), fill=color)

    # Base
    d.rectangle((15, 36, 29, 38), fill=color)

    return img


BLACK = (0, 0, 0, 255)
RED = (220, 53, 53, 255)

draw_mic(BLACK).save(ASSETS / "menubar_icon.png")
draw_mic(RED).save(ASSETS / "menubar_icon_recording.png")
print(f"wrote {ASSETS}/menubar_icon.png + menubar_icon_recording.png")
