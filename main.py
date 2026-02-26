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

FPS = 30


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
    tempo = 500000  # 默认 120 BPM
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


def generate_video(midi_path: str, image_path: str, track_index: int) -> str:
    """生成翻转视频，返回输出文件名。"""
    mid = mido.MidiFile(midi_path)
    note_times = extract_note_times(mid, track_index)

    if not note_times:
        raise ValueError("所选轨道没有音符事件")

    img = Image.open(image_path)
    if img.mode == "RGBA":
        img = img.convert("RGB")

    img_normal = np.array(img)
    img_normal = cv2.cvtColor(img_normal, cv2.COLOR_RGB2BGR)
    img_flipped = cv2.flip(img_normal, 1)  # 水平翻转

    h, w = img_normal.shape[:2]

    # 视频时长：从 0 到最后一个音符 + 1 秒缓冲
    duration = note_times[-1] + 1.0
    total_frames = int(duration * FPS)

    output_name = f"{uuid.uuid4().hex}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_name)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, FPS, (w, h))

    flipped = False
    note_idx = 0

    for frame_no in range(total_frames):
        current_time = frame_no / FPS
        # 检查是否有音符在当前帧触发
        while note_idx < len(note_times) and note_times[note_idx] <= current_time:
            flipped = not flipped
            note_idx += 1
        writer.write(img_flipped if flipped else img_normal)

    writer.release()
    return output_name


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json()
    session_id = data.get("session_id")
    track_index = data.get("track_index")

    if session_id not in sessions:
        return jsonify({"error": "会话不存在，请重新上传文件"}), 400

    session = sessions[session_id]
    try:
        filename = generate_video(session["midi"], session["image"], track_index)
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
