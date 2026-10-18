#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦麦省钱助手 · README / 赛事演示视频生成器（MP4）
==================================================
用 Pillow 渲染 720p@30fps 终端打字机动画，经管道流式喂给 ffmpeg
（libopenh264 / H.264 + yuv420p）编码为通用 MP4。

与 make_demo_gif.py 的区别
--------------------------
- 输出 MP4 而非 GIF：用于大赛报名演示、社区发帖、B 站/视频号
- 720p / 30fps / 全片约 30 秒：比 GIF 更完整，含片头卡片、三个场景、片尾
- 管道流式编码：零中间文件，内存占用恒定

用法
----
    python3 tools/make_demo_video.py                 # 默认输出 docs/demo.mp4
    python3 tools/make_demo_video.py --out my.mp4   # 自定义输出路径
    python3 tools/make_demo_video.py --fps 60       # 提高帧率

依赖：Pillow + ffmpeg（需含 libopenh264 编码器）
参赛作品：2026 麦当劳程序员创意开发大赛
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------- #
# 画布常量（720p）
# --------------------------------------------------------------------------- #
WIDTH, HEIGHT = 1280, 720
FPS = 30
FONT_SIZE = 20
LINE_H = int(FONT_SIZE * 1.6)
PAD_X, PAD_Y = 24, 84
TERM_W, TERM_H = 1180, 540
TERM_X = (WIDTH - TERM_W) // 2
TERM_Y = (HEIGHT - TERM_H) // 2 + 8
TITLEBAR_H = 44
MAX_LINES = (TERM_H - TITLEBAR_H - 30) // LINE_H

C_BG = "#0d1117"
C_PAGE_BG = "#010409"
C_TITLEBAR = "#161b22"
C_BORDER = "#30363d"
C_TEXT = "#c9d1d9"
C_DIM = "#8b949e"
C_GREEN = "#7ee787"
C_YELLOW = "#e3b341"
C_BLUE = "#79c0ff"
C_CURSOR = "#58a6ff"

MONO_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_BOLD_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
CJK_FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
CJK_BOLD_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

EMOJI_MAP = {
    "🥇": "[1]", "🥈": "[2]", "🥉": "[3]",
    "🍔": "", "🍟": "", "🥤": "", "🍽️": "",
    "⚠️": "[!]", "✅": "[v]", "👑": "*", "🎟️": "",
    "🔌": "", "🤖": "", "✨": "", "🚀": "",
}

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
SCENE3_CMD = "python3 scripts/coupon_matcher.py --items big_mac fries_l coke_l"
SCENE3_OUT = [
    ("优惠券匹配｜订单 big_mac + fries_l + coke_l｜小计 ¥51.50", C_DIM),
    ("", C_TEXT),
    ("* 最优 满50减8｜实省 ¥8.00｜订单 ¥51.50 满 ¥50｜券后 ¥43.50", C_YELLOW),
    ("  备选 全场85折｜实省 ¥7.73｜订单 ¥51.50 × 0.85（封顶 ¥10）｜券后 ¥43.77", C_TEXT),
    ("", C_TEXT),
    (">>> 满减在临界点比折扣券更香，算法帮你算清楚", C_GREEN),
]


def strip_emoji(text: str) -> str:
    for k, v in EMOJI_MAP.items():
        text = text.replace(k, v)
    return text


def _char_w(ch: str, font: ImageFont.FreeTypeFont) -> float:
    return font.size * (1.0 if ord(ch) > 0x2E7F else 0.6)


def _draw_mixed(d: ImageDraw.ImageDraw, x: float, y: int, text: str,
                color: str, font: ImageFont.FreeTypeFont,
                font_cjk: ImageFont.FreeTypeFont) -> float:
    cx = x
    for ch in text:
        f = font_cjk if ord(ch) > 0x2E7F else font
        d.text((cx, y), ch, font=f, fill=color)
        cx += _char_w(ch, f)
    return cx


# --------------------------------------------------------------------------- #
# 终端渲染器（行式滚动 + 打字机）
# --------------------------------------------------------------------------- #
class TerminalScene:
    def __init__(self, fps: int = FPS):
        self.fps = fps
        self.font = ImageFont.truetype(MONO_FONT_PATH, FONT_SIZE)
        self.font_cjk = ImageFont.truetype(CJK_FONT_PATH, FONT_SIZE)
        self.lines: list[tuple[str, str]] = []
        self.current: tuple[str, str] | None = None
        self.cursor_on = True

    def _render(self) -> Image.Image:
        img = Image.new("RGB", (WIDTH, HEIGHT), C_PAGE_BG)
        d = ImageDraw.Draw(img)

        # 终端窗口（圆角 + 边框）
        d.rounded_rectangle([TERM_X, TERM_Y, TERM_X + TERM_W, TERM_Y + TERM_H],
                            radius=12, fill=C_BG, outline=C_BORDER, width=2)
        # 标题栏
        d.rounded_rectangle([TERM_X, TERM_Y, TERM_X + TERM_W, TERM_Y + TITLEBAR_H],
                            radius=12, fill=C_TITLEBAR)
        d.rectangle([TERM_X, TERM_Y + TITLEBAR_H - 12,
                     TERM_X + TERM_W, TERM_Y + TITLEBAR_H], fill=C_TITLEBAR)
        for i, color in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
            cx, cy = TERM_X + 26 + i * 26, TERM_Y + TITLEBAR_H // 2
            d.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=color)
        title = "yry@mai-ai-saver: ~/hackathon2026"
        d.text((TERM_X + TERM_W / 2, TERM_Y + TITLEBAR_H / 2), title,
               font=self.font, fill=C_DIM, anchor="mm")

        # 可见行（自动上滚）
        visible = self.lines[-MAX_LINES:]
        base_y = TERM_Y + TITLEBAR_H + 16
        for i, (text, color) in enumerate(visible):
            _draw_mixed(d, TERM_X + PAD_X, base_y + i * LINE_H,
                        strip_emoji(text), color, self.font, self.font_cjk)

        # 正在输入的行 + 光标
        if self.current is not None:
            y = base_y + len(visible) * LINE_H
            if y < TERM_Y + TERM_H - LINE_H // 2:
                text, color = self.current
                cx = _draw_mixed(d, TERM_X + PAD_X, y, text, color,
                                 self.font, self.font_cjk)
                if self.cursor_on:
                    d.rectangle([cx + 2, y + 3, cx + FONT_SIZE * 0.55, y + FONT_SIZE + 5],
                                fill=C_CURSOR)
        return img

    def type_command(self, cmd: str, emit) -> None:
        prompt = "$ "
        typed = ""
        for ch in cmd:
            typed += ch
            self.current = (prompt + typed, C_BLUE)
            self.cursor_on = True
            emit(self._render(), 1)
        self.cursor_on = False
        emit(self._render(), int(self.fps * 0.6))     # 回车前停顿
        self.lines.append((prompt + typed, C_BLUE))
        self.current = None

    def print_line(self, text: str, color: str, emit, hold: float = 0.5) -> None:
        self.lines.append((text, color))
        self.current = ("$ ", C_GREEN)
        self.cursor_on = True
        emit(self._render(), int(self.fps * hold))


# --------------------------------------------------------------------------- #
# 片头 / 片尾卡片（淡入动画）
# --------------------------------------------------------------------------- #
def _card_layer() -> Image.Image:
    """生成一张 RGBA 文字层，供淡入合成。"""
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f_title = ImageFont.truetype(CJK_BOLD_PATH, 72)
    f_sub = ImageFont.truetype(CJK_FONT_PATH, 26)
    f_small = ImageFont.truetype(CJK_FONT_PATH, 20)
    f_mono = ImageFont.truetype(MONO_FONT_PATH, 22)

    cx = WIDTH / 2
    y = 218
    d.text((cx, y), "麦麦省钱助手", font=f_title, fill=(230, 237, 243, 255), anchor="mm")
    d.text((cx, y + 66), "Mai-AI Saver", font=f_mono, fill=(139, 148, 158, 255), anchor="mm")
    d.text((cx, y + 128), "1024 程序员节的午餐救星", font=f_sub,
           fill=(126, 231, 135, 255), anchor="mm")
    d.text((cx, y + 168), "花最少的钱，吃最像样的一顿", font=f_sub,
           fill=(201, 209, 217, 255), anchor="mm")
    d.text((cx, y + 232), "基于麦当劳 MCP · 2026 麦当劳程序员创意开发大赛参赛作品",
           font=f_small, fill=(139, 148, 158, 255), anchor="mm")
    return layer


def _ending_layer() -> Image.Image:
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f_big = ImageFont.truetype(CJK_BOLD_PATH, 44)
    f_mid = ImageFont.truetype(CJK_FONT_PATH, 26)
    f_mono = ImageFont.truetype(MONO_FONT_PATH, 22)

    cx = WIDTH / 2
    y = 250
    d.text((cx, y), "把脑子留给代码，把午餐交给麦麦", font=f_big,
           fill=(230, 237, 243, 255), anchor="mm")
    d.text((cx, y + 70), "github.com/<your-id>/mai-ai-saver", font=f_mono,
           fill=(121, 192, 255, 255), anchor="mm")
    d.text((cx, y + 116), "觉得有点意思？求一个 Star ⭐", font=f_mid,
           fill=(126, 231, 135, 255), anchor="mm")
    return layer


def _fade_frames(layer: Image.Image, emit, fps: int,
                 fade_in: float = 0.8, hold: float = 1.6, fade_out: float = 0.5) -> None:
    """文字层淡入 → 停留 → 淡出，背景为纯色。"""
    bg = Image.new("RGB", (WIDTH, HEIGHT), C_PAGE_BG)
    n_in, n_hold, n_out = int(fps * fade_in), int(fps * hold), int(fps * fade_out)
    for i in range(n_in):                          # 淡入
        alpha = (i + 1) / n_in
        frame = bg.copy()
        frame.paste(layer, (0, 0), layer.split()[3].point(lambda a, al=alpha: int(a * al)))
        emit(frame, 1)
    full = bg.copy()                               # 完整显示的帧（停留用）
    full.paste(layer, (0, 0), layer.split()[3])
    for _ in range(n_hold):
        emit(full, 1)
    for i in range(n_out):                         # 淡出
        alpha = 1 - (i + 1) / n_out
        frame = bg.copy()
        frame.paste(layer, (0, 0), layer.split()[3].point(lambda a, al=alpha: int(a * al)))
        emit(frame, 1)


# --------------------------------------------------------------------------- #
# 编码管道
# --------------------------------------------------------------------------- #
class FfmpegPipe:
    def __init__(self, out: Path, fps: int):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("未找到 ffmpeg，请先安装")
        self.proc = subprocess.Popen(
            [ffmpeg, "-y",
             "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{WIDTH}x{HEIGHT}", "-r", str(fps),
             "-i", "-",
             "-c:v", "libopenh264", "-pix_fmt", "yuv420p",
             "-b:v", "3000k",
             "-movflags", "+faststart",
             str(out)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.count = 0

    def write(self, frame: Image.Image, hold: int = 1) -> None:
        data = frame.tobytes()
        for _ in range(hold):
            self.proc.stdin.write(data)
            self.count += 1

    def close(self) -> None:
        self.proc.stdin.close()
        if self.proc.wait() != 0:
            raise RuntimeError("ffmpeg 编码失败")
        print(f"[视频] 共 {self.count} 帧 / {self.count / FPS:.1f} 秒")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def build(out: Path, fps: int) -> None:
    pipe = FfmpegPipe(out, fps)
    emit = pipe.write

    # 片头
    _fade_frames(_card_layer(), emit, fps)

    # 场景 1：aha moment —— 必点巨无霸自动升套餐
    t = TerminalScene(fps)
    t.type_command(SCENE1_CMD, emit)
    for text, color in SCENE1_OUT:
        t.print_line(text, color, emit, hold=0.55)
    emit(t._render(), int(fps * 1.2))              # 场景间停顿

    # 场景 2：打工人午餐
    t2 = TerminalScene(fps)
    t2.type_command(SCENE2_CMD, emit)
    for text, color in SCENE2_OUT:
        t2.print_line(text, color, emit, hold=0.55)
    emit(t2._render(), int(fps * 1.0))

    # 场景 3：优惠券匹配
    t3 = TerminalScene(fps)
    t3.type_command(SCENE3_CMD, emit)
    for text, color in SCENE3_OUT:
        t3.print_line(text, color, emit, hold=0.55)
    emit(t3._render(), int(fps * 1.0))

    # 片尾
    _fade_frames(_ending_layer(), emit, fps)

    pipe.close()
    size_mb = out.stat().st_size / 1024 / 1024
    print(f"[完成] {out} ({size_mb:.2f} MB)")


def main() -> None:
    ap = argparse.ArgumentParser(description="生成参赛演示 MP4")
    ap.add_argument("--out", default="docs/demo.mp4", help="输出路径")
    ap.add_argument("--fps", type=int, default=FPS, help="帧率")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    build(out, args.fps)


if __name__ == "__main__":
    main()
