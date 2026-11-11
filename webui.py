"""A small, dependency-free web UI for separating vocals with Demucs.

Run from the repository root with ``python webui.py`` and open
http://127.0.0.1:7860 in a browser.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import uuid
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOST = "127.0.0.1"
PORT = 7860
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"}
JOBS_ROOT = Path(tempfile.gettempdir()) / "demucs-webui"
JOBS_ROOT.mkdir(parents=True, exist_ok=True)

# Keep completed files available for playback while the server is running.
JOBS: dict[str, Path] = {}

PAGE = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Demucs Vocal Separator</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
           background: #10131d; color: #eef0ff; }
    main { width: min(760px, calc(100% - 32px)); padding: 32px 0; }
    h1 { margin-bottom: 8px; }
    .muted { color: #a9afc5; }
    .drop { margin: 24px 0; padding: 32px 18px; text-align: center;
            border: 2px dashed #737b9b; border-radius: 16px; background: #191e2d;
            cursor: pointer; transition: .15s ease; }
    .drop.active { border-color: #9c8cff; background: #24233b; }
    .drop strong { display: block; font-size: 1.15rem; margin-bottom: 6px; }
    input[type=file] { display: none; }
    button { border: 0; border-radius: 10px; padding: 12px 20px; color: white;
             background: #6754e8; font-size: 1rem; font-weight: 650; cursor: pointer; }
    button:disabled { opacity: .55; cursor: wait; }
    .players { display: grid; gap: 16px; margin-top: 26px; }
    .card { padding: 18px; border-radius: 14px; background: #191e2d; }
    .card h2 { font-size: 1rem; margin: 0 0 12px; }
    audio { display: block; width: 100%; }
    #status { min-height: 1.5em; margin-top: 14px; }
    @media (min-width: 650px) { .players { grid-template-columns: repeat(2, 1fr); } }
  </style>
</head>
<body>
<main>
  <h1>Demucs Vocal Separator</h1>
  <p class="muted">অডিও ফাইল ড্র্যাগ-এন্ড-ড্রপ করুন, শুনুন, তারপর vocals আলাদা করতে Submit চাপুন।</p>
  <form id="form">
    <label class="drop" id="drop" for="audioFile">
      <strong>অডিও এখানে ড্রপ করুন</strong>
      <span class="muted" id="fileName">অথবা ক্লিক করে একটি ফাইল বেছে নিন (সর্বোচ্চ 100 MB)</span>
    </label>
    <input id="audioFile" name="audio" type="file" accept="audio/*,.mp3,.wav,.flac,.ogg,.m4a,.aac,.wma" required>
    <div class="card">
      <h2>আপনার অডিও</h2>
      <audio id="inputPlayer" controls></audio>
    </div>
    <p><button id="submit" type="submit">Submit</button></p>
  </form>
  <p id="status" class="muted" role="status" aria-live="polite"></p>
  <section class="players" aria-label="Separated audio">
    <div class="card"><h2>Vocals</h2><audio id="vocalsPlayer" controls></audio></div>
    <div class="card"><h2>No vocals (accompaniment)</h2><audio id="noVocalsPlayer" controls></audio></div>
  </section>
</main>
<script>
  const form = document.querySelector('#form');
  const input = document.querySelector('#audioFile');
  const drop = document.querySelector('#drop');
  const status = document.querySelector('#status');
  const submit = document.querySelector('#submit');
  let inputUrl;

  function selectFile(file) {
    if (!file) return;
    input.files = (() => { const data = new DataTransfer(); data.items.add(file); return data.files; })();
    document.querySelector('#fileName').textContent = file.name;
    if (inputUrl) URL.revokeObjectURL(inputUrl);
    inputUrl = URL.createObjectURL(file);
    document.querySelector('#inputPlayer').src = inputUrl;
    document.querySelector('#vocalsPlayer').removeAttribute('src');
    document.querySelector('#noVocalsPlayer').removeAttribute('src');
    status.textContent = '';
  }
  input.addEventListener('change', () => selectFile(input.files[0]));
  for (const eventName of ['dragenter', 'dragover']) {
    drop.addEventListener(eventName, event => { event.preventDefault(); drop.classList.add('active'); });
  }
  for (const eventName of ['dragleave', 'drop']) {
    drop.addEventListener(eventName, event => { event.preventDefault(); drop.classList.remove('active'); });
  }
  drop.addEventListener('drop', event => selectFile(event.dataTransfer.files[0]));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const file = input.files[0];
    if (!file) { status.textContent = 'আগে একটি অডিও ফাইল নির্বাচন করুন।'; return; }
    if (file.size > 100 * 1024 * 1024) { status.textContent = 'ফাইলটি 100 MB-এর চেয়ে বড়।'; return; }
    submit.disabled = true;
    status.textContent = 'অডিও প্রসেস হচ্ছে—ফাইলের দৈর্ঘ্য ও কম্পিউটারের ক্ষমতা অনুযায়ী সময় লাগতে পারে…';
    try {
      const response = await fetch('/separate', { method: 'POST', body: new FormData(form) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'প্রসেসিং ব্যর্থ হয়েছে।');
      document.querySelector('#vocalsPlayer').src = result.vocals;
      document.querySelector('#noVocalsPlayer').src = result.no_vocals;
      status.textContent = 'সম্পন্ন! নিচের প্লেয়ারগুলোতে ফলাফল শুনুন।';
    } catch (error) {
      status.textContent = error.message || 'একটি ত্রুটি হয়েছে।';
    } finally { submit.disabled = false; }
  });
</script>
</body>
</html>'''


class WebUIHandler(BaseHTTPRequestHandler):
    server_version = "DemucsWebUI/1.0"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        match = re.fullmatch(r"/audio/([0-9a-f]{32})/(vocals|no_vocals)\.wav", path)
        if match:
            job_id, stem = match.groups()
            job_dir = JOBS.get(job_id)
            audio_path = job_dir / f"{stem}.wav" if job_dir else None
            if audio_path and audio_path.is_file():
                self._send_file(audio_path)
                return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/separate":
            self.send_error(404, "Not found")
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "Invalid request size."})
            return
        if content_length <= 0 or content_length > MAX_UPLOAD_BYTES + 1024 * 1024:
            self._send_json(413, {"error": "ফাইল খালি অথবা 100 MB সীমার চেয়ে বড়।"})
            return

        try:
            raw = self.rfile.read(content_length)
            content_type = self.headers.get("Content-Type", "")
            message = BytesParser(policy=policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + raw
            )
            uploaded = next(
                (part for part in message.iter_parts()
                 if part.get_param("name", header="content-disposition") == "audio"),
                None,
            )
            if uploaded is None or not uploaded.get_filename():
                self._send_json(400, {"error": "একটি অডিও ফাইল নির্বাচন করুন।"})
                return
            extension = Path(uploaded.get_filename()).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                self._send_json(400, {"error": "এই অডিও ফরম্যাটটি সমর্থিত নয়।"})
                return
            audio_bytes = uploaded.get_payload(decode=True)
            if not audio_bytes or len(audio_bytes) > MAX_UPLOAD_BYTES:
                self._send_json(413, {"error": "ফাইল খালি অথবা 100 MB সীমার চেয়ে বড়।"})
                return

            job_id = uuid.uuid4().hex
            job_root = JOBS_ROOT / job_id
            job_root.mkdir(mode=0o700)
            input_path = job_root / f"input{extension}"
            input_path.write_bytes(audio_bytes)
            output_root = job_root / "output"
            command = [
                sys.executable, "-m", "demucs.separate",
                "--two-stems", "vocals",
                "--out", str(output_root),
                "--filename", "{stem}.{ext}",
                str(input_path),
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=Path(__file__).resolve().parent,
                    capture_output=True,
                    text=True,
                    timeout=3600,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                self._send_json(504, {"error": "প্রসেসিংয়ে এক ঘণ্টার বেশি সময় লেগেছে; অনুগ্রহ করে আবার চেষ্টা করুন।"})
                return

            result_dir = output_root / "htdemucs" / "input"
            vocals_path = result_dir / "vocals.wav"
            accompaniment_path = result_dir / "no_vocals.wav"
            if completed.returncode != 0 or not vocals_path.is_file() or not accompaniment_path.is_file():
                details = (completed.stderr or completed.stdout or "Demucs কোনো অডিও তৈরি করতে পারেনি.").strip()
                self._send_json(500, {"error": "Demucs প্রসেস করতে পারেনি: " + details[-1800:]})
                return

            JOBS[job_id] = result_dir
            self._send_json(200, {
                "vocals": f"/audio/{job_id}/vocals.wav",
                "no_vocals": f"/audio/{job_id}/no_vocals.wav",
            })
        except (OSError, ValueError) as error:
            self._send_json(500, {"error": f"সার্ভার ত্রুটি: {error}"})

    def _send_json(self, status: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(os.fspath(path))[0] or "audio/wav")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        with path.open("rb") as audio:
            while chunk := audio.read(64 * 1024):
                self.wfile.write(chunk)

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), WebUIHandler)
    print(f"Demucs Web UI is running at http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop the server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Demucs Web UI.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
