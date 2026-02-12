#!/usr/bin/env python3
import sys
import json
import os
import requests
from datetime import datetime
import urllib.parse

print("Content-Type: application/json")
print("Status: 200 OK")
print()

body = sys.stdin.read()

try:
    data = json.loads(body)
    code = data.get('code')
    msg = data.get('msg')
    cb_data = data.get('data', {})
    callback_type = cb_data.get('callbackType')
    task_id = cb_data.get('task_id')
    music_tracks = cb_data.get('data', [])

    log_file = "/var/www/cgi-bin/callback.log"
    with open(log_file, "a") as f:
        f.write(f"[{datetime.now()}] {json.dumps(data, ensure_ascii=False)}\n\n")

    if code == 200 and callback_type == 'complete' and music_tracks:
        songs_dir = "/var/www/html/songs"
        os.makedirs(songs_dir, exist_ok=True)

        downloaded = []
        for i, track in enumerate(music_tracks):
            title = track.get('title', f"track_{i+1}").replace("/", "_").replace("\\", "_")
            audio_url = track.get('audio_url') or track.get('stream_audio_url')
            image_url = track.get('image_url')
            duration = track.get('duration', '—')
            lyrics = track.get('prompt', '')  # часто lyrics в prompt

            if audio_url:
                try:
                    r = requests.get(audio_url, stream=True, timeout=30)
                    r.raise_for_status()
                    filename = f"{task_id}_{i+1}_{title}.mp3"
                    path = os.path.join(songs_dir, filename)
                    with open(path, 'wb') as f:
                        for chunk in r.iter_content(8192):
                            f.write(chunk)
                    public_url = f"http://твой-ip/songs/{urllib.parse.quote(filename)}"  # замени на свой IP/домен

                    img_path = None
                    if image_url:
                        img_r = requests.get(image_url, timeout=15)
                        if img_r.status_code == 200:
                            img_filename = f"{task_id}_{i+1}_{title}.jpg"
                            img_path = os.path.join(songs_dir, img_filename)
                            with open(img_path, 'wb') as img_f:
                                img_f.write(img_r.content)
                            img_public = f"http://твой-ip/songs/{urllib.parse.quote(img_filename)}"

                    downloaded.append({
                        'title': title,
                        'duration': duration,
                        'audio_url': public_url,
                        'image_url': img_public if img_path else image_url,
                        'lyrics': lyrics,
                        'local_path': path
                    })
                except Exception as e:
                    with open("/var/www/cgi-bin/error.log", "a") as ef:
                        ef.write(f"[{datetime.now()}] Download error {audio_url}: {e}\n")

        # Уведомляем Telegram-бота (через простой webhook или файл-очередь)
        # Вариант 1: простой — пишем в файл, бот периодически читает
        if downloaded:
            notify_file = "/var/www/cgi-bin/notify_queue.jsonl"
            with open(notify_file, "a") as nf:
                nf.write(json.dumps({
                    'task_id': task_id,
                    'tracks': downloaded,
                    'timestamp': str(datetime.now())
                }) + "\n")

    print(json.dumps({"status": "received"}))

except Exception as e:
    with open("/var/www/cgi-bin/error.log", "a") as ef:
        ef.write(f"[{datetime.now()}] Error: {str(e)}\n")
    print(json.dumps({"status": "error", "message": str(e)}))
