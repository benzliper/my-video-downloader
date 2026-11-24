import os
import uuid
import shutil
import urllib.parse
from flask import Flask, render_template, request, send_file, after_this_request, jsonify
import yt_dlp

app = Flask(__name__)
DOWNLOAD_FOLDER = 'downloads'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)


class YdlLogger:
    """เก็บข้อความแจ้งเตือนและ Error จาก yt-dlp เพื่อใช้ตรวจสอบสาเหตุหากดาวน์โหลดไม่ผ่าน"""
    def __init__(self):
        self.warnings = []
        self.errors = []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        self.warnings.append(str(msg))

    def error(self, msg):
        self.errors.append(str(msg))


def find_cookies_source():
    """ค้นหาตำแหน่งไฟล์ cookies.txt ในเครื่องหรือใน Secret Files ของ Render"""
    for path in ('cookies.txt', '/etc/secrets/cookies.txt'):
        if os.path.exists(path):
            return path
    return None


def get_ydl_opts(use_cookies=True, logger=None, player_clients=None):
    """สร้างชุดการตั้งค่าพื้นฐานสำหรับ yt-dlp พร้อมเปิดใช้งาน Deno และ EJS"""
    opts = {
        'quiet': logger is None,
        'no_warnings': logger is None,
        'noplaylist': True,
        'extract_flat': False,
        'socket_timeout': 25,
        'nocheckcertificate': True,
        'restrictfilenames': True,
        'remote_components': ['ejs:github'],
        'js_runtimes': {'deno': {}},
    }

    if logger is not None:
        opts['logger'] = logger

    if player_clients:
        opts['extractor_args'] = {
            'youtube': {
                'player_client': player_clients,
            }
        }

    if use_cookies:
        cookie_src = find_cookies_source()
        if cookie_src:
            temp_cookie = '/tmp/yt_cookies.txt'
            try:
                shutil.copyfile(cookie_src, temp_cookie)
                opts['cookiefile'] = temp_cookie
            except Exception as e:
                if logger:
                    logger.warning(f"Copy cookies failed: {e}")

    return opts


def clean_youtube_url(url):
    """แปลงลิงก์ย่อ youtu.be และตัดพารามิเตอร์ Playlist/Mix ออก"""
    parsed = urllib.parse.urlparse(url)
    if 'youtu.be' in parsed.netloc:
        video_id = parsed.path.lstrip('/')
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
    elif 'youtube.com' in parsed.netloc and 'v=' in parsed.query:
        query = urllib.parse.parse_qs(parsed.query)
        if 'v' in query:
            return f"https://www.youtube.com/watch?v={query['v'][0]}"
    return url


def resolve_downloaded_filepath(ydl, info, is_mp3):
    """ค้นหาพาธไฟล์สุดท้ายที่ดาวน์โหลดและแปลงไฟล์เสร็จแล้ว"""
    requested = info.get('requested_downloads')
    if requested and isinstance(requested, list):
        filepath = requested[0].get('filepath')
        if filepath and os.path.exists(filepath):
            return filepath

    filename = ydl.prepare_filename(info)
    base, ext = os.path.splitext(filename)
    if is_mp3:
        mp3_path = base + '.mp3'
        if os.path.exists(mp3_path):
            return mp3_path
    else:
        mp4_path = base + '.mp4'
        if ext != '.mp4' and os.path.exists(mp4_path):
            return mp4_path

    return filename


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/info', methods=['POST'])
def get_info():
    """ดึงข้อมูลพรีวิว (ปกคลิป ชื่อคลิป ช่อง และความยาว)"""
    data = request.get_json(silent=True) or {}
    raw_url = data.get('url', '').strip()

    if not raw_url:
        return jsonify({'error': 'กรุณาวางลิงก์วิดีโอก่อนครับ'}), 400

    url = clean_youtube_url(raw_url)
    opts = get_ydl_opts(use_cookies=True)
    opts['skip_download'] = True
    opts['ignore_no_formats_error'] = True

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
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
        return jsonify({'error': f'ดึงข้อมูลไม่สำเร็จ: {str(e)[:150]}'}), 400


@app.route('/download', methods=['POST'])
def download():
    """ดาวน์โหลดและแปลงไฟล์ MP4/MP3 พร้อมลองสลับช่องทางอัตโนมัติหากติดข้อจำกัด"""
    raw_url = request.form.get('url', '').strip()
    quality = request.form.get('quality', '1080')

    if not raw_url:
        return "กรุณาวางลิงก์วิดีโอก่อนครับ", 400

    url = clean_youtube_url(raw_url)
    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_FOLDER, f"{file_id}_%(title)s.%(ext)s")
    is_mp3 = quality.startswith('mp3')

    if is_mp3:
        primary_format = 'bestaudio/best'
    elif quality == '1080':
        primary_format = 'bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b'
    elif quality == '720':
        primary_format = 'bv*[height<=720]+ba/b[height<=720]/bv*+ba/b'
    elif quality == '480':
        primary_format = 'bv*[height<=480]+ba/b[height<=480]/bv*+ba/b'
    else:
        primary_format = 'bv*+ba/b'

    has_cookies = find_cookies_source() is not None

    # รายการรูปแบบการลองดาวน์โหลดทีละขั้น (หลีกเลี่ยงการใช้ -f best อย่างเดียว)
    attempts = [
        (has_cookies, primary_format, None),
        (has_cookies, 'bestaudio/best' if is_mp3 else None, ['default', 'mweb', 'web_safari']),
    ]
    if has_cookies:
        # ลองใหม่แบบไม่ใช้ Cookies เผื่อ Cookies หมดอายุหรือบัญชีติด SABR
        attempts.append((False, primary_format, None))
        attempts.append((False, 'bestaudio/best' if is_mp3 else None, ['android', 'tv', 'web_safari']))
    else:
        attempts.append((False, 'bestaudio/best' if is_mp3 else None, ['android', 'tv']))

    filename = None
    last_error = ""
    collected_warnings = []

    for use_cookies, fmt, clients in attempts:
        logger = YdlLogger()
        ydl_opts = get_ydl_opts(use_cookies=use_cookies, logger=logger, player_clients=clients)
        ydl_opts['outtmpl'] = output_template

        if fmt:
            ydl_opts['format'] = fmt

        if is_mp3:
            bitrate = quality.split('_')[1] if '_' in quality else '192'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': bitrate,
            }]
        else:
            ydl_opts['merge_output_format'] = 'mp4'

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = resolve_downloaded_filepath(ydl, info, is_mp3)
                if filename and os.path.exists(filename):
                    break
        except Exception as e:
            last_error = str(e)
            for w in logger.warnings:
                if w not in collected_warnings:
                    collected_warnings.append(w)

    if not filename or not os.path.exists(filename):
        deno_status = "พบ Deno" if shutil.which('deno') else "ไม่พบ Deno"
        cookie_status = "พบ cookies.txt" if has_cookies else "ไม่มี cookies.txt"
        warn_text = "\n".join(collected_warnings[:5]) if collected_warnings else "ไม่มีคำเตือนเพิ่มเติม"
        return (
            f"เกิดข้อผิดพลาดในการดาวน์โหลด: {last_error}\n\n"
            f"--- ข้อมูลตรวจสอบระบบ ---\n"
            f"สถานะระบบ: {deno_status} | {cookie_status}\n"
            f"คำเตือนจาก yt-dlp:\n{warn_text}"
        ), 500, {'Content-Type': 'text/plain; charset=utf-8'}

    file_to_remove = filename

    @after_this_request
    def remove_file(response):
        try:
            if file_to_remove and os.path.exists(file_to_remove):
                os.remove(file_to_remove)
        except Exception as e:
            print(f"Error removing file: {e}")
        return response

    basename = os.path.basename(filename)
    clean_name = basename.split('_', 1)[1] if '_' in basename else basename
    return send_file(filename, as_attachment=True, download_name=clean_name)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 7860))
    app.run(host='0.0.0.0', port=port, debug=False)
