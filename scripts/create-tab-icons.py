"""Generate the small local bitmap icons required by WeChat's native tabBar."""
from pathlib import Path
from PIL import Image, ImageDraw

target = Path(__file__).resolve().parents[1] / 'assets'
target.mkdir(exist_ok=True)
for name in ['home', 'chat', 'collection', 'profile']:
    for active in [False, True]:
        image = Image.new('RGBA', (192, 192))
        draw = ImageDraw.Draw(image)
        color = '#665273' if active else '#9E98A6'
        width = 12 if active else 8
        if active:
            draw.ellipse((6, 6, 186, 186), fill='#E7DFEF')
        if name == 'home':
            draw.line([(36, 87), (96, 35), (156, 87)], fill=color, width=width, joint='curve')
            draw.line([(48, 82), (48, 151), (79, 151), (79, 108), (113, 108), (113, 151), (144, 151), (144, 82)], fill=color, width=width, joint='curve')
        elif name == 'chat':
            draw.rounded_rectangle((33, 39, 157, 134), radius=29, outline=color, width=width)
            draw.polygon([(51, 123), (51, 154), (86, 131)], fill=color)
            draw.polygon([(60, 121), (60, 138), (79, 121)], fill='#E7DFEF' if active else (0, 0, 0, 0))
            for x in [66, 95, 124]:
                draw.ellipse((x - 5, 81, x + 5, 91), fill=color)
        elif name == 'collection':
            draw.rounded_rectangle((39, 41, 148, 145), radius=12, outline=color, width=width)
            draw.line([(49, 59), (93, 59), (108, 85), (108, 59), (135, 59)], fill=color, width=6, joint='curve')
            draw.line([(59, 113), (116, 113)], fill=color, width=7)
            draw.line([(59, 130), (105, 130)], fill=color, width=7)
        else:
            draw.ellipse((68, 34, 125, 91), outline=color, width=width)
            draw.arc((43, 95, 149, 186), 180, 360, fill=color, width=width)
            draw.line([(47, 139), (47, 151), (145, 151), (145, 139)], fill=color, width=width, joint='curve')
        image.resize((64, 64), Image.Resampling.LANCZOS).save(target / f'tab-{name}{"-active" if active else ""}.png')
