#!/usr/bin/env python3
"""Generate illustrated PDF picture book for '狼来了'"""
import os
import sys
from PIL import Image

BASE = r"D:\WorkSpace\VSCodeProject\2026_AIGC\wangpu\content-builder-agent"
BLOGS = os.path.join(BASE, "blogs")
OUTPUT = os.path.join(BASE, "story", "狼来了-绘本.pdf")

pages_data = [
    ("wolf-comes-page-1-cover", "封面"),
    ("wolf-comes-page-2", "第2页"),
    ("wolf-comes-page-3", "第3页"),
    ("wolf-comes-page-4", "第4页"),
    ("wolf-comes-page-5", "第5页"),
    ("wolf-comes-page-6", "第6页"),
    ("wolf-comes-page-7", "第7页"),
    ("wolf-comes-page-8", "第8页"),
    ("wolf-comes-page-9", "第9页"),
    ("wolf-comes-page-10", "第10页"),
    ("wolf-comes-page-11", "第11页"),
    ("wolf-comes-page-12", "第12页"),
]

images = []
for slug, label in pages_data:
    img_path = os.path.join(BLOGS, slug, "hero.png")
    if os.path.exists(img_path):
        img = Image.open(img_path).convert("RGB")
        images.append(img)
        print(f"Loaded: {label} ({img.size})")
    else:
        print(f"MISSING: {img_path}")

if images:
    images[0].save(OUTPUT, "PDF", save_all=True, append_images=images[1:], resolution=150)
    print(f"\nPDF saved to: {OUTPUT}")
    print(f"Total pages: {len(images)}")
else:
    print("No images found!")
    sys.exit(1)
