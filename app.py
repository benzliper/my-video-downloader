import os
import uuid
from flask import Flask, render_template, request, send_file, after_this_request, jsonify
import yt_dlp

app = Flask(__name__)
DOWNLOAD_FOLDER = 'downloads'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# ตั้งค่าพื้นฐานเพื่อเลี่ยงการบล็อกของ YouTube บน Cloud Server
BASE_YDL_OPTS = {
    'quiet': True,
    'no_warnings': True,
    'noplaylist': True,
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'ios', 'web'],
            'player_skip': ['webpage', 'configs'],
        }
    }
}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/info', methods=['POST'])
def get_info():
    data = request.get_json()
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({'error': 'กรุณาวางลิงก์วิดีโอก่อนครับ'}), 400

    try:
        with yt_dlp.YoutubeDL(BASE_YDL_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
            
            duration = info.get('duration')
            if duration:
                mins, secs = divmod(int(duration), 60)
                hours, mins = divmod(mins, 60)
                duration_str = f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"
            else:
                duration_str = "ไม่ระบุความยาว"

            return jsonify({
                'title': info.get('title', 'ไม่มีชื่อคลิป'),
                'thumbnail': info.get('thumbnail', ''),
                'uploader': info.get('uploader', 'ไม่ระบุช่อง'),
                'duration': duration_str
            })
    except Exception as e:
        return jsonify({'error': f'ดึงข้อมูลไม่สำเร็จ: {str(e)}'}), 400

@app.route('/download', methods=['POST'])
def download():
    url = request.form.get('url')
    quality = request.form.get('quality', '1080')
    
    if not url:
        return "กรุณาวางลิงก์วิดีโอก่อนครับ", 400

    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_FOLDER, f"{file_id}_%(title)s.%(ext)s")

    ydl_opts = BASE_YDL_OPTS.copy()
    ydl_opts['outtmpl'] = output_template

    if quality.startswith('mp3'):
        bitrate = quality.split('_')[1] if '_' in quality else '192'
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': bitrate,
        }]
    else:
        if quality == '1080':
            format_str = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'
        elif quality == '720':
            format_str = 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best'
        elif quality == '480':
            format_str = 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480]/best'
        else:
            format_str = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'

        ydl_opts['format'] = format_str
        ydl_opts['merge_output_format'] = 'mp4'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            
            if quality.startswith('mp3'):
                filename = os.path.splitext(filename)[0] + '.mp3'
            else:
                base, ext = os.path.splitext(filename)
                if ext != '.mp4' and os.path.exists(base + '.mp4'):
                    filename = base + '.mp4'

        @after_this_request
        def remove_file(response):
            try:
                if os.path.exists(filename):
                    os.remove(filename)
            except Exception as e:
                print(f"Error removing file: {e}")
            return response

        clean_name = os.path.basename(filename).split('_', 1)[1]
        return send_file(filename, as_attachment=True, download_name=clean_name)

    except Exception as e:
        return f"เกิดข้อผิดพลาดในการดาวน์โหลด: {str(e)}", 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
