"""Embed the slogan and author letter's WenKai glyphs for offline WXSS delivery.

Source: https://github.com/lxgw/LxgwWenKai
License: assets/fonts/LICENSE-WenKai.txt (SIL OFL 1.1).
Run with fonttools and brotli available on PYTHONPATH; source TTF stays in artifacts.
"""
import base64
import re
import subprocess
from pathlib import Path
from fontTools import subset
from fontTools.ttLib import TTFont

root = Path(__file__).resolve().parent.parent
phrase = "每一次结束，都是新的开始。"
phrase += subprocess.check_output(
    ["node", "-e", "process.stdout.write(require('./utils/constants').AUTHOR_LETTER.paragraphs.join('') + require('./utils/constants').AUTHOR_LETTER.title)"],
    cwd=root, encoding="utf-8",
)
# Read the native labels too, so button text changes keep all their WenKai glyphs.
wxml = (root / "components/author-letter/author-letter.wxml").read_text(encoding="utf-8")
phrase += "".join(re.findall(r">([^<>]+)<", wxml))
phrase = re.sub(r"\{\{.*?\}\}", "", phrase)
font = TTFont(root / "artifacts/LXGWWenKai-Regular.ttf")
missing = [char for char in set(phrase) if not char.isspace() and ord(char) not in font.getBestCmap()]
# Decorative symbols may use the system fallback; all text must have WenKai glyphs.
assert all(char in "✦✧✳" for char in missing), missing
options = subset.Options()
options.flavor = "woff2"
options.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14, 16, 17]
options.name_legacy = True
subsetter = subset.Subsetter(options=options)
subsetter.populate(text=phrase)
subsetter.subset(font)
font.flavor = "woff2"
target = root / "assets/fonts/motto-wenkai.woff2"
font.save(target)
encoded = base64.b64encode(target.read_bytes()).decode("ascii")
css = (
    "/* WenKai slogan and author-letter subset. Source and SIL OFL 1.1: LICENSE-WenKai.txt. */\n"
    "@font-face { font-family: 'MAMottoWenKai'; font-style: normal; font-weight: 400; "
    f"src: url('data:font/woff2;base64,{encoded}') format('woff2'); }}\n"
)
(root / "assets/fonts/motto.wxss").write_text(css, encoding="utf-8")
print(f"Embedded {len(set(phrase))} slogan and letter characters; WOFF2: {target.stat().st_size} bytes.")
