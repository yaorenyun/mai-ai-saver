#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦麦省钱助手 · README 演示 GIF 生成器
=====================================
用 Pillow 逐帧渲染"终端打字机"动画，产出 README 展示用的 demo.gif。

设计决策
--------
- 西文用 DejaVu Sans Mono（等宽），中文用 Noto Sans CJK（全角=2倍半角宽），
  逐字符混合排版，模拟真实终端中英文混排效果。
- 内容顺序把 aha moment 放最前：先演示"想吃巨无霸 → 自动升档套餐"，
  再演示"预算30打工人午餐"，3 秒抓住评委注意力。
- 行式滚动渲染：超出行高自动上滚，还原真实终端行为。
- emoji 在无彩色字体的渲染环境下会变方框，统一映射为 ASCII 标记。
- 帧率 12fps + 96 色调色板量化，控制 GIF 体积（目标 < 4MB）。

用法
----
    python3 tools/make_demo_gif.py                 # 默认输出 docs/demo.gif
    python3 tools/make_demo_gif.py --out my.gif    # 自定义输出路径

参赛作品：2026 麦当劳程序员创意开发大赛
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
WIDTH, HEIGHT = 900, 460
FPS = 12
FONT_SIZE = 15
LINE_H = int(FONT_SIZE * 1.55)
PAD_X, PAD_Y = 18, 56          # 内容区原点（标题栏下方）
TITLEBAR_H = 34
MAX_LINES = (HEIGHT - PAD_Y - 16) // LINE_H

C_BG = "#0d1117"               # GitHub Dark 终端背景
C_TITLEBAR = "#161b22"
C_BORDER = "#30363d"
C_TEXT = "#c9d1d9"
C_DIM = "#8b949e"
C_GREEN = "#7ee787"            # 提示符 / 升级提示
C_YELLOW = "#e3b341"           # 高亮金额
C_BLUE = "#79c0ff"             # 命令
C_CURSOR = "#58a6ff"

MONO_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
CJK_FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

# emoji → ASCII（渲染环境无彩色 emoji 字体）
EMOJI_MAP = {
    "🥇": "[1]", "🥈": "[2]", "🥉": "[3]",
    "🍔": "", "🍟": "", "🥤": "", "🍽️": "",
    "⚠️": "[!]", "✅": "[v]", "👑": "*", "🎟️": "",
    "🔌": "", "🤖": "", "✨": "", "🚀": "",
}

# 演示内容：与真实脚本输出一致（见 examples/demo-cases.md）
SCENE1_CMD = "python3 scripts/price_optimizer.py --budget 50 --must big_mac"
SCENE1_OUT = [
    ("[提示] 「巨无霸」已自动升级为「巨无霸套餐」（套餐比单点省 ¥7.50）", C_GREEN),
    ("", C_TEXT),
    ("[1] 方案 1｜合计 ¥45.50｜综合得分 32｜完整正餐", C_TEXT),
    ("   · 巨无霸套餐  ¥39.50  [套餐] （比单点省 ¥7.50）", C_YELLOW),
    ("   · 圆筒冰淇淋  ¥6.00  [甜品]", C_TEXT),
    ("", C_TEXT),
    (">>> 说「想吃巨无霸」，算法直接给你套餐：立省 ¥7.50", C_GREEN),
]
SCENE2_CMD = "python3 scripts/price_optimizer.py --budget 30"
SCENE2_OUT = [
    ("[1] 方案 1｜合计 ¥28.50｜双层吉士堡 + 珍珠奶茶｜轻食档", C_TEXT),
    ("[2] 方案 2｜合计 ¥28.00｜双层吉士堡 + 大可乐｜轻食档", C_TEXT),
    ("[3] 方案 3｜合计 ¥29.50｜麦辣鸡腿堡 + 香甜玉米杯｜轻食档", C_TEXT),
    ("", C_TEXT),
    (">>> ¥30 吃不到套餐？保证「主食+饮料」结构，不做小食拼盘", C_GREEN),
]
ENDING = [
    ("# 1024 程序员节 · 麦麦省钱助手", C_YELLOW),
    ("# 花最少的钱，吃最像样的一顿 —— github.com/<your-id>/mai-ai-saver", C_DIM),
]


def strip_emoji(text: str) -> str:
    for k, v in EMOJI_MAP.items():
        text = text.replace(k, v)
    return text


# --------------------------------------------------------------------------- #
# 终端渲染器（行式滚动）
# --------------------------------------------------------------------------- #
class TerminalGif:
    def __init__(self) -> None:
        self.font = ImageFont.truetype(MONO_FONT_PATH, FONT_SIZE)
        self.font_cjk = ImageFont.truetype(CJK_FONT_PATH, FONT_SIZE)
        self.frames: list[Image.Image] = []
        self.lines: list[tuple[str, str]] = []     # 已完成的行 (文本, 颜色)
        self.current: tuple[str, str] | None = None  # 正在输入的行
        self.cursor_on = True

    # ---- 混合字宽与绘制 ----
    def _text_width(self, text: str) -> float:
        w = 0.0
        for ch in text:
            w += FONT_SIZE * (1.0 if ord(ch) > 0x2E7F else 0.6)
        return w

    def _draw_mixed(self, d: ImageDraw.ImageDraw, x: float, y: int,
                    text: str, color: str) -> None:
        cx = x
        for ch in text:
            font = self.font_cjk if ord(ch) > 0x2E7F else self.font
            d.text((cx, y), ch, font=font, fill=color)
            cx += FONT_SIZE * (1.0 if ord(ch) > 0x2E7F else 0.6)

    # ---- 帧渲染 ----
    def _render(self) -> Image.Image:
        img = Image.new("RGB", (WIDTH, HEIGHT), C_BG)
        d = ImageDraw.Draw(img)

        # 标题栏
        d.rectangle([0, 0, WIDTH, TITLEBAR_H], fill=C_TITLEBAR)
        d.line([0, TITLEBAR_H, WIDTH, TITLEBAR_H], fill=C_BORDER, width=1)
        for i, color in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
            cx, cy = 18 + i * 20, TITLEBAR_H // 2
            d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=color)
        title = "yry@mai-ai-saver: ~/hackathon2026"
        tw = d.textlength(title, font=self.font)
        d.text(((WIDTH - tw) / 2, TITLEBAR_H / 2), title, font=self.font,
               fill=C_DIM, anchor="lm")

        # 可见行（超出 MAX_LINES 自动上滚）
        visible = self.lines[-MAX_LINES:]
        for i, (text, color) in enumerate(visible):
            self._draw_mixed(d, PAD_X, PAD_Y + i * LINE_H, strip_emoji(text), color)

        # 正在输入的行 + 光标
        if self.current is not None:
            y = PAD_Y + len(visible) * LINE_H
            text, color = self.current
            self._draw_mixed(d, PAD_X, y, text, color)
            if self.cursor_on:
                cx = PAD_X + self._text_width(strip_emoji(text))
                d.rectangle([cx, y + 2, cx + FONT_SIZE * 0.6, y + FONT_SIZE + 2],
                            fill=C_CURSOR)
        return img

    def _snap(self, hold: int = 1) -> None:
        frame = self._render()
        for _ in range(hold):
            self.frames.append(frame)

    # ---- 动画原子操作 ----
    def blank(self, hold: int = 6) -> None:
        self._snap(hold)

    def type_command(self, cmd: str) -> None:
        prompt = "$ "
        typed = ""
        for ch in cmd:
            typed += ch
            self.current = (prompt + typed, C_BLUE)
            self.cursor_on = True
            self._snap(2)                        # 2 帧/字符 ≈ 6 字符/秒
        self.cursor_on = False
        self._snap(8)                            # 回车前停顿
        self.lines.append((prompt + typed, C_BLUE))
        self.current = None

    def print_line(self, text: str, color: str = C_TEXT, hold: int = 4) -> None:
        self.lines.append((text, color))
        self.current = ("$ ", C_GREEN)           # 光标驻留提示符
        self.cursor_on = True
        self._snap(hold)

    # ---- 保存 ----
    def save(self, path: Path, max_mb: float = 4.0) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        frames = [f.quantize(colors=96, method=Image.Quantize.MEDIANCUT)
                  for f in self.frames]
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=int(1000 / FPS), loop=0, optimize=True)
        size_mb = path.stat().st_size / 1024 / 1024
        print(f"[GIF] {len(frames)} 帧 / {size_mb:.2f} MB -> {path}")
        if size_mb > max_mb:
            print(f"[警告] 超过 {max_mb} MB，可降低 FPS 或精简输出行", file=sys.stderr)


def build_demo() -> TerminalGif:
    t = TerminalGif()
    t.blank(20)                                  # 片头停顿

    # 场景 1：aha moment —— 必点巨无霸自动升套餐
    t.type_command(SCENE1_CMD)
    for text, color in SCENE1_OUT:
        t.print_line(text, color)

    t.blank(10)

    # 场景 2：打工人午餐
    t.type_command(SCENE2_CMD)
    for text, color in SCENE2_OUT:
        t.print_line(text, color)

    t.blank(10)
    for text, color in ENDING:
        t.print_line(text, color, hold=6)

    t.blank(24)                                  # 结尾定格
    return t


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 README 演示 GIF")
    ap.add_argument("--out", default="docs/demo.gif", help="输出路径")
    args = ap.parse_args()

    t = build_demo()
    t.save(Path(args.out))

    # 交叉校验：GIF 文案与脚本真实输出保持一致（防止文案漂移）
    repo = Path(__file__).resolve().parent.parent
    real = subprocess.run(
        [sys.executable, str(repo / "scripts" / "price_optimizer.py"),
         "--budget", "50", "--must", "big_mac", "--top", "1"],
        capture_output=True, text=True, cwd=repo,
    )
    print("[校验] 脚本真实输出 TOP1：")
    for line in real.stdout.strip().splitlines():
        if "方案 1" in line or "巨无霸套餐" in line or "自动升级" in real.stderr:
            print("   " + line.strip())
    print("[校验] 升级提示：", "[提示]" in real.stderr or "自动升级" in real.stderr)


if __name__ == "__main__":
    main()
