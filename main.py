import os
import sys
import threading
import uuid
import webbrowser

import cv2
import mido
import numpy as np
from flask import Flask, jsonify, render_template, request, send_from_directory
from PIL import Image

app = Flask(__name__)

# 判断是否为 PyInstaller 打包环境，调整基础路径
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(__file__)

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 临时存储上传文件路径，key = session_id
sessions: dict[str, dict] = {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    midi_file = request.files.get("midi")
    image_file = request.files.get("image")
    if not midi_file or not image_file:
        return jsonify({"error": "请同时上传 MIDI 文件和图片"}), 400

    session_id = uuid.uuid4().hex
    midi_path = os.path.join(UPLOAD_DIR, f"{session_id}.mid")
    image_path = os.path.join(UPLOAD_DIR, f"{session_id}_img{os.path.splitext(image_file.filename)[1]}")
    midi_file.save(midi_path)
    image_file.save(image_path)

    mid = mido.MidiFile(midi_path)
    tracks = []
    for i, track in enumerate(mid.tracks):
        note_count = sum(
            1 for msg in track if msg.type == "note_on" and msg.velocity > 0
        )
        tracks.append({"index": i, "name": track.name or f"Track {i}", "notes": note_count})

    sessions[session_id] = {"midi": midi_path, "image": image_path}
    return jsonify({"session_id": session_id, "tracks": tracks})


def extract_note_times(mid: mido.MidiFile, track_index: int) -> list[float]:
    """提取指定轨道中 note_on 事件的绝对时间（秒）。"""
    ticks_per_beat = mid.ticks_per_beat

    # 先从所有轨道收集 tempo 变化（通常在 track 0）
    tempo_map: list[tuple[int, int]] = []  # (abs_tick, tempo)
    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "set_tempo":
                tempo_map.append((abs_tick, msg.tempo))
    tempo_map.sort(key=lambda x: x[0])

    def ticks_to_seconds(target_tick: int) -> float:
        """将绝对 tick 转换为秒，考虑 tempo 变化。"""
        current_tempo = 500000
        prev_tick = 0
        elapsed = 0.0
        for map_tick, map_tempo in tempo_map:
            if map_tick >= target_tick:
                break
            elapsed += (map_tick - prev_tick) * current_tempo / (ticks_per_beat * 1_000_000)
            current_tempo = map_tempo
            prev_tick = map_tick
        elapsed += (target_tick - prev_tick) * current_tempo / (ticks_per_beat * 1_000_000)
        return elapsed

    target_track = mid.tracks[track_index]
    note_times: list[float] = []
    abs_tick = 0
    for msg in target_track:
        abs_tick += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            note_times.append(ticks_to_seconds(abs_tick))

    return note_times


def hex_to_bgr(hex_color: str) -> tuple[int, int, int]:
    """将 #RRGGBB 转换为 OpenCV BGR 元组。"""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return (b, g, r)


def generate_video(
    midi_path: str,
    image_path: str,
    track_index: int,
    max_scale: float = 2.0,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    bg_color: str = "#00ff00",
    flip: bool = True,
) -> str:
    """根据 MIDI 音符生成缩放动画视频。

    每个音符触发图片从小（1x）到大（max_scale）的缩放动画，
    可选在每个音符处水平翻转图片。
    图片居中放置在指定背景色的画布上。
    """
    mid = mido.MidiFile(midi_path)
    note_times = extract_note_times(mid, track_index)

    if not note_times:
        raise ValueError("所选轨道没有音符事件")

    img = Image.open(image_path)
    if img.mode == "RGBA":
        img = img.convert("RGB")

    img_array = np.array(img)
    img_array = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
    orig_h, orig_w = img_array.shape[:2]

    # 计算基础尺寸：在 max_scale 时图片恰好填满画布
    scale_fit = min(width / orig_w, height / orig_h) / max_scale
    base_w = int(orig_w * scale_fit)
    base_h = int(orig_h * scale_fit)

    # 预缩放到基础尺寸及其翻转版本
    img_base = cv2.resize(img_array, (base_w, base_h), interpolation=cv2.INTER_AREA)
    img_base_flipped = cv2.flip(img_base, 1)

    bgr = hex_to_bgr(bg_color)
    bg_frame = np.full((height, width, 3), bgr, dtype=np.uint8)

    # 视频时长：从 0 到最后一个音符 + 1 秒缓冲
    duration = note_times[-1] + 1.0
    total_frames = int(duration * fps)

    output_name = f"{uuid.uuid4().hex}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_name)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

    for frame_no in range(total_frames):
        current_time = frame_no / fps

        # 在第一个音符之前，保持最小缩放，音符计数为 0
        if current_time < note_times[0]:
            progress = 0.0
            note_count = 0
        else:
            # 找到当前所处的音符区间
            idx = 0
            for i in range(len(note_times) - 1):
                if note_times[i + 1] > current_time:
                    idx = i
                    break
            else:
                idx = len(note_times) - 1

            note_count = idx + 1  # 已经过的音符数量（从第 1 个开始）

            if idx < len(note_times) - 1:
                start_t = note_times[idx]
                end_t = note_times[idx + 1]
                progress = (current_time - start_t) / (end_t - start_t)
                progress = max(0.0, min(1.0, progress))
            else:
                progress = 1.0

        # 选择正常或翻转的基础图（奇数音符翻转）
        if flip and note_count % 2 == 1:
            src_img = img_base_flipped
        else:
            src_img = img_base

        # 缩放因子从 1.0 到 max_scale
        current_scale = 1.0 + (max_scale - 1.0) * progress
        disp_w = int(base_w * current_scale)
        disp_h = int(base_h * current_scale)

        if disp_w < 1 or disp_h < 1:
            writer.write(bg_frame)
            continue

        img_scaled = cv2.resize(src_img, (disp_w, disp_h), interpolation=cv2.INTER_LINEAR)

        frame = bg_frame.copy()

        # 居中放置
        x_off = (width - disp_w) // 2
        y_off = (height - disp_h) // 2

        # 处理图片超出画布的情况（裁剪）
        src_x = max(0, -x_off)
        src_y = max(0, -y_off)
        dst_x = max(0, x_off)
        dst_y = max(0, y_off)
        copy_w = min(disp_w - src_x, width - dst_x)
        copy_h = min(disp_h - src_y, height - dst_y)

        if copy_w > 0 and copy_h > 0:
            frame[dst_y:dst_y + copy_h, dst_x:dst_x + copy_w] = \
                img_scaled[src_y:src_y + copy_h, src_x:src_x + copy_w]

        writer.write(frame)

    writer.release()
    return output_name


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json()
    session_id = data.get("session_id")
    track_index = data.get("track_index")

    if session_id not in sessions:
        return jsonify({"error": "会话不存在，请重新上传文件"}), 400

    max_scale = float(data.get("max_scale", 2.0))
    resolution = data.get("resolution", "1920x1080")
    fps = int(data.get("fps", 30))
    bg_color = data.get("bg_color", "#00ff00")
    flip = bool(data.get("flip", True))

    try:
        w, h = map(int, resolution.split("x"))
    except (ValueError, AttributeError):
        w, h = 1920, 1080

    session = sessions[session_id]
    try:
        filename = generate_video(
            session["midi"], session["image"], track_index,
            max_scale=max_scale, width=w, height=h, fps=fps,
            bg_color=bg_color, flip=flip,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"filename": filename})


@app.route("/download/<filename>")
def download(filename):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    port = 5000
    url = f"http://127.0.0.1:{port}"

    # 延迟 1.5 秒后自动打开浏览器
    threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    print(f" * 浏览器将自动打开 {url}")

    app.run(port=port)
