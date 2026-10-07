# 视频格式转换器

`src/video_converter.py` 是一个基于 Python 和 FFmpeg 的常见视频格式转换工具。
它提供图形界面，用户可以选择输入视频、输出格式、转换模式和保存位置，也可以通过命令行执行转换。

## 支持格式

- MP4
- WebM
- AVI
- MKV
- MOV

输入文件还可以是其他 FFmpeg 支持的视频格式，例如 FLV、WMV 和 M4V。

## 转换模式

- **快速**：转换速度较快，文件质量和大小适中。
- **平衡（推荐）**：在速度、画质和文件大小之间取得平衡。
- **高质量**：画质更好，但转换时间更长，文件通常更大。

## 环境要求

- Python 3.10 或更高版本
- Tkinter（图形界面需要）
- FFmpeg，并且 `ffmpeg` 已加入系统 `PATH`

本程序只使用 Python 标准库，不需要安装 Python 第三方包。

### 安装 FFmpeg

Ubuntu/Debian：

```bash
sudo apt update
sudo apt install ffmpeg
```

Windows 和 macOS 用户可以安装对应平台的 FFmpeg，并将其所在目录加入 `PATH`。

## 使用图形界面

在项目根目录运行：

```bash
python3 src/video_converter.py
```

然后按以下步骤操作：

1. 选择输入视频。
2. 选择目标格式。
3. 选择转换模式。
4. 选择输出文件位置，或使用自动生成的文件名。
5. 点击“开始转换”。

如果输出文件已经存在，程序会先询问是否覆盖。

## 使用命令行

```bash
python3 src/video_converter.py input.mp4 -f webm -o output.webm -m balanced
```

参数说明：

- `input.mp4`：输入视频路径。
- `-f` 或 `--format`：输出格式，可选 `mp4`、`webm`、`avi`、`mkv`、`mov`。
- `-o` 或 `--output`：输出文件路径；省略时自动生成 `原文件名_converted.格式`。
- `-m` 或 `--mode`：转换模式，可选 `fast`、`balanced`、`quality`，默认是 `balanced`。

例如，将 AVI 转换为高质量 MP4：

```bash
python3 src/video_converter.py input.avi -f mp4 -o output.mp4 -m quality
```

## 实现原理

Python 负责读取用户选择、检查输入和输出路径，并通过 `subprocess` 调用 FFmpeg。FFmpeg 负责解码原视频、使用目标格式兼容的编码器重新编码音视频，再封装为目标容器格式。

程序会根据输出格式选择合适的编码组合：

- MP4、MOV、MKV：H.264 视频和 AAC 音频
- WebM：VP9 视频和 Opus 音频
- AVI：MPEG-4 视频和 MP3 音频

转换任务在后台线程运行，因此图形界面不会因为转换过程而卡住。

## 常见问题

### 提示找不到 FFmpeg

请确认 FFmpeg 已安装，并在终端执行：

```bash
ffmpeg -version
```

如果系统找不到该命令，需要把 FFmpeg 的安装目录加入 `PATH`，然后重新打开终端或运行程序。

### 输出文件无法播放

请确认磁盘空间足够，并尝试使用“平衡”或“高质量”模式。某些特殊编码的视频可能需要先用 FFmpeg 转换为标准 H.264/AAC 格式。

## 文件结构

```text
src/
└── video_converter.py
```

## 许可证

本项目使用 MIT License。发布前请将根目录 `LICENSE` 文件中的
`[Your Name or Organization]` 替换为实际的作者或组织名称。
