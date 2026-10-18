#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
麦麦省钱助手 · 视频旁白合成与混音（去 AI 味版）
=================================================
在基础 TTS 混音之上，做了三层"去 AI 味"处理：

1. 音色层：zh-CN-YunxiNeural（微软中文 neural 中自然度最高的男声之一）
   + SSML narration-relaxed（放松叙述风格，替换默认"播报腔"）
   + pitch +1Hz / rate +4%（偏离标准调，放慢语速增加说话感）
2. 后期层：highpass 90Hz 砍低频嗡、lowpass 13kHz 砍合成味超高频、
   compand 动态压缩（人声贴耳）、aecho 短混响（房间空间感）、
   volume 1.1（补偿压缩损失）
3. 文本层：台词口语化（"立省七块五"→"能省七块五"、"帮你算清楚"→"帮你算笔账"）

用法
----
    python3 tools/make_narration.py                        # 默认配置出全片
    python3 tools/make_narration.py --voice zh-CN-YunjianNeural   # 换激情男声
    python3 tools/make_narration.py --no-deai              # 跳过后期链（对比用）

依赖：edge-tts + ffmpeg
参赛作品：2026 麦当劳程序员创意开发大赛
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------- #
# 台词与时间轴（口语化定稿，与 NARRATION.md 保持一致）
# --------------------------------------------------------------------------- #
LINES = [
    (0.8,  "麦麦省钱助手——程序员节的午餐救星。"),
    (4.9,  "说“想吃巨无霸”，算法自动升档套餐，能省七块五。"),
    (10.8, "预算三十吃不到套餐？主食加饮料，给你安排明白。"),
    (16.8, "满减还是折扣？哪张券划算，帮你算笔账。"),
    (22.9, "把脑子留给代码，把午餐交给麦麦。"),
]
DEFAULT_VOICE = "zh-CN-YunxiNeural"
DEFAULT_RATE = "+4%"
DEFAULT_PITCH = "+1Hz"
# 注：edge-tts 7.x 的 Communicate 已不支持 SSML 直传（传入会被当作纯文本朗读），
# narration-relaxed 等多情感风格暂不可用；靠"阳光人设本音 + 慢语速 + 后期链"去 AI 味。
DEFAULT_STYLE = "none"

REPO = Path(__file__).resolve().parent.parent

# 去 AI 味后期链（见模块 docstring）
DEAI_CHAIN = (
    "highpass=f=90, "
    "lowpass=f=13000, "
    "compand=attacks=0.02:decays=0.15:points=-80/-80|-30/-25|-20/-15|0/-8:soft-knee=3, "
    "aecho=0.8:0.75:25|35:0.35|0.28, "
    "volume=1.1"
)


# --------------------------------------------------------------------------- #
# 1. TTS 合成（SSML 风格注入）
# --------------------------------------------------------------------------- #
def ssml_wrap(text: str, voice: str, style: str | None) -> str:
    if not style:
        return text
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="http://www.w3.org/2001/mstts" xml:lang="zh-CN">'
        f'<voice name="{voice}">'
        f'<mstts:express-as style="{style}">{text}</mstts:express-as>'
        "</voice></speak>"
    )


async def synth(text: str, out: Path, voice: str, rate: str,
                pitch: str, style: str | None) -> None:
    import edge_tts
    payload = ssml_wrap(text, voice, style)
    communicate = edge_tts.Communicate(payload, voice=voice, rate=rate, pitch=pitch)
    await communicate.save(str(out))


# --------------------------------------------------------------------------- #
# 2. 后期处理 + 时长测量
# --------------------------------------------------------------------------- #
def deai_process(src: Path, dst: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-af", DEAI_CHAIN, str(dst)],
        check=True,
    )


def duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


# --------------------------------------------------------------------------- #
# 3. 混音
# --------------------------------------------------------------------------- #
def mix(video: Path, clips: list[tuple[float, Path]], out: Path) -> None:
    inputs: list[str] = ["-i", str(video)]
    for _, clip in clips:
        inputs += ["-i", str(clip)]

    filters = []
    mix_labels = []
    for i, (start_ms, _) in enumerate(clips, start=1):
        filters.append(f"[{i}:a]adelay={start_ms}|{start_ms}[a{i}]")
        mix_labels.append(f"[a{i}]")
    filters.append("".join(mix_labels) +
                   f"amix=inputs={len(clips)}:normalize=0:dropout_transition=0[aout]")

    cmd = ["ffmpeg", "-y", *inputs,
           "-filter_complex", ";".join(filters),
           "-map", "0:v", "-map", "[aout]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2000:], file=sys.stderr)
        raise RuntimeError("ffmpeg 混音失败")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="合成旁白并混入视频（去 AI 味版）")
    ap.add_argument("--video", default=str(REPO / "docs" / "demo.mp4"))
    ap.add_argument("--out", default=str(REPO / "docs" / "demo_narrated.mp4"))
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--rate", default=DEFAULT_RATE)
    ap.add_argument("--pitch", default=DEFAULT_PITCH)
    ap.add_argument("--style", default=DEFAULT_STYLE,
                    help="SSML 风格，传 none 关闭")
    ap.add_argument("--no-deai", action="store_true", help="跳过去 AI 味后期链")
    args = ap.parse_args()

    video = Path(args.video)
    out = Path(args.out)
    if not video.exists():
        raise FileNotFoundError(f"视频不存在：{video}，请先运行 make_demo_video.py")
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"未找到 {tool}")

    style = None if args.style == "none" else args.style
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        clips: list[tuple[float, Path]] = []
        print(f"[TTS] voice={args.voice} style={style} rate={args.rate} "
              f"pitch={args.pitch} deai={'off' if args.no_deai else 'on'}")
        for i, (start, text) in enumerate(LINES, start=1):
            raw = tmp / f"line{i}_raw.mp3"
            proc = raw if args.no_deai else tmp / f"line{i}.mp3"
            asyncio.run(synth(text, raw, args.voice, args.rate, args.pitch, style))
            if not args.no_deai:
                deai_process(raw, proc)
            dur = duration(proc)
            end = start + dur
            print(f"  句{i}: {start:5.1f}s -> {end:5.1f}s ({dur:4.1f}s) 「{text}」")
            if i < len(LINES) and end > LINES[i][0]:
                print(f"  [警告] 句{i} 与句{i+1} 重叠 {end - LINES[i][0]:.1f}s",
                      file=sys.stderr)
            clips.append((int(start * 1000), proc))

        mix(video, clips, out)
        total = duration(out)
        size_mb = out.stat().st_size / 1024 / 1024
        print(f"[完成] {out}｜{total:.1f}s｜{size_mb:.2f} MB")


if __name__ == "__main__":
    main()
