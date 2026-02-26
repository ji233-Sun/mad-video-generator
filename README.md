# MAD Video Generator

根据 MIDI 音符节奏生成图片缩放动画视频。每读取到一个音符，图片从小变大，产生跟随节拍的缩放脉冲效果。

## 功能

- 上传 MIDI 文件和图片，选择轨道后生成视频
- 每个音符触发一次"由小变大"的缩放动画
- 可自定义：
  - **最大缩放倍数** — 默认 2 倍
  - **分辨率** — 720p / 1080p / 2K / 4K，默认 1080p
  - **帧率** — 默认 30 FPS
  - **背景颜色** — 默认绿色 (`#00ff00`)
- 启动后自动打开浏览器

## 快速开始

### 从 Release 下载

前往 [Releases](https://github.com/ji233-Sun/mad-video-generator/releases) 下载对应平台的可执行文件，双击运行即可。

### 从源码运行

```bash
# 克隆仓库
git clone https://github.com/ji233-Sun/mad-video-generator.git
cd mad-video-generator

# 安装依赖（需要 Python 3.13+）
pip install flask mido Pillow opencv-python

# 启动
python main.py
```

浏览器会自动打开 `http://127.0.0.1:5000`。

## 使用步骤

1. 上传 MIDI 文件和图片
2. 选择包含音符的轨道
3. 调整视频参数（缩放倍数、分辨率、帧率、背景色）
4. 点击"生成视频"
5. 下载生成的 MP4 文件

## 构建独立程序

```bash
pip install pyinstaller
pyinstaller --onefile --name mad --add-data "templates:templates" main.py
```

产物位于 `dist/mad`。

## License

MIT
