"""Genera karaoke.ico (micrófono sobre círculo) para el ejecutable."""

from pathlib import Path

from PIL import Image, ImageDraw

S = 512
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.ellipse((16, 16, S - 16, S - 16), fill=(124, 58, 237))
# cápsula
d.rounded_rectangle((196, 92, 316, 300), radius=60, fill=(255, 255, 255))
for y in (150, 190, 230):
    d.line((214, y, 298, y), fill=(196, 181, 253), width=10)
# horquilla y pie
d.arc((156, 170, 356, 350), start=0, end=180, fill=(255, 255, 255), width=22)
d.rectangle((245, 345, 267, 410), fill=(255, 255, 255))
d.rounded_rectangle((186, 400, 326, 426), radius=12, fill=(255, 255, 255))
# notas
d.ellipse((370, 300, 420, 340), fill=(253, 224, 71))
d.rectangle((406, 200, 418, 322), fill=(253, 224, 71))
d.polygon([(418, 200), (452, 222), (418, 236)], fill=(253, 224, 71))

out = Path(__file__).with_name("karaoke.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save(out.with_suffix(".png"))
print(out)
