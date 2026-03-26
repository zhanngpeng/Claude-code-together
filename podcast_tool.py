#!/usr/bin/env python3
"""
播客转文字稿工具
支持平台：Bilibili、小宇宙，以及 yt-dlp 支持的其他平台
输出格式：Markdown（大纲 + 完整文字稿）
"""

import os
import re
import sys
import json
import math
import argparse
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime

import requests

from dotenv import load_dotenv
from openai import OpenAI
from tqdm import tqdm

load_dotenv()

# ─── 配置 ───────────────────────────────────────────────────────────────────

API_KEY       = os.getenv("OPENAI_API_KEY", "")
BASE_URL      = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
CHAT_MODEL    = os.getenv("CHAT_MODEL", "gpt-4o")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")
USE_WHISPER_API = os.getenv("USE_WHISPER_API", "true").lower() == "true"
OUTPUT_DIR    = Path(os.getenv("OUTPUT_DIR", "./output"))

MAX_CHUNK_BYTES = 24 * 1024 * 1024  # Whisper API 限制 25MB，留 1MB 余量


# ─── 客户端 ─────────────────────────────────────────────────────────────────

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)


# ─── Step 1：提取音频 ────────────────────────────────────────────────────────

def is_xiaoyuzhou(url: str) -> bool:
    return "xiaoyuzhoufm.com" in url


def extract_xiaoyuzhou(url: str, tmpdir: str) -> tuple[str, dict]:
    """
    小宇宙专用提取器：解析 Next.js __NEXT_DATA__ 获取音频直链并下载
    """
    print("🎵 检测到小宇宙链接，使用专用提取器……")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
    }
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()

    # 从 __NEXT_DATA__ 中提取 JSON
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', resp.text, re.S)
    if not m:
        raise RuntimeError("无法解析小宇宙页面，可能页面结构已变化。")

    data = json.loads(m.group(1))

    # 尝试多个可能的路径定位音频 URL
    episode = None
    try:
        # 常见路径：pageProps.episode 或 pageProps.data.episode
        props = data["props"]["pageProps"]
        episode = props.get("episode") or props.get("data", {}).get("episode")
    except (KeyError, TypeError):
        pass

    if not episode:
        # 兜底：全文搜索 enclosureUrl
        raw = m.group(1)
        enc = re.search(r'"enclosureUrl"\s*:\s*"([^"]+)"', raw)
        med = re.search(r'"mediaKey"\s*:\s*"([^"]+)"', raw)
        title_m = re.search(r'"title"\s*:\s*"([^"]+)"', raw)
        audio_url  = enc.group(1) if enc else (med.group(1) if med else None)
        title = title_m.group(1) if title_m else "未知标题"
        duration = 0
    else:
        audio_url = episode.get("enclosureUrl") or episode.get("mediaKey")
        title     = episode.get("title", "未知标题")
        duration  = episode.get("duration", 0)

    if not audio_url:
        raise RuntimeError("未能从小宇宙页面提取音频地址，请检查链接或稍后重试。")

    # 下载音频
    print(f"⬇️  下载音频中……   {audio_url[:80]}…")
    audio_path = os.path.join(tmpdir, "audio.mp3")
    with requests.get(audio_url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        with open(audio_path, "wb") as f, tqdm(
            total=total, unit="B", unit_scale=True, desc="下载进度"
        ) as bar:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                bar.update(len(chunk))

    size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    print(f"✅ 音频下载完成（{size_mb:.1f} MB）")

    meta = {
        "title": title,
        "uploader": episode.get("podcast", {}).get("title", "") if episode else "",
        "duration": duration,
        "webpage_url": url,
        "extractor_key": "小宇宙",
    }
    return audio_path, meta


def extract_via_ytdlp(url: str, tmpdir: str) -> tuple[str, dict]:
    """通用：使用 yt-dlp 下载音频"""
    audio_template = os.path.join(tmpdir, "audio.%(ext)s")

    # 先获取元数据
    result = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-playlist", url],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp 获取元数据失败：\n{result.stderr}")
    meta = json.loads(result.stdout.strip().split("\n")[-1])

    # 下载音频
    print("⬇️  下载音频中……")
    dl = subprocess.run(
        ["yt-dlp", "--no-playlist", "-x",
         "--audio-format", "mp3", "--audio-quality", "128K",
         "-o", audio_template, url],
        capture_output=True, text=True
    )
    if dl.returncode != 0:
        raise RuntimeError(f"yt-dlp 下载失败：\n{dl.stderr}")

    audio_files = list(Path(tmpdir).glob("audio.*"))
    if not audio_files:
        raise FileNotFoundError("未找到下载的音频文件")
    audio_path = str(audio_files[0])
    size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    print(f"✅ 音频下载完成（{size_mb:.1f} MB）")
    return audio_path, meta


def extract_audio(url: str, tmpdir: str) -> tuple[str, dict]:
    """
    根据 URL 类型选择合适的提取方式，返回 (音频文件路径, 元数据字典)
    """
    print(f"\n🎙️  正在提取音频：{url}")
    if is_xiaoyuzhou(url):
        return extract_xiaoyuzhou(url, tmpdir)
    return extract_via_ytdlp(url, tmpdir)


# ─── Step 2：切片（超过 25MB 时分段） ───────────────────────────────────────

def split_audio_if_needed(audio_path: str, tmpdir: str) -> list[str]:
    """
    如果音频文件超过 API 限制，用 ffmpeg 分割为多个片段
    """
    file_size = os.path.getsize(audio_path)
    if file_size <= MAX_CHUNK_BYTES:
        return [audio_path]

    print(f"⚙️  文件较大（{file_size / 1024 / 1024:.1f} MB），自动切割……")

    # 获取总时长
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", audio_path],
        capture_output=True, text=True
    )
    duration = float(json.loads(probe.stdout)["format"]["duration"])

    # 按 20 分钟一段切割
    chunk_duration = 1200  # 秒
    num_chunks = math.ceil(duration / chunk_duration)
    chunks = []

    for i in range(num_chunks):
        start = i * chunk_duration
        out_path = os.path.join(tmpdir, f"chunk_{i:03d}.mp3")
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(start),
            "-t", str(chunk_duration),
            "-i", audio_path,
            "-acodec", "libmp3lame", "-ab", "128k",
            out_path
        ], capture_output=True)
        chunks.append(out_path)
        print(f"  片段 {i+1}/{num_chunks} 就绪")

    return chunks


# ─── Step 3：语音转文字 ──────────────────────────────────────────────────────

def transcribe_via_api(audio_path: str) -> str:
    """调用 Whisper API 转写单个音频文件"""
    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model=WHISPER_MODEL,
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )
    # verbose_json 返回带时间戳的 segments
    if hasattr(response, "segments") and response.segments:
        lines = []
        for seg in response.segments:
            ts = int(seg.get("start", 0))
            h, rem = divmod(ts, 3600)
            m, s = divmod(rem, 60)
            timestamp = f"[{h:02d}:{m:02d}:{s:02d}]" if h else f"[{m:02d}:{s:02d}]"
            lines.append(f"{timestamp} {seg['text'].strip()}")
        return "\n".join(lines)
    return response.text


def transcribe_local(audio_path: str) -> str:
    """降级方案：调用本地 whisper CLI"""
    print("  📍 使用本地 Whisper（需已安装 `whisper` 包）")
    result = subprocess.run(
        ["whisper", audio_path, "--language", "zh", "--output_format", "json"],
        capture_output=True, text=True, cwd=os.path.dirname(audio_path)
    )
    if result.returncode != 0:
        raise RuntimeError(f"本地 Whisper 失败：{result.stderr}")
    json_path = audio_path.replace(".mp3", ".json")
    with open(json_path, "r") as f:
        data = json.load(f)
    lines = []
    for seg in data.get("segments", []):
        ts = int(seg["start"])
        m, s = divmod(ts, 60)
        lines.append(f"[{m:02d}:{s:02d}] {seg['text'].strip()}")
    return "\n".join(lines)


def transcribe(chunks: list[str]) -> str:
    """转写所有片段并合并"""
    print(f"\n📝 语音转文字（共 {len(chunks)} 个片段）……")
    all_text = []

    for i, chunk in enumerate(tqdm(chunks, desc="转写进度")):
        try:
            if USE_WHISPER_API:
                text = transcribe_via_api(chunk)
            else:
                text = transcribe_local(chunk)
        except Exception as e:
            print(f"\n  ⚠️  Whisper API 失败，尝试本地降级：{e}")
            text = transcribe_local(chunk)
        all_text.append(text)

    return "\n\n".join(all_text)


# ─── Step 4：生成大纲 ────────────────────────────────────────────────────────

OUTLINE_PROMPT = """你是一位专业的内容编辑。以下是一期播客节目的完整文字稿。

请你完成以下任务，并**严格按照 Markdown 格式**输出：

## 🗺️ 内容大纲

按时间顺序，将内容分为 5~10 个章节，每个章节包含：
- 带时间戳的标题（如 `### [00:05] 嘉宾介绍`）
- 2~3 句话的内容摘要

## 💡 核心观点

提炼 5~8 个最有价值的观点或金句，每条一行，用 `- ` 开头。

---
文字稿：

{transcript}
"""


def generate_outline(transcript: str, title: str) -> str:
    """调用 LLM 生成大纲和核心观点"""
    print("\n🤖 正在生成内容大纲……")

    # 如果文字稿太长，截取前 12 万字符（约 6 万 token）
    truncated = transcript[:120000]
    if len(transcript) > 120000:
        truncated += "\n\n[……文字稿较长，以上为前半部分摘要依据……]"

    response = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": "你是一位专业的播客内容编辑，擅长中文内容整理与结构化输出。"},
            {"role": "user", "content": OUTLINE_PROMPT.format(transcript=truncated)},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content


# ─── Step 5：输出 Markdown ───────────────────────────────────────────────────

def safe_filename(name: str) -> str:
    """将标题转为合法文件名"""
    for ch in r'\/:*?"<>|':
        name = name.replace(ch, "_")
    return name[:80].strip()


def build_markdown(meta: dict, outline: str, transcript: str) -> str:
    title    = meta.get("title", "未知标题")
    uploader = meta.get("uploader", meta.get("channel", "未知作者"))
    duration = meta.get("duration", 0)
    webpage  = meta.get("webpage_url", "")
    platform = meta.get("extractor_key", "")
    dur_str  = f"{duration // 60} 分 {duration % 60} 秒" if duration else "未知"

    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    md = f"""# {title}

## 📋 基本信息

| 项目 | 内容 |
|------|------|
| 平台 | {platform} |
| 作者 | {uploader} |
| 时长 | {dur_str} |
| 来源 | [{webpage}]({webpage}) |
| 生成时间 | {today} |

---

{outline}

---

## 📝 完整文字稿

{transcript}
"""
    return md


def save_output(md: str, title: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str  = datetime.now().strftime("%Y%m%d")
    filename  = f"{safe_filename(title)}_{date_str}.md"
    out_path  = OUTPUT_DIR / filename
    out_path.write_text(md, encoding="utf-8")
    return out_path


# ─── 主入口 ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="播客 URL → Markdown 文字稿 + 大纲生成工具"
    )
    parser.add_argument("url", help="播客 URL（支持 Bilibili、小宇宙等）")
    parser.add_argument(
        "--no-whisper-api",
        action="store_true",
        help="强制使用本地 Whisper（不走 API）",
    )
    args = parser.parse_args()

    global USE_WHISPER_API
    if args.no_whisper_api:
        USE_WHISPER_API = False

    if not API_KEY:
        print("❌ 未检测到 OPENAI_API_KEY，请在 .env 文件中配置。")
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. 提取音频
        audio_path, meta = extract_audio(args.url, tmpdir)

        # 2. 切片
        chunks = split_audio_if_needed(audio_path, tmpdir)

        # 3. 转写
        transcript = transcribe(chunks)

        # 4. 生成大纲
        outline = generate_outline(transcript, meta.get("title", ""))

        # 5. 组装并保存
        md = build_markdown(meta, outline, transcript)
        out_path = save_output(md, meta.get("title", "podcast"))

    print(f"\n✅ 完成！文件已保存至：{out_path.resolve()}")


if __name__ == "__main__":
    main()
