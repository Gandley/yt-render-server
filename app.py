from flask import Flask, request, jsonify, send_from_directory
import os
import uuid
import subprocess
import requests
import textwrap

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(BASE_DIR, "tmp")
OUT_DIR = os.path.join(BASE_DIR, "outputs")
os.makedirs(TMP_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

PORT = int(os.environ.get("PORT", 8080))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def esc(text: str) -> str:
    text = str(text or "")
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    text = text.replace(",", "\\,")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace("%", "\\%")
    text = text.replace("\n", "\\n")
    return text


def wrap_text(text: str, width: int) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    return "\n".join(textwrap.wrap(text, width=width))


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/outputs/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUT_DIR, filename)


@app.post("/render")
def render():
    data = request.get_json(force=True)

    video_url = data.get("video_url")
    hook = data.get("hook", "")
    question = data.get("question", "")
    cta = data.get("cta", "")

    if not video_url:
        return jsonify({"error": "video_url is required"}), 400

    # Wrap text into multiple lines first
    hook_wrapped = esc(wrap_text(hook, 20))
    question_wrapped = esc(wrap_text(question, 18))
    cta_wrapped = esc(wrap_text(cta, 22))

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

    # Smaller fonts + multi-line + safe margins
    vf = (
        f"drawtext=text='{hook_wrapped}':"
        f"x=(w-text_w)/2:y=h*0.10:"
        f"fontsize=42:fontcolor=white:"
        f"line_spacing=10:"
        f"shadowcolor=black:shadowx=3:shadowy=3:"

        f"drawtext=text='{question_wrapped}':"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"fontsize=50:fontcolor=white:"
        f"line_spacing=12:"
        f"shadowcolor=black:shadowx=3:shadowy=3:"

        f"drawtext=text='{cta_wrapped}':"
        f"x=(w-text_w)/2:y=h*0.78:"
        f"fontsize=38:fontcolor=white:"
        f"line_spacing=10:"
        f"shadowcolor=black:shadowx=3:shadowy=3"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "copy",
        output_path
    ]

    subprocess.run(cmd, check=True)

    if PUBLIC_BASE_URL:
        public_url = f"{PUBLIC_BASE_URL}/outputs/{output_name}"
    else:
        public_url = f"/outputs/{output_name}"

    return jsonify({
        "success": True,
        "video_url": public_url,
        "filename": output_name
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
