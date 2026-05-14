#!/usr/bin/env python3
"""Create a beautiful PDF picture book from AI-generated illustrations for '狼来了'"""

import os
import sys
from PIL import Image, ImageDraw, ImageFont

# Determine base paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# The script is in /story/, so BASE_PATH is parent directory
BASE_PATH = os.path.dirname(SCRIPT_DIR)
BLOGS_PATH = os.path.join(BASE_PATH, "blogs")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "狼来了-绘本.pdf")

# Page dimensions (A4 landscape ratio)
PAGE_WIDTH = 1600
PAGE_HEIGHT = 1132

# Story pages data with corresponding AI images
PAGES = [
    {
        "type": "cover",
        "title": "狼来了",
        "subtitle": "一个关于诚实的经典寓言故事",
        "image_folder": "wolf-comes-page-1-cover",
    },
    {
        "type": "story",
        "page_num": 1,
        "text": "从前，在一个美丽的小山村里，住着一个可爱的小男孩。小男孩每天都带着羊群到山坡上放牧。山坡上开满了五颜六色的野花，蝴蝶在花丛中翩翩起舞。",
        "image_folder": "wolf-comes-page-2",
    },
    {
        "type": "story",
        "page_num": 2,
        "text": "一天，小男孩在山坡上觉得非常无聊。他看着远处正在田里劳作的村民们，突然想到了一个捉弄人的主意。",
        "image_folder": "wolf-comes-page-3",
    },
    {
        "type": "story",
        "page_num": 3,
        "text": '"狼来了！狼来了！快来救救我的羊！"他深吸一口气，大声喊道。',
        "image_folder": "wolf-comes-page-4",
    },
    {
        "type": "story",
        "page_num": 4,
        "text": "村民们听到喊声后，立刻放下手中的农活，拿起锄头和木棍，急急忙忙地跑上山坡。可是却发现小男孩正坐在地上哈哈大笑。",
        "image_folder": "wolf-comes-page-5",
    },
    {
        "type": "story",
        "page_num": 5,
        "text": "村民们听了很生气，下山继续干活了。过了几天，小男孩又故技重施，再次大声喊道："狼来了！狼来了！这次是真的！"",
        "image_folder": "wolf-comes-page-6",
    },
    {
        "type": "story",
        "page_num": 6,
        "text": '善良的村民们又一次跑上山坡，结果再次被骗了。村里的长者严肃地对小男孩说："说谎是不对的，你这样会失去大家的信任。"',
        "image_folder": "wolf-comes-page-7",
    },
    {
        "type": "story",
        "page_num": 7,
        "text": "又过了几天，小男孩正在山坡上悠闲地吃着苹果。突然，他发现远处的树林里真的窜出了一只大灰狼！",
        "image_folder": "wolf-comes-page-8",
    },
    {
        "type": "story",
        "page_num": 8,
        "text": '小男孩吓坏了，他拼命地大声呼救："狼来了！狼来了！真的有狼！求求你们快来救我！"',
        "image_folder": "wolf-comes-page-9",
    },
    {
        "type": "story",
        "page_num": 9,
        "text": '可是这一次，山坡下的村民们听到喊声后，只是互相看了看，继续低头干活。"那个孩子又在说谎了，我们不能再被他骗了。"',
        "image_folder": "wolf-comes-page-10",
    },
    {
        "type": "story",
        "page_num": 10,
        "text": "大灰狼冲进了羊群，咬死了好多只羊。小男孩吓得躲在树上大哭。幸好这时一位路过的猎人赶来赶走了大灰狼，救下了小男孩。",
        "image_folder": "wolf-comes-page-11",
    },
    {
        "type": "story",
        "page_num": 11,
        "text": "小男孩哭着跑回村子，向大家道歉："对不起，我以前不该说谎骗你们。"村民们原谅了他，但长者告诉他："记住，一个经常说谎的人，即使他说真话，也没有人会相信了。"",
        "image_folder": "wolf-comes-page-12",
    },
    {
        "type": "ending",
        "title": "故事寓意",
        "text": "做人要诚实守信，不要欺骗他人。\n一旦失去了别人的信任，就很难再挽回。",
        "image_folder": "wolf-comes-page-12",
    },
]


def get_chinese_font(size):
    """Get Chinese font with fallback options"""
    # Try different Chinese fonts
    font_candidates = [
        ("msyh.ttc", size),      # Microsoft YaHei
        ("simhei.ttf", size),    # SimHei
        ("simsun.ttc", size),    # SimSun
        ("STSONG.TTF", size),    # STSong
        ("simkai.ttf", size),    # KaiTi
        ("arial.ttf", size),     # Arial fallback
    ]
    
    for font_name, font_size in font_candidates:
        try:
            # Try Windows font path
            font_path = os.path.join(r"C:\Windows\Fonts", font_name)
            if os.path.exists(font_path):
                return ImageFont.truetype(font_path, font_size)
        except:
            continue
    
    # Try system default
    try:
        return ImageFont.truetype("arial.ttf", size)
    except:
        return ImageFont.load_default()


def load_image(image_folder, max_width, max_height):
    """Load and resize image from blogs directory"""
    img_path = os.path.join(BLOGS_PATH, image_folder, "hero.png")
    
    if os.path.exists(img_path):
        img = Image.open(img_path).convert('RGB')
        # Resize maintaining aspect ratio
        img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        return img
    else:
        print(f"Warning: Image not found at {img_path}")
        return None


def create_gradient(width, height, color_top, color_bottom):
    """Create vertical gradient background"""
    img = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(img)
    
    r1, g1, b1 = int(color_top[1:3], 16), int(color_top[3:5], 16), int(color_top[5:7], 16)
    r2, g2, b2 = int(color_bottom[1:3], 16), int(color_bottom[3:5], 16), int(color_bottom[5:7], 16)
    
    for y in range(height):
        ratio = y / height
        r = int(r1 + (r2 - r1) * ratio)
        g = int(g1 + (g2 - g1) * ratio)
        b = int(b1 + (b2 - b1) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
    
    return img


def wrap_text(text, font, max_width):
    """Wrap Chinese text to fit within max_width"""
    chars = list(text)
    lines = []
    current_line = ""
    
    for char in chars:
        test_line = current_line + char
        bbox = font.getbbox(test_line)
        width = bbox[2] - bbox[0]
        
        if width <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = char
    
    if current_line:
        lines.append(current_line)
    
    return lines


def draw_rounded_rect(draw, x1, y1, x2, y2, radius, fill=None, outline=None):
    """Draw rounded rectangle"""
    # Main rectangle
    draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill, outline=outline)
    draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill, outline=outline)
    
    # Corners
    draw.pieslice([x1, y1, x1 + radius * 2, y1 + radius * 2], 180, 270, fill=fill, outline=outline)
    draw.pieslice([x2 - radius * 2, y1, x2, y1 + radius * 2], 270, 360, fill=fill, outline=outline)
    draw.pieslice([x1, y2 - radius * 2, x1 + radius * 2, y2], 90, 180, fill=fill, outline=outline)
    draw.pieslice([x2 - radius * 2, y2 - radius * 2, x2, y2], 0, 90, fill=fill, outline=outline)


def create_cover_page(page_data):
    """Create beautiful cover page"""
    # Dark blue gradient background
    bg = create_gradient(PAGE_WIDTH, PAGE_HEIGHT, "#1e3a5f", "#0f1f33")
    draw = ImageDraw.Draw(bg)
    
    # Load and place main image
    img = load_image(page_data["image_folder"], PAGE_WIDTH - 80, int(PAGE_HEIGHT * 0.55))
    if img:
        img_x = (PAGE_WIDTH - img.width) // 2
        img_y = 50
        
        # Add decorative frame
        frame_padding = 10
        draw.rectangle(
            [img_x - frame_padding, img_y - frame_padding, 
             img_x + img.width + frame_padding, img_y + img.height + frame_padding],
            fill="#1a2744"
        )
        draw.rectangle(
            [img_x - frame_padding, img_y - frame_padding, 
             img_x + img.width + frame_padding, img_y + img.height + frame_padding],
            outline="#FFD700", width=4
        )
        
        bg.paste(img, (img_x, img_y))
    
    # Title with gold color
    title_font = get_chinese_font(80)
    title = page_data["title"]
    bbox = title_font.getbbox(title)
    title_w = bbox[2] - bbox[0]
    title_x = (PAGE_WIDTH - title_w) // 2
    title_y = 650
    
    # Text shadow
    draw.text((title_x + 3, title_y + 3), title, fill="#000000", font=title_font)
    # Main title
    draw.text((title_x, title_y), title, fill="#FFD700", font=title_font)
    
    # Subtitle
    subtitle_font = get_chinese_font(28)
    subtitle = page_data["subtitle"]
    bbox = subtitle_font.getbbox(subtitle)
    subtitle_w = bbox[2] - bbox[0]
    subtitle_x = (PAGE_WIDTH - subtitle_w) // 2
    subtitle_y = title_y + 90
    
    draw.text((subtitle_x, subtitle_y), subtitle, fill="#E8E8E8", font=subtitle_font)
    
    # Decorative stars
    star_font = get_chinese_font(24)
    import random
    random.seed(42)  # Consistent decoration
    for _ in range(12):
        x = random.randint(30, PAGE_WIDTH - 30)
        y = random.randint(30, title_y - 30)
        draw.text((x, y), "✦", fill="#FFD700", font=star_font)
    
    return bg


def create_story_page(page_data):
    """Create story page with image and text"""
    # White background
    bg = Image.new('RGB', (PAGE_WIDTH, PAGE_HEIGHT), "#FFFFFF")
    draw = ImageDraw.Draw(bg)
    
    # Outer decorative border
    border_pad = 15
    draw.rectangle(
        [border_pad, border_pad, PAGE_WIDTH - border_pad, PAGE_HEIGHT - border_pad],
        outline="#1e3a5f", width=3
    )
    draw.rectangle(
        [border_pad + 6, border_pad + 6, PAGE_WIDTH - border_pad - 6, PAGE_HEIGHT - border_pad - 6],
        outline="#FFD700", width=1
    )
    
    # Image area
    img_width = PAGE_WIDTH - 60
    img_height = int(PAGE_HEIGHT * 0.52)
    img = load_image(page_data["image_folder"], img_width, img_height)
    
    if img:
        img_x = 30
        img_y = 40
        
        # Image shadow effect
        shadow_offset = 8
        shadow = Image.new('RGBA', (img.width + shadow_offset, img.height + shadow_offset), (0, 0, 0, 30))
        bg.paste(shadow, (img_x + shadow_offset, img_y + shadow_offset), shadow)
        
        bg.paste(img, (img_x, img_y))
        
        # Image border
        draw.rectangle(
            [img_x, img_y, img_x + img.width, img_y + img.height],
            outline="#e0e0e0", width=2
        )
    
    # Text area
    text_area_y = 620
    text_area_height = 450
    text_margin = 60
    text_box_width = PAGE_WIDTH - text_margin * 2
    
    # Text background with subtle color
    draw_rounded_rect(
        draw,
        text_margin, text_area_y,
        PAGE_WIDTH - text_margin, text_area_y + text_area_height,
        radius=20,
        fill="#f5f8fa"
    )
    
    # Page number badge
    page_num = page_data["page_num"]
    badge_font = get_chinese_font(20)
    badge_x = PAGE_WIDTH - 70
    badge_y = PAGE_HEIGHT - 55
    
    # Circular badge
    draw.ellipse([badge_x - 25, badge_y - 25, badge_x + 25, badge_y + 25], fill="#1e3a5f")
    badge_text_bbox = badge_font.getbbox(str(page_num))
    badge_text_w = badge_text_bbox[2] - badge_text_bbox[0]
    badge_text_h = badge_text_bbox[3] - badge_text_bbox[1]
    draw.text(
        (badge_x - badge_text_w // 2, badge_y - badge_text_h // 2),
        str(page_num),
        fill="#FFFFFF",
        font=badge_font
    )
    
    # Story text
    text_font = get_chinese_font(26)
    text = page_data["text"]
    lines = wrap_text(text, text_font, text_box_width - 40)
    
    # Center text vertically in text area
    line_spacing = 42
    total_text_h = len(lines) * line_spacing
    start_y = text_area_y + (text_area_height - total_text_h) // 2
    
    for i, line in enumerate(lines):
        y = start_y + i * line_spacing
        x = text_margin + 20
        draw.text((x, y), line, fill="#2c3e50", font=text_font)
    
    return bg


def create_ending_page(page_data):
    """Create the ending/moral page"""
    # Warm golden gradient
    bg = create_gradient(PAGE_WIDTH, PAGE_HEIGHT, "#1e3a5f", "#0f1f33")
    draw = ImageDraw.Draw(bg)
    
    # Load image
    img = load_image(page_data["image_folder"], PAGE_WIDTH - 100, int(PAGE_HEIGHT * 0.45))
    if img:
        img_x = (PAGE_WIDTH - img.width) // 2
        img_y = 50
        
        # Decorative golden frame
        frame_pad = 12
        draw.rectangle(
            [img_x - frame_pad, img_y - frame_pad,
             img_x + img.width + frame_pad, img_y + img.height + frame_pad],
            outline="#FFD700", width=5
        )
        draw.rectangle(
            [img_x - frame_pad - 5, img_y - frame_pad - 5,
             img_x + img.width + frame_pad + 5, img_y + img.height + frame_pad + 5],
            outline="#FFD700", width=2
        )
        
        bg.paste(img, (img_x, img_y))
    
    # Title "故事寓意"
    title_font = get_chinese_font(56)
    title = page_data["title"]
    bbox = title_font.getbbox(title)
    title_w = bbox[2] - bbox[0]
    title_x = (PAGE_WIDTH - title_w) // 2
    title_y = 600
    
    draw.text((title_x + 2, title_y + 2), title, fill="#000000", font=title_font)
    draw.text((title_x, title_y), title, fill="#FFD700", font=title_font)
    
    # Moral text
    moral_font = get_chinese_font(30)
    moral_lines = page_data["text"].split('\n')
    
    start_y = 690
    for i, line in enumerate(moral_lines):
        bbox = moral_font.getbbox(line)
        line_w = bbox[2] - bbox[0]
        line_x = (PAGE_WIDTH - line_w) // 2
        y = start_y + i * 55
        draw.text((line_x, y), line, fill="#FFFFFF", font=moral_font)
    
    # Decorative element
    deco_font = get_chinese_font(36)
    deco_text = "✦    ✦    ✦"
    bbox = deco_font.getbbox(deco_text)
    deco_w = bbox[2] - bbox[0]
    deco_x = (PAGE_WIDTH - deco_w) // 2
    deco_y = 830
    draw.text((deco_x, deco_y), deco_text, fill="#FFD700", font=deco_font)
    
    return bg


def generate_pdf():
    """Main function to generate the PDF picture book"""
    print("=" * 60)
    print("  📖 正在生成《狼来了》精美绘本 PDF")
    print("=" * 60)
    
    # Check PIL availability
    try:
        from PIL import Image
        print("✓ PIL/Pillow 库就绪")
    except ImportError:
        print("✗ PIL/Pillow 未安装，请执行: pip install Pillow")
        sys.exit(1)
    
    # Determine PDF generation method
    pdf_method = None
    
    # Try reportlab first (best quality)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Image as RLImage
        from reportlab.lib.units import mm
        pdf_method = 'reportlab'
        print("✓ 使用 reportlab 生成PDF（高质量）")
    except ImportError:
        pass
    
    # Try img2pdf
    if not pdf_method:
        try:
            import img2pdf
            pdf_method = 'img2pdf'
            print("✓ 使用 img2pdf 生成PDF")
        except ImportError:
            pass
    
    # Fallback to PIL
    if not pdf_method:
        pdf_method = 'pil'
        print("! 未找到PDF库，使用PIL内置PDF功能")
    
    # Create temp directory for page images
    temp_dir = os.path.join(SCRIPT_DIR, "temp_pages")
    os.makedirs(temp_dir, exist_ok=True)
    
    print(f"\n📝 共 {len(PAGES)} 页，开始生成...\n")
    
    page_files = []
    for i, page_data in enumerate(PAGES):
        print(f"  [{i+1}/{len(PAGES)}] 生成: {page_data['type']}...", end=" ")
        
        if page_data["type"] == "cover":
            page_img = create_cover_page(page_data)
        elif page_data["type"] == "ending":
            page_img = create_ending_page(page_data)
        else:
            page_img = create_story_page(page_data)
        
        # Save page as high-quality PNG
        page_path = os.path.join(temp_dir, f"page_{i+1:03d}.png")
        page_img.save(page_path, "PNG", quality=95)
        page_files.append(page_path)
        print(f"✓ ({page_img.width}x{page_img.height})")
    
    print(f"\n✓ 所有 {len(page_files)} 页已生成")
    print(f"📁 临时文件: {temp_dir}")
    
    # Assemble PDF
    print(f"\n📄 正在组装 PDF: {OUTPUT_PATH}")
    
    if pdf_method == 'reportlab':
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate, Image as RLImage, PageBreak
        from reportlab.lib.units import mm
        
        doc = SimpleDocTemplate(
            OUTPUT_PATH,
            pagesize=A4,
            rightMargin=0, leftMargin=0,
            topMargin=0, bottomMargin=0
        )
        
        story = []
        for page_path in page_files:
            img = RLImage(page_path, width=A4[0], height=A4[1])
            story.append(img)
            if page_path != page_files[-1]:
                story.append(PageBreak())
        
        doc.build(story)
        print("✓ PDF 生成成功 (reportlab)")
    
    elif pdf_method == 'img2pdf':
        import img2pdf
        with open(OUTPUT_PATH, "wb") as f:
            f.write(img2pdf.convert(page_files))
        print("✓ PDF 生成成功 (img2pdf)")
    
    elif pdf_method == 'pil':
        images = [Image.open(p) for p in page_files]
        first = images[0]
        rest = images[1:]
        first.save(
            OUTPUT_PATH,
            "PDF",
            save_all=True,
            append_images=rest,
            resolution=150.0
        )
        print("✓ PDF 生成成功 (PIL内置)")
    
    # Cleanup temp files
    print(f"\n🧹 清理临时文件...")
    for page_path in page_files:
        if os.path.exists(page_path):
            os.remove(page_path)
    if os.path.exists(temp_dir):
        os.rmdir(temp_dir)
    
    # Print summary
    file_size_mb = os.path.getsize(OUTPUT_PATH) / (1024 * 1024)
    print(f"\n{'=' * 60}")
    print(f"  ✅ 绘本生成完成！")
    print(f"  📄 文件路径: {OUTPUT_PATH}")
    print(f"  📊 文件大小: {file_size_mb:.2f} MB")
    print(f"  📖 总页数: {len(PAGES)} 页（含封面和封底）")
    print(f"{'=' * 60}")
    
    return OUTPUT_PATH


if __name__ == "__main__":
    try:
        output = generate_pdf()
        print(f"\n🎉 成功！PDF绘本已保存至: {output}")
    except Exception as e:
        print(f"\n❌ 生成失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
