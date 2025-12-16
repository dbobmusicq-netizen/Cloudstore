import os
import json
import uuid
import time
import threading
import logging
import requests
from flask import Flask, request, Response, render_template_string, redirect, abort, send_file
import telebot

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "changeme")
# Render sets RENDER_EXTERNAL_URL automatically. 
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000")
DB_FILE = "database.json"

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Apps
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN)

# --- DATABASE MANAGEMENT ---
db_lock = threading.Lock()

def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    try:
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def save_to_db(token, data):
    with db_lock:
        db = load_db()
        db[token] = data
        with open(DB_FILE, 'w') as f:
            json.dump(db, f, indent=4)

def get_from_db(token):
    db = load_db()
    return db.get(token)

def delete_from_db(token):
    with db_lock:
        db = load_db()
        if token in db:
            del db[token]
            with open(DB_FILE, 'w') as f:
                json.dump(db, f, indent=4)
            return True
        return False

def restore_db(file_obj):
    try:
        data = json.load(file_obj)
        with db_lock:
            with open(DB_FILE, 'w') as f:
                json.dump(data, f, indent=4)
        return True
    except Exception:
        return False

# --- HTML TEMPLATES ---
TIMER_PAGE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Download</title>
    <style>
        body { font-family: sans-serif; background: #f0f2f5; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .card { background: white; padding: 2rem; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); text-align: center; max-width: 400px; width: 100%; }
        .spinner { border: 4px solid #f3f3f3; border-top: 4px solid #3498db; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto 1rem; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        #timer { font-weight: bold; color: #3498db; }
        .filename { background: #eee; padding: 5px; border-radius: 4px; font-family: monospace; display: block; margin-top:10px; word-break: break-all;}
    </style>
    <script>
        let timeLeft = 10;
        function updateTimer() {
            document.getElementById('timer').innerText = timeLeft;
            if (timeLeft <= 0) {
                document.getElementById('status').innerText = "Starting stream...";
                window.location.href = "{{ stream_url }}";
            } else {
                timeLeft--;
                setTimeout(updateTimer, 1000);
            }
        }
        window.onload = updateTimer;
    </script>
</head>
<body>
    <div class="card">
        <div class="spinner"></div>
        <h3>Preparing File...</h3>
        <span class="filename">{{ filename }}</span>
        <p>Stream starts in <span id="timer">10</span>s.</p>
        <p id="status"></p>
    </div>
</body>
</html>
"""

ADMIN_PANEL_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Admin</title>
    <style>
        body { font-family: sans-serif; padding: 20px; background: #f4f4f4; }
        table { width: 100%; border-collapse: collapse; background: white; }
        th, td { padding: 12px; border-bottom: 1px solid #ddd; text-align: left; }
        th { background: #333; color: white; }
        .btn { padding: 5px 10px; color: white; text-decoration: none; border-radius: 3px; font-size: 12px; margin-right: 5px; }
        .view { background: #2196F3; } .del { background: #f44336; } .dl { background: #ff9800; }
        .backup-box { background: white; padding: 15px; margin-bottom: 20px; border-radius: 5px; border-left: 5px solid #ff9800; }
        form { display: inline-block; margin-left: 20px; }
    </style>
</head>
<body>
    <h1>Admin Panel</h1>
    <div class="backup-box">
        <strong>⚠️ Backup/Restore:</strong>
        <a href="/admin/backup?key={{ key }}" class="btn dl">DOWNLOAD DB</a>
        <form action="/admin/restore?key={{ key }}" method="post" enctype="multipart/form-data">
            <input type="file" name="backup_file" required>
            <button type="submit" class="btn del">RESTORE</button>
        </form>
    </div>
    <table>
        <thead><tr><th>File</th><th>Size</th><th>Actions</th></tr></thead>
        <tbody>
            {% for token, data in files.items() %}
            <tr>
                <td>{{ data['file_name'] }}</td>
                <td>{{ (data['file_size']/1024/1024)|round(2) }} MB</td>
                <td>
                    <a href="/file/{{ token }}" target="_blank" class="btn view">OPEN</a>
                    <a href="/admin/delete/{{ token }}?key={{ key }}" class="btn del">DEL</a>
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</body>
</html>
"""

# --- ROUTES ---
@app.route('/')
def index(): return "Bot is Running."

@app.route('/ping')
def ping(): return "pong", 200

@app.route('/file/<token>')
def file_landing(token):
    data = get_from_db(token)
    if not data: abort(404)
    return render_template_string(TIMER_PAGE_HTML, filename=data['file_name'], stream_url=f"/stream/{token}")

@app.route('/stream/<token>')
def stream_file(token):
    data = get_from_db(token)
    if not data: abort(404)
    try:
        file_info = bot.get_file(data['file_id'])
        tg_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        req = requests.get(tg_url, stream=True)
        headers = {'Content-Type': data['mime_type'], 'Content-Disposition': f'inline; filename="{data["file_name"]}"'}
        if 'Content-Length' in req.headers: headers['Content-Length'] = req.headers['Content-Length']
        return Response(req.iter_content(chunk_size=4096), headers=headers, direct_passthrough=True)
    except Exception as e:
        logger.error(e)
        return "Error", 500

@app.route('/admin')
def admin_panel():
    key = request.args.get('key')
    if key != ADMIN_SECRET: abort(403)
    return render_template_string(ADMIN_PANEL_HTML, files=load_db(), key=key)

@app.route('/admin/delete/<token>')
def admin_delete(token):
    if request.args.get('key') != ADMIN_SECRET: abort(403)
    delete_from_db(token)
    return redirect(f'/admin?key={ADMIN_SECRET}')

@app.route('/admin/backup')
def admin_backup():
    if request.args.get('key') != ADMIN_SECRET: abort(403)
    if not os.path.exists(DB_FILE): return "No DB", 404
    return send_file(DB_FILE, as_attachment=True, download_name='database_backup.json')

@app.route('/admin/restore', methods=['POST'])
def admin_restore():
    if request.args.get('key') != ADMIN_SECRET: abort(403)
    file = request.files.get('backup_file')
    if file and restore_db(file): return redirect(f'/admin?key={ADMIN_SECRET}')
    return "Failed", 500

# --- BOT HANDLERS ---
def save_msg(msg, type_name):
    if hasattr(msg, type_name):
        obj = getattr(msg, type_name)
        if obj.file_size > 20*1024*1024:
            bot.reply_to(msg, "❌ Too large (Max 20MB)")
            return
        token = uuid.uuid4().hex
        fname = getattr(obj, 'file_name', f'{type_name}.file')
        # Audio specific fix
        if type_name == 'audio' and hasattr(obj, 'title'): fname = f"{obj.title}.mp3"
        
        save_to_db(token, {
            "file_id": obj.file_id, 
            "file_name": fname, 
            "mime_type": getattr(obj, 'mime_type', 'application/octet-stream'), 
            "file_size": obj.file_size,
            "uploader_id": msg.from_user.id
        })
        bot.reply_to(msg, f"✅ Saved!\n🔗 {BASE_URL}/file/{token}")

@bot.message_handler(content_types=['document', 'video', 'audio'])
def handle_files(m):
    if m.document: save_msg(m, 'document')
    elif m.video: save_msg(m, 'video')
    elif m.audio: save_msg(m, 'audio')

@bot.message_handler(commands=['start'])
def start(m): bot.reply_to(m, "Send me a file (Max 20MB).")

if __name__ == "__main__":
    if not BOT_TOKEN: exit("No Token")
    threading.Thread(target=bot.infinity_polling, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
