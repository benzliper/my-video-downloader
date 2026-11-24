import os
import uuid
import shutil
import urllib.parse
from flask import Flask, render_template, request, send_file, after_this_request, jsonify
import yt_dlp

app = Flask(__name__)
DOWNLOAD_FOLDER = 'downloads'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)


def get_ydl_opts():
    """สร้างชุดการตั้งค่าพื้นฐานสำหรับ yt-dlp โดยให้ใช้ค่าเริ่มต้นร่วมกับ Deno"""
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'extract_flat': False,
        'socket_timeout': 25,
        'nocheckcertificate': True,
        'restrictfilenames': True,  # ป้องกัน Error จากชื่อคลิปที่มีอักขระพิเศษ
    }

    # คัดลอกไฟล์ cookies.txt ไปไว้ที่ /tmp เพื่อให้ yt-dlp ใช้งานได้โดยไม่ติดสิทธิ์ Read-only
    temp_cookie = '/tmp/yt_cookies.txt'
    try:
        if os.path.exists('cookies.txt'):
            shutil.copyfile('cookies.txt', temp_cookie)
            opts['cookiefile'] = temp_cookie
        elif os.path.exists('/etc/secrets/cookies.txt'):
            shutil.copyfile('/etc/secrets/cookies.txt', temp_cookie)
            opts['cookiefile'] = temp_cookie
    except Exception as e:
        print(f"Warning: Could not copy cookies file: {e}")

    return opts


def clean_youtube_url(url):
    """แปลงลิงก์ย่อ youtu.be และตัดพารามิเตอร์ Playlist/Mix ออกเพื่อป้องกันระบบค้าง"""
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
    """ค้นหาพาธไฟล์สุดท้ายที่ดาวน์โหลดและแปลงไฟล์เสร็จสมบูรณ์แล้ว"""
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
    """ดึงข้อมูลพรีวิว (ปกคลิป ชื่อคลิป ช่อง และความยาว) โดยยังไม่ดาวน์โหลดไฟล์จริง"""
    data = request.get_json(silent=True) or {}
    raw_url = data.get('url', '').strip()

    if not raw_url:
        return jsonify({'error': 'กรุณาวางลิงก์วิดีโอก่อนครับ'}), 400

    url = clean_youtube_url(raw_url)
    opts = get_ydl_opts()
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
    """ดาวน์โหลดและแปลงไฟล์เป็น MP4 หรือ MP3 พร้อมระบบสำรองรูปแบบไฟล์อัตโนมัติ"""
    raw_url = request.form.get('url', '').strip()
    quality = request.form.get('quality', '1080')

    if not raw_url:
        return "กรุณาวางลิงก์วิดีโอก่อนครับ", 400

    url = clean_youtube_url(raw_url)
    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_FOLDER, f"{file_id}_%(title)s.%(ext)s")
    is_mp3 = quality.startswith('mp3')

    ydl_opts = get_ydl_opts()
    ydl_opts['outtmpl'] = output_template

    if is_mp3:
        bitrate = quality.split('_')[1] if '_' in quality else '192'
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': bitrate,
        }]
    else:
        if quality == '1080':
            format_str = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'
        elif quality == '720':
            format_str = 'bestvideo[height<=720]+bestaudio/best[height<=720]/best'
        elif quality == '480':
            format_str = 'bestvideo[height<=480]+bestaudio/best[height<=480]/best'
        else:
            format_str = 'bestvideo+bestaudio/best'

        ydl_opts['format'] = format_str
        ydl_opts['merge_output_format'] = 'mp4'

    filename = None
    try:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = resolve_downloaded_filepath(ydl, info, is_mp3)
        except yt_dlp.utils.DownloadError as dl_err:
            # หากความละเอียดที่เลือกไม่มีในระบบ ให้ลองดาวน์โหลดใหม่ด้วยรูปแบบพื้นฐาน (best)
            if 'Requested format is not available' in str(dl_err):
                fallback_opts = ydl_opts.copy()
                fallback_opts['format'] = 'bestaudio/best' if is_mp3 else 'best'
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = resolve_downloaded_filepath(ydl, info, is_mp3)
            else:
                raise

        if not filename or not os.path.exists(filename):
            return "ไม่พบไฟล์ที่ดาวน์โหลดเสร็จสิ้น กรุณาลองใหม่อีกครั้ง", 500

        @after_this_request
        def remove_file(response):
            try:
                if filename and os.path.exists(filename):
                    os.remove(filename)
            except Exception as e:
                print(f"Error removing file: {e}")
            return response

        basename = os.path.basename(filename)
        clean_name = basename.split('_', 1)[1] if '_' in basename else basename
        return send_file(filename, as_attachment=True, download_name=clean_name)

    except Exception as e:
        return f"เกิดข้อผิดพลาดในการดาวน์โหลด: {str(e)}", 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 7860))
    app.run(host='0.0.0.0', port=port, debug=False)
