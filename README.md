# 播客转文字稿工具 🎙️

将播客 URL 自动转换为包含**内容大纲 + 带时间戳完整文字稿**的 Markdown 文件。

## 支持平台

| 平台 | 示例 URL |
|------|---------|
| 📺 Bilibili | `https://www.bilibili.com/video/BVxxxxxxx` |
| 🎧 小宇宙 | `https://www.xiaoyuzhoufm.com/episode/xxxxxxxx` |
| 以及其他 yt-dlp 支持的 1000+ 站点 | |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的 API 配置：

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=https://your-custom-api-endpoint.com/v1
CHAT_MODEL=gpt-4o
USE_WHISPER_API=true
WHISPER_MODEL=whisper-1
```

### 3. 运行

```bash
# Bilibili
python podcast_tool.py https://www.bilibili.com/video/BVxxxxxxx

# 小宇宙
python podcast_tool.py https://www.xiaoyuzhoufm.com/episode/xxxxxxxx

# 强制使用本地 Whisper（不走 API）
python podcast_tool.py <URL> --no-whisper-api
```

输出文件会保存在 `./output/` 目录下，格式为：`标题_YYYYMMDD.md`

## 输出格式

```markdown
# 播客标题

## 📋 基本信息
（平台、作者、时长、来源链接）

## 🗺️ 内容大纲
（带时间戳的章节划分 + 摘要）

## 💡 核心观点
（5~8 条精华观点）

## 📝 完整文字稿
（带时间戳的逐段文字）
```

## 注意事项

- 音频文件超过 25MB 时会自动切片处理，需要系统安装 `ffmpeg`
- 如果 API 不支持 Whisper，在 `.env` 中设置 `USE_WHISPER_API=false` 并安装本地 `whisper`
- 小宇宙部分节目可能有地区限制
