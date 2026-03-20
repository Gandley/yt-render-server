from flask import Flask, request, jsonify, send_from_directory
import os
import uuid
import subprocess
import requests
import textwrap
from urllib.parse import urlparse, unquote

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, "tmp")
OUT_DIR = os.path.join(BASE_DIR, "outputs")
AUDIO_DIR = os.path.join(BASE_DIR, "public", "audio")

os.makedirs(TMP_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)

PORT = int(os.environ.get("PORT", 8080))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

FONT_FILE = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def clean_text(text: str) -> str:
    text = str(text or "").strip()
    text = text.replace("’", "'")
    text = text.replace("‘", "'")
    text = text.replace("“", '"')
    text = text.replace("”", '"')
    text = text.replace("—", "-")
    text = text.replace("–", "-")
    text = text.replace("…", "...")
    text = text.replace("\r", "")
    text = text.replace("\n", " ")

    allowed = []
    for ch in text:
        code = ord(ch)
        if 32 <= code <= 126:
            allowed.append(ch)

    return "".join(allowed)


def wrap_lines(text: str, width: int) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    return textwrap.wrap(text, width=width)


def build_block_filters(lines, prefix, start_y_expr, fontsize, line_gap, job_id):
    filters = []
    text_files = []

    for i, line in enumerate(lines):
        line_file = os.path.join(TMP_DIR, f"{job_id}_{prefix}_{i}.txt")
        with open(line_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(line)
        text_files.append(line_file)

        if i == 0:
            y_expr = start_y_expr
        else:
            y_expr = f"{start_y_expr}+{i}*{line_gap}"

        filters.append(
            f"drawtext=fontfile='{FONT_FILE}':"
            f"textfile='{line_file}':"
            f"x=(w-text_w)/2:"
            f"y={y_expr}:"
            f"fontsize={fontsize}:"
            f"fontcolor=white:"
            f"shadowcolor=black:shadowx=3:shadowy=3"
        )

    return filters, text_files


def get_public_url(filename: str) -> str:
    if PUBLIC_BASE_URL:
        return f"{PUBLIC_BASE_URL}/outputs/{filename}"
    return f"/outputs/{filename}"


def resolve_local_audio_path(audio_url: str) -> str | None:
    """
    If the audio URL points to this same app's /audio/... route,
    convert it to a local filesystem path instead of downloading it over HTTP.
    """
    if not audio_url:
        return None

    audio_url = str(audio_url).lstrip("=").strip()

    try:
        parsed = urlparse(audio_url)
        path = parsed.path or ""
    except Exception:
        return None

    if not path.startswith("/audio/"):
        return None

    filename = unquote(path.replace("/audio/", "", 1)).strip()
    if not filename:
        return None

    local_path = os.path.abspath(os.path.join(AUDIO_DIR, filename))
    audio_root = os.path.abspath(AUDIO_DIR)

    if not local_path.startswith(audio_root):
        return None

    if not os.path.exists(local_path):
        return None

    return local_path


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/outputs/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUT_DIR, filename)


@app.get("/audio/<path:filename>")
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)


@app.post("/render")
def render():
    data = request.get_json(force=True)

    video_url = data.get("video_url")
    hook = data.get("hook", "")
    question = data.get("question", "")
    cta = data.get("cta", "")

    if not video_url:
        return jsonify({"error": "video_url is required"}), 400

    video_url = str(video_url).lstrip("=").strip()

    hook_lines = wrap_lines(hook, 24)
    question_lines = wrap_lines(question, 18)
    cta_lines = wrap_lines(cta, 24)

    job_id = str(uuid.uuid4())
    input_path = os.path.join(TMP_DIR, f"{job_id}_input.mp4")
    output_name = f"{job_id}_rendered.mp4"
    output_path = os.path.join(OUT_DIR, output_name)

    r = requests.get(video_url, stream=True, timeout=120)
    r.raise_for_status()
    with open(input_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)

    filters = []
    temp_text_files = []

    hook_filters, hook_files = build_block_filters(
        lines=hook_lines,
        prefix="hook",
        start_y_expr="h*0.08",
        fontsize=38,
        line_gap=46,
        job_id=job_id
    )
    filters.extend(hook_filters)
    temp_text_files.extend(hook_files)

    question_filters, question_files = build_block_filters(
        lines=question_lines,
        prefix="question",
        start_y_expr="h*0.42",
        fontsize=44,
        line_gap=54,
        job_id=job_id
    )
    filters.extend(question_filters)
    temp_text_files.extend(question_files)

    cta_filters, cta_files = build_block_filters(
        lines=cta_lines,
        prefix="cta",
        start_y_expr="h*0.80",
        fontsize=32,
        line_gap=42,
        job_id=job_id
    )
    filters.extend(cta_filters)
    temp_text_files.extend(cta_files)

    vf = ",".join(filters)

    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        output_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        return jsonify({
            "success": False,
            "error": "ffmpeg failed",
            "stderr": result.stderr
        }), 500

    return jsonify({
        "success": True,
        "video_url": get_public_url(output_name),
        "filename": output_name
    })


@app.post("/merge-audio")
def merge_audio():
    data = request.get_json(force=True)

    video_url = str(data.get("video_url", "")).lstrip("=").strip()
    audio_url = str(data.get("audio_url", "")).lstrip("=").strip()

    if not video_url:
        return jsonify({"error": "video_url is required"}), 400

    if not audio_url:
        return jsonify({"error": "audio_url is required"}), 400

    job_id = str(uuid.uuid4())

    input_video_path = os.path.join(TMP_DIR, f"{job_id}_input_video.mp4")
    output_name = f"{job_id}_merged.mp4"
    output_path = os.path.join(OUT_DIR, output_name)

    cleanup_paths = [input_video_path]

    try:
        video_response = requests.get(video_url, stream=True, timeout=120)
        video_response.raise_for_status()
        with open(input_video_path, "wb") as f:
            for chunk in video_response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

        local_audio_path = resolve_local_audio_path(audio_url)

        if local_audio_path:
            audio_input = local_audio_path
        else:
            input_audio_path = os.path.join(TMP_DIR, f"{job_id}_input_audio.mp3")
            cleanup_paths.append(input_audio_path)

            audio_response = requests.get(audio_url, stream=True, timeout=120)
            audio_response.raise_for_status()
            with open(input_audio_path, "wb") as f:
                for chunk in audio_response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

            audio_input = input_audio_path

        cmd = [
            "ffmpeg",
            "-y",
            "-i", input_video_path,
            "-i", audio_input,
            "-filter_complex", "[1:a]volume=0.15,afade=t=in:st=0:d=0.5[a1]",
            "-map", "0:v:0",
            "-map", "[a1]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            return jsonify({
                "success": False,
                "error": "ffmpeg merge failed",
                "stderr": result.stderr
            }), 500

        return jsonify({
            "success": True,
            "video_url": get_public_url(output_name),
            "filename": output_name
        })

    except requests.RequestException as e:
        return jsonify({
            "success": False,
            "error": "download failed",
            "details": str(e),
            "video_url": video_url,
            "audio_url": audio_url
        }), 500

    finally:
        for path in cleanup_paths:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
