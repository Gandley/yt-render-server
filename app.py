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

    # Wrap text so it fits vertically
    hook_wrapped = wrap_text(hook, 20)
    question_wrapped = wrap_text(question, 16)
    cta_wrapped = wrap_text(cta, 22)

    job_id = str(uuid.uuid4())
    input_path = os.path.join(TMP_DIR, f"{job_id}_input.mp4")
    output_name = f"{job_id}_rendered.mp4"
    output_path = os.path.join(OUT_DIR, output_name)

    # Temporary text files for drawtext
    hook_file = os.path.join(TMP_DIR, f"{job_id}_hook.txt")
    question_file = os.path.join(TMP_DIR, f"{job_id}_question.txt")
    cta_file = os.path.join(TMP_DIR, f"{job_id}_cta.txt")

    with open(hook_file, "w", encoding="utf-8") as f:
        f.write(hook_wrapped)

    with open(question_file, "w", encoding="utf-8") as f:
        f.write(question_wrapped)

    with open(cta_file, "w", encoding="utf-8") as f:
        f.write(cta_wrapped)

    # Download source video
    r = requests.get(video_url, stream=True, timeout=120)
    r.raise_for_status()
    with open(input_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)

    # FFmpeg drawtext using text files instead of inline text
    vf = (
        f"drawtext=textfile='{hook_file}':"
        f"x=(w-text_w)/2:y=h*0.08:"
        f"fontsize=38:fontcolor=white:"
        f"line_spacing=10:"
        f"shadowcolor=black:shadowx=3:shadowy=3,"
        f"drawtext=textfile='{question_file}':"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"fontsize=46:fontcolor=white:"
        f"line_spacing=12:"
        f"shadowcolor=black:shadowx=3:shadowy=3,"
        f"drawtext=textfile='{cta_file}':"
        f"x=(w-text_w)/2:y=h*0.80:"
        f"fontsize=34:fontcolor=white:"
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
