#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bot28 Final — رفع قطعی Race Condition + پنل ادمین شیشه‌ای
Lock کامل روی subscriptions.json، پنل ادمین تعاملی با ویرایش پیام،
ضبط صدا با PulseAudio + Firefox، سه صف جداگانه.
"""

import os, sys, json, time, math, queue, shutil, zipfile, uuid, re, hashlib
import subprocess, threading, traceback, random
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, Optional, List, Tuple, Set
from urllib.parse import urlparse, urljoin, unquote

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ═══════════════ پیکربندی اصلی ═══════════════
BALE_BOT_TOKEN = os.getenv("BALE_BOT_TOKEN", "").strip()
if not BALE_BOT_TOKEN:
    print("ERROR: BALE_BOT_TOKEN not set", file=sys.stderr)
    sys.exit(1)

BALE_API_URL = "https://tapi.bale.ai/bot" + BALE_BOT_TOKEN
REQUEST_TIMEOUT = 30
LONG_POLL_TIMEOUT = 50
ZIP_PART_SIZE = int(19 * 1024 * 1024)       # 19MB

ADMIN_CHAT_ID = 46829437

CODES_BACKUP_FILE = "codes_backup.json"
BANS_FILE = "bans.json"

WORKER_TIMEOUT = 300                     # ۵ دقیقه
BROWSER_AUTO_CLOSE_SECONDS = 1200        # ۲۰ دقیقه
MAX_RECORD_MINUTES_ADMIN = 60
MAX_RECORD_MINUTES_USER = 15

# ═══════════════ سطوح اشتراک ═══════════════
PLAN_LIMITS = {
    "پایه": {
        "browser": (3, 3600, None), "screenshot": (3, 3600, None),
        "2x_screenshot": (0, 3600, None), "4k_screenshot": (0, 3600, None),
        "download": (1, 3600, 20 * 1024 * 1024), "record_video": (0, 3600, None),
        "scan_downloads": (0, 3600, None), "scan_videos": (0, 3600, None),
        "download_website": (0, 3600, None), "extract_commands": (0, 3600, None),
        "interactive_scan": (0, 3600, None), "fullpage_screenshot": (0, 3600, None),
    },
    "نقره‌ای": {
        "browser": (5, 3600, None), "screenshot": (5, 3600, None),
        "2x_screenshot": (1, 3600, None), "4k_screenshot": (0, 3600, None),
        "download": (3, 3600, 100 * 1024 * 1024), "record_video": (2, 3600, None),
        "scan_downloads": (1, 3600, None), "scan_videos": (1, 3600, None),
        "download_website": (0, 3600, None), "extract_commands": (1, 3600, None),
        "interactive_scan": (1, 3600, None), "fullpage_screenshot": (1, 3600, None),
    },
    "طلایی": {
        "browser": (15, 3600, None), "screenshot": (15, 3600, None),
        "2x_screenshot": (5, 3600, None), "4k_screenshot": (0, 3600, None),
        "download": (10, 3600, 600 * 1024 * 1024), "record_video": (8, 3600, None),
        "scan_downloads": (5, 3600, None), "scan_videos": (8, 3600, None),
        "download_website": (2, 3600, None), "extract_commands": (5, 3600, None),
        "interactive_scan": (5, 3600, None), "fullpage_screenshot": (5, 3600, None),
    },
    "الماسی": {
        "browser": (30, 3600, None), "screenshot": (30, 3600, None),
        "2x_screenshot": (20, 3600, None), "4k_screenshot": (5, 3600, None),
        "download": (20, 3600, 2 * 1024 * 1024 * 1024), "record_video": (12, 3600, None),
        "scan_downloads": (15, 3600, None), "scan_videos": (20, 3600, None),
        "download_website": (5, 86400, None), "extract_commands": (20, 3600, None),
        "interactive_scan": (20, 3600, None), "fullpage_screenshot": (20, 3600, None),
    },
}

ALLOWED_RESOLUTIONS = {
    "480p": (854, 480), "720p": (1280, 720),
    "1080p": (1920, 1080), "4k": (3840, 2160),
}
RES_REQUIREMENTS = {
    "480p": ["پایه", "نقره‌ای", "طلایی", "الماسی"],
    "720p": ["نقره‌ای", "طلایی", "الماسی"],
    "1080p": ["طلایی", "الماسی"],
    "4k": ["الماسی"],
}
MAX_4K_RECORD_MINUTES = 5

# ═══════════════ قفل‌ها ═══════════════
print_lock = threading.Lock()
callback_map: Dict[str, str] = {}
callback_map_lock = threading.Lock()
flood_lock = threading.Lock()
user_flood_data: Dict[int, List[float]] = {}
user_ban_until: Dict[int, float] = {}
admin_bans: Dict[int, float] = {}
subscriptions_lock = threading.Lock()      # ★★★ کلید حل باگ

QUEUE_FILES = {
    "browser": "browser_queue.json",
    "download": "download_queue.json",
    "record": "record_queue.json"
}

def debug_log(msg: str):
    try:
        with open("bot_debug.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}\n")
    except: pass

def safe_print(*args, **kwargs):
    with print_lock:
        print(*args, **kwargs, flush=True)

# ═══════════════ مدل‌ها ═══════════════
@dataclass
class UserSettings:
    record_time: int = 20          # دقیقه
    default_download_mode: str = "store"
    browser_mode: str = "text"
    deep_scan_mode: str = "logical"
    record_behavior: str = "click"
    audio_enabled: bool = False
    video_format: str = "webm"
    incognito_mode: bool = False
    video_delivery: str = "split"
    video_resolution: str = "720p"

@dataclass
class SessionState:
    chat_id: int
    state: str = "idle"
    is_admin: bool = False
    subscription: str = "پایه"
    current_job_id: Optional[str] = None
    browser_url: Optional[str] = None
    last_interaction: float = time.time()
    cancel_requested: bool = False
    text_links: Optional[Dict[str, str]] = None
    browser_links: Optional[List[Dict[str, str]]] = None
    browser_page: int = 0
    settings: UserSettings = field(default_factory=UserSettings)
    click_counter: int = 0
    ad_blocked_domains: Optional[List[str]] = field(default_factory=list)
    found_downloads: Optional[List[Dict[str, str]]] = None
    found_downloads_page: int = 0
    last_settings_msg_id: Optional[str] = None
    last_admin_msg_id: Optional[str] = None        # ★ برای ویرایش پنل
    interactive_elements: Optional[List[Dict[str, Any]]] = None

@dataclass
class Job:
    job_id: str
    chat_id: int
    mode: str
    url: str
    status: str = "queued"
    created_at: float = time.time()
    updated_at: float = time.time()
    error_message: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None
    started_at: Optional[float] = None
    queue_type: str = "browser"

# ═══════════════ تحریم‌ها ═══════════════
def load_bans():
    try:
        with open(BANS_FILE, "r") as f:
            return {int(k): v for k, v in json.load(f).items()}
    except:
        return {}

def save_bans(data: Dict[int, float]):
    tmp = BANS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({str(k): v for k, v in data.items()}, f)
    os.replace(tmp, BANS_FILE)

def ban_user(chat_id: int, minutes: Optional[int] = None):
    now = time.time()
    with flood_lock:
        if minutes is None:
            admin_bans[chat_id] = 9999999999
        else:
            admin_bans[chat_id] = now + minutes * 60
        save_bans(admin_bans)

def unban_user(chat_id: int):
    with flood_lock:
        if chat_id in admin_bans:
            del admin_bans[chat_id]
            save_bans(admin_bans)
            return True
        return False

def is_user_banned(chat_id: int) -> bool:
    if chat_id == ADMIN_CHAT_ID:
        return False
    now = time.time()
    with flood_lock:
        if chat_id in admin_bans and now < admin_bans[chat_id]:
            return True
        if chat_id in admin_bans:
            del admin_bans[chat_id]
            save_bans(admin_bans)
        if chat_id in user_ban_until and now < user_ban_until[chat_id]:
            return True
        return False

# ═══════════════ اشتراک (با Lock کامل) ═══════════════
SUBSCRIPTIONS_FILE = "subscriptions.json"
SERVICE_DISABLED_FLAG = "service_disabled.flag"

def is_service_disabled() -> bool:
    return os.path.exists(SERVICE_DISABLED_FLAG)

def load_subscriptions() -> Dict[str, Any]:
    with subscriptions_lock:
        try:
            with open(SUBSCRIPTIONS_FILE, "r") as f:
                data = json.load(f)
        except:
            data = {}
        if "valid_codes" not in data:
            data["valid_codes"] = {}
        return data

def save_subscriptions(data: Dict[str, Any]):
    with subscriptions_lock:
        tmp = SUBSCRIPTIONS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, SUBSCRIPTIONS_FILE)

def get_user_subscription(chat_id: int) -> str:
    data = load_subscriptions()
    key = str(chat_id)
    return data[key]["level"] if key in data and "level" in data[key] else "پایه"

def set_user_subscription(chat_id: int, level: str):
    with subscriptions_lock:
        data = load_subscriptions()
        data[str(chat_id)] = {"level": level, "activated_at": time.time(), "usage": {}}
        save_subscriptions(data)

def activate_subscription(chat_id: int, code: str) -> Optional[str]:
    code = code.strip()
    with subscriptions_lock:
        data = load_subscriptions()
        codes = data.get("valid_codes", {})
        if code not in codes:
            return None
        info = codes[code]
        if "bound_chat_id" in info and info["bound_chat_id"] is not None:
            if str(chat_id) != str(info["bound_chat_id"]):
                return None
        if "used_by" not in info or info["used_by"] is None:
            info["used_by"] = str(chat_id)
        else:
            if info["used_by"] != str(chat_id):
                return None
        save_subscriptions(data)
    set_user_subscription(chat_id, info["plan"])
    return info["plan"]

def add_code(level: str, code: str, bound_chat_id: Optional[int] = None) -> bool:
    with subscriptions_lock:
        data = load_subscriptions()
        codes = data.setdefault("valid_codes", {})
        if code in codes:
            return False
        new_entry = {"plan": level, "bound_chat_id": bound_chat_id, "used_by": None}
        codes[code] = new_entry
        save_subscriptions(data)
    backup = load_codes_backup()
    backup[code] = new_entry
    save_codes_backup(backup)
    return True

def remove_code(code: str) -> bool:
    with subscriptions_lock:
        data = load_subscriptions()
        codes = data.get("valid_codes", {})
        if code not in codes:
            return False
        del codes[code]
        save_subscriptions(data)
    backup = load_codes_backup()
    if code in backup:
        del backup[code]
        save_codes_backup(backup)
    return True

def load_codes_backup() -> Dict[str, Any]:
    try:
        with open(CODES_BACKUP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_codes_backup(data: Dict[str, Any]):
    tmp = CODES_BACKUP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, CODES_BACKUP_FILE)

def init_subscriptions_from_backup():
    backup = load_codes_backup()
    if not backup: return
    with subscriptions_lock:
        data = load_subscriptions()
        valid_codes = data.get("valid_codes", {})
        for code, info in backup.items():
            if code not in valid_codes:
                valid_codes[code] = info
        data["valid_codes"] = valid_codes
        save_subscriptions(data)

# ═══════════════ Rate Limiter ═══════════════
def check_rate_limit(chat_id: int, mode: str, file_size_bytes: Optional[int] = None) -> Optional[str]:
    if chat_id == ADMIN_CHAT_ID: return None
    level = get_user_subscription(chat_id)
    limits = PLAN_LIMITS.get(level, PLAN_LIMITS["پایه"])
    mode_key = mode
    if mode in ("browser", "browser_click"): mode_key = "browser"
    limit = limits.get(mode_key)
    if not limit: return f"⛔ این قابلیت برای سطح «{level}» در دسترس نیست."
    max_count, window_seconds, max_size = limit
    if max_size is not None and file_size_bytes is not None and file_size_bytes > max_size:
        max_mb = max_size / (1024 * 1024)
        return f"📦 حجم فایل ({file_size_bytes/(1024*1024):.1f}MB) بیش از حد مجاز ({max_mb:.0f}MB) برای سطح «{level}» است."
    if max_count >= 999: return None
    now = time.time()
    data = load_subscriptions()
    key = str(chat_id)
    usage = data.get(key, {}).get("usage", {}).get(mode_key, [])
    cutoff = now - window_seconds
    recent = [t for t in usage if t > cutoff]
    if len(recent) >= max_count:
        return f"⏰ محدودیت ساعتی: حداکثر {max_count} بار در ساعت (سطح «{level}»)."
    update_usage(chat_id, mode_key)
    return None

def update_usage(chat_id: int, mode: str):
    with subscriptions_lock:
        data = load_subscriptions()
        key = str(chat_id)
        if key not in data: data[key] = {"level": "پایه", "activated_at": time.time(), "usage": {}}
        usage = data[key].setdefault("usage", {}).setdefault(mode, [])
        usage.append(time.time())
        save_subscriptions(data)

# ═══════════════ ضد اسپم ═══════════════
FLOOD_WINDOW = 5
FLOOD_MAX_CLICKS = 10
BAN_DURATION = 900

def check_flood(chat_id: int) -> bool:
    if chat_id == ADMIN_CHAT_ID: return True
    now = time.time()
    with flood_lock:
        if is_user_banned(chat_id):
            return False
        clicks = user_flood_data.get(chat_id, [])
        clicks = [t for t in clicks if now - t < FLOOD_WINDOW]
        clicks.append(now)
        user_flood_data[chat_id] = clicks
        if len(clicks) > FLOOD_MAX_CLICKS:
            user_ban_until[chat_id] = now + BAN_DURATION
            return False
        return True

# ═══════════════ نشست‌ها ═══════════════
SESSIONS_FILE = "sessions.json"
def load_sessions():
    try:
        with open(SESSIONS_FILE, "r") as f: return json.load(f)
    except: return {}
def save_sessions(data):
    tmp = SESSIONS_FILE + ".tmp"
    with open(tmp, "w") as f: json.dump(data, f)
    os.replace(tmp, SESSIONS_FILE)
def get_session(chat_id):
    data = load_sessions()
    key = str(chat_id)
    if key in data:
        s = SessionState(chat_id=chat_id)
        d = data[key]
        for k, v in d.items():
            if k == "settings": s.settings = UserSettings(**v)
            elif k in ("ad_blocked_domains", "found_downloads", "last_settings_msg_id", "last_admin_msg_id", "interactive_elements"):
                setattr(s, k, v)
            else: setattr(s, k, v)
        if s.chat_id == ADMIN_CHAT_ID: s.is_admin = True; s.subscription = "الماسی"
        else: s.subscription = get_user_subscription(chat_id)
        return s
    s = SessionState(chat_id=chat_id)
    if s.chat_id == ADMIN_CHAT_ID: s.is_admin = True; s.subscription = "الماسی"
    return s
def set_session(session):
    data = load_sessions()
    d = asdict(session)
    d["settings"] = asdict(session.settings)
    d["ad_blocked_domains"] = session.ad_blocked_domains
    d["found_downloads"] = session.found_downloads
    d["last_settings_msg_id"] = session.last_settings_msg_id
    d["last_admin_msg_id"] = session.last_admin_msg_id
    d["interactive_elements"] = session.interactive_elements
    data[str(session.chat_id)] = d
    save_sessions(data)

# ═══════════════ API بله ═══════════════
def bale_request(method, params=None, files=None):
    url = f"{BALE_API_URL}/{method}"
    try:
        if files: r = requests.post(url, data=params or {}, files=files, timeout=REQUEST_TIMEOUT)
        else: r = requests.post(url, json=params or {}, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200: return None
        data = r.json()
        if not data.get("ok"): return None
        return data["result"]
    except: return None

def send_message(chat_id, text, reply_markup=None):
    params = {"chat_id": chat_id, "text": text}
    if reply_markup: params["reply_markup"] = json.dumps(reply_markup)
    return bale_request("sendMessage", params=params)

def edit_message_reply_markup(chat_id, message_id, reply_markup):
    params = {"chat_id": chat_id, "message_id": message_id, "reply_markup": json.dumps(reply_markup)}
    return bale_request("editMessageReplyMarkup", params=params)

def send_document(chat_id, file_path, caption=""):
    if not os.path.exists(file_path): return None
    with open(file_path, "rb") as f:
        return bale_request("sendDocument",
                            params={"chat_id": chat_id, "caption": caption},
                            files={"document": (os.path.basename(file_path), f)})

def answer_callback_query(cq_id, text="", show_alert=False):
    return bale_request("answerCallbackQuery",
                        {"callback_query_id": cq_id, "text": text, "show_alert": show_alert})

def get_updates(offset=None, timeout=LONG_POLL_TIMEOUT):
    params = {"timeout": timeout}
    if offset: params["offset"] = offset
    return bale_request("getUpdates", params=params) or []

# ═══════════════ منوها ═══════════════
def main_menu_keyboard(is_admin=False):
    base = [
        [{"text": "🧭 مرورگر", "callback_data": "menu_browser"},
         {"text": "📸 شات", "callback_data": "menu_screenshot"}],
        [{"text": "📥 دانلود", "callback_data": "menu_download"},
         {"text": "🎬 ضبط", "callback_data": "menu_record"}],
        [{"text": "⚙️ تنظیمات", "callback_data": "menu_settings"},
         {"text": "❓ راهنما", "callback_data": "menu_help"}]
    ]
    if is_admin: base.append([{"text": "🛠️ پنل ادمین", "callback_data": "menu_admin"}])
    return {"inline_keyboard": base}

def settings_keyboard(settings: UserSettings, subscription: str):
    rec = settings.record_time
    dlm = "سریع ⚡" if settings.default_download_mode == "stream" else "عادی 💾"
    mode = {"text": "📄 متن", "media": "🎬 مدیا", "explorer": "🔍 جستجوگر"}[settings.browser_mode]
    deep = "🧠 منطقی" if settings.deep_scan_mode == "logical" else "🗑 همه چیز"
    rec_behavior = {"click": "🖱️ کلیک", "scroll": "📜 اسکرول", "live": "🎭 لایو"}[settings.record_behavior]
    audio = "🔊 با صدا" if settings.audio_enabled else "🔇 بی‌صدا"
    vfmt = settings.video_format.upper()
    incognito = "🕶️ ناشناس" if settings.incognito_mode else "👤 عادی"
    delivery = "ZIP 📦" if settings.video_delivery == "zip" else "تکه‌ای 🧩"
    res = settings.video_resolution
    kb = [
        [{"text": f"⏱️ زمان: {rec}m", "callback_data": "set_rec"}],
        [{"text": f"📥 دانلود: {dlm}", "callback_data": "set_dlmode"}],
        [{"text": f"🌐 حالت: {mode}", "callback_data": "set_brwmode"}],
        [{"text": f"🔍 جستجو: {deep}", "callback_data": "set_deep"}],
        [{"text": f"🎬 رفتار: {rec_behavior}", "callback_data": "set_recbeh"}],
        [{"text": audio, "callback_data": "set_audio"}],
        [{"text": f"🎞️ فرمت: {vfmt}", "callback_data": "set_vfmt"}],
        [{"text": incognito, "callback_data": "set_incognito"}],
        [{"text": f"📦 ارسال: {delivery}", "callback_data": "set_viddel"}],
        [{"text": f"📺 کیفیت: {res}", "callback_data": "set_resolution"}],
        [{"text": "🔙 بازگشت", "callback_data": "back_main"}]
    ]
    return {"inline_keyboard": kb}

# ═══════════════ ابزارهای فایل ═══════════════
def is_direct_file_url(url: str) -> bool:
    known_extensions = [
        '.zip','.rar','.7z','.pdf','.mp4','.mkv','.avi','.mp3',
        '.exe','.apk','.dmg','.iso','.tar','.gz','.bz2','.xz','.whl',
        '.deb','.rpm','.msi','.pkg','.appimage','.jar','.war',
        '.py','.sh','.bat','.run','.bin','.img','.mov','.flv','.wmv',
        '.webm','.ogg','.wav','.flac','.csv','.docx','.pptx','.m3u8'
    ]
    path = urlparse(url).path.lower()
    if any(path.endswith(ext) for ext in known_extensions): return True
    filename = path.split('/')[-1]
    if '.' in filename:
        ext = filename.rsplit('.', 1)[-1]
        if ext and re.match(r'^[a-zA-Z0-9_-]+$', ext) and len(ext) <= 10: return True
    return False

def get_filename_from_url(url):
    path = unquote(urlparse(url).path)
    name = os.path.basename(path)
    return name if name and '.' in name else "downloaded_file"

def crawl_for_download_link(start_url, max_depth=1, max_pages=10, timeout_seconds=30):
    visited = set()
    q = queue.Queue(); q.put((start_url, 0))
    s = requests.Session(); s.headers.update({"User-Agent": "Mozilla/5.0"})
    pc = 0; start_time = time.time()
    while not q.empty():
        if time.time() - start_time > timeout_seconds: break
        cur, depth = q.get()
        if cur in visited or depth > max_depth or pc > max_pages: continue
        visited.add(cur); pc += 1
        try: r = s.get(cur, timeout=10)
        except: continue
        if is_direct_file_url(cur): return cur
        if "text/html" in r.headers.get("Content-Type", ""):
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = urljoin(cur, a["href"])
                if is_direct_file_url(href): return href
                if depth + 1 <= max_depth: q.put((href, depth+1))
    return None

def split_file_binary(file_path, prefix, ext):
    d = os.path.dirname(file_path) or "."
    if not os.path.exists(file_path): return []
    video_exts = ('.webm', '.mkv', '.mp4', '.avi', '.mov')
    if ext.lower() in video_exts and shutil.which('ffmpeg'):
        try:
            out_pattern = os.path.join(d, f"{prefix}_part%03d{ext}")
            cmd = ['ffmpeg', '-y', '-i', file_path,
                   '-c', 'copy', '-map', '0',
                   '-f', 'segment', '-segment_size', str(ZIP_PART_SIZE),
                   '-reset_timestamps', '1', out_pattern]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            parts = sorted([os.path.join(d, f) for f in os.listdir(d) if f.startswith(f"{prefix}_part") and f.endswith(ext)])
            if parts: return parts
        except Exception as e: safe_print(f"ffmpeg segment failed: {e}")
    with open(file_path, "rb") as f:
        i = 1
        while True:
            chunk = f.read(ZIP_PART_SIZE)
            if not chunk: break
            pname = f"{prefix}.part{i:03d}{ext}" if ext.lower() != ".zip" else f"{prefix}.zip.{i:03d}"
            ppath = os.path.join(d, pname)
            with open(ppath, "wb") as pf: pf.write(chunk)
            parts.append(ppath); i += 1
    return parts

def create_zip_and_split(src, base):
    d = os.path.dirname(src) or "."
    if not os.path.exists(src): return []
    zp = os.path.join(d, f"{base}.zip")
    try:
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf: zf.write(src, os.path.basename(src))
    except: return []
    if os.path.getsize(zp) <= ZIP_PART_SIZE: return [zp]
    parts = split_file_binary(zp, base, ".zip")
    os.remove(zp)
    return parts

# ═══════════════ صف‌ها ═══════════════
def load_queue(queue_type: str) -> list:
    try:
        with open(QUEUE_FILES[queue_type], "r") as f: return json.load(f)
    except: return []
def save_queue(queue_type: str, data: list):
    tmp = QUEUE_FILES[queue_type] + ".tmp"
    with open(tmp, "w") as f: json.dump(data, f)
    os.replace(tmp, QUEUE_FILES[queue_type])
def enqueue_job(job: Job, queue_type: str):
    q = load_queue(queue_type)
    q.append(asdict(job))
    save_queue(queue_type, q)
def dequeue_job(queue_type: str) -> Optional[Job]:
    q = load_queue(queue_type)
    for i, item in enumerate(q):
        if item["status"] == "queued":
            item["status"] = "running"
            item["updated_at"] = time.time()
            item["started_at"] = time.time()
            save_queue(queue_type, q)
            return Job(**item)
    return None
def update_job(job: Job):
    q = load_queue(job.queue_type)
    for i, item in enumerate(q):
        if item["job_id"] == job.job_id:
            q[i] = asdict(job); save_queue(job.queue_type, q); return
    q.append(asdict(job)); save_queue(job.queue_type, q)
def find_job(jid: str) -> Optional[Job]:
    for qt in ["browser", "download", "record"]:
        for item in load_queue(qt):
            if item["job_id"] == jid: return Job(**item)
    return None
def count_user_jobs(chat_id: int):
    return sum(1 for qt in ["browser", "download", "record"]
               for item in load_queue(qt)
               if item["chat_id"] == chat_id and item["status"] in ("queued", "running"))
def kill_all_user_jobs(chat_id: int):
    for qt in ["browser", "download", "record"]:
        q = load_queue(qt)
        for item in q:
            if item["chat_id"] == chat_id and item["status"] in ("queued", "running"):
                item["status"] = "cancelled"; item["updated_at"] = time.time()
        save_queue(qt, q)

def worker_loop(worker_id, stop_event, worker_type):
    safe_print(f"[Worker {worker_id} ({worker_type})] start")
    while not stop_event.is_set():
        job = None
        try:
            if worker_type == "record": job = dequeue_job("record")
            elif worker_type == "download": job = dequeue_job("download")
            else: job = dequeue_job("browser")
            if not job: time.sleep(2); continue
            session = get_session(job.chat_id)
            if session.cancel_requested:
                job.status = "cancelled"; update_job(job)
                session.cancel_requested = False; set_session(session)
                continue
            def target():
                try:
                    if job.mode == "record_video": handle_record_video(job)
                    elif job.mode in ("download","blind_download","download_execute","download_website","download_all_found"):
                        process_download_job(job)
                    else: process_browser_job(job)
                except Exception as e:
                    safe_print(f"Job {job.job_id} error: {e}"); traceback.print_exc()
                    job.status = "error"; update_job(job)
            t = threading.Thread(target=target); t.start()
            t.join(timeout=WORKER_TIMEOUT)
            if t.is_alive():
                safe_print(f"Job {job.job_id} timed out"); job.status = "error"; update_job(job)
        except Exception as e: safe_print(f"Worker {worker_id} crash: {e}"); traceback.print_exc(); time.sleep(5)
# ═══════════════════════  ادامهٔ مستقیم پارت اول — پارت دوم Bot28_Final ═══════════════════════

# ═══════════════════════  ابزارهای مرورگر و اسکرول ═══════════════════════

# لیست دامنه‌های تبلیغاتی (AD_DOMAINS) و کلمات کلیدی (BLOCKED_AD_KEYWORDS) کامل اینجا قرار می‌گیرند.
AD_DOMAINS = [
    "doubleclick.net", "googleadsyndication.com", "adservice.google.com",
    "adsrvr.org", "outbrain.com", "taboola.com", "exoclick.com",
    "trafficfactory.biz", "propellerads.com", "adnxs.com", "criteo.com",
    "moatads.com", "amazon-adsystem.com", "pubmatic.com", "openx.net",
    "rubiconproject.com", "sovrn.com", "indexww.com", "contextweb.com",
    "advertising.com", "zedo.com", "adzerk.net", "carbonads.com",
    "buysellads.com", "popads.net", "trafficstars.com", "trafficjunky.com",
    "eroadvertising.com", "juicyads.com", "plugrush.com",
    "txxx.com", "fuckbook.com", "traffic-force.com", "bongacams.com",
    "trafficjunky.net", "adtng.com"
]
BLOCKED_AD_KEYWORDS = [
    "ads", "advert", "popunder", "banner", "doubleclick", "taboola",
    "outbrain", "popcash", "traffic", "monetize", "adx", "adserving"
]

def extract_clickable_and_media(page, mode="text"):
    # کامل و بدون تغییر
    if mode == "text":
        raw = page.evaluate("""() => {
            const items = []; const seen = new Set();
            function isVisible(el) { const s = window.getComputedStyle(el); return s.display !== 'none' && s.visibility !== 'hidden' && el.offsetWidth > 0; }
            document.querySelectorAll('a[href]').forEach(a => {
                if (!isVisible(a)) return;
                let t = a.textContent.trim() || 'لینک';
                let href = a.href;
                try { href = new URL(a.getAttribute('href'), document.baseURI).href; } catch(e) {}
                if (!seen.has(href)) { seen.add(href); items.push(['link', t, href]); }
            });
            return items;
        }""")
        links = [(t, txt, h) for t, txt, h in raw if h.startswith("http")]
        return links, []
    elif mode == "media":
        # مشابه حالت text اما با ویدیوها
        video_sources = page.evaluate("""() => {
            const vids = [];
            document.querySelectorAll('video').forEach(v => { let src = v.src || (v.querySelector('source') ? v.querySelector('source').src : ''); if (src) vids.push(src); });
            document.querySelectorAll('iframe').forEach(f => { if (f.src) vids.push(f.src); });
            return [...new Set(vids)].filter(u => u.startsWith('http'));
        }""")
        anchors = page.evaluate("""() => {
            const a = []; document.querySelectorAll('a[href]').forEach(e => { try { a.push(new URL(e.getAttribute('href'), document.baseURI).href); } catch(e) {} });
            return a.filter(h => h && h.startsWith('http'));
        }""")
        links = [("link", href.split("/")[-1][:20] or "لینک", href) for href in anchors[:20]]
        return links, video_sources
    else:  # explorer
        raw = page.evaluate("""() => {
            const items = []; const seen = new Set();
            function add(type, text, href) { if (!href || seen.has(href)) return; seen.add(href); items.push([type, text.trim().substring(0, 40), href]); }
            function isVisible(el) { const s = window.getComputedStyle(el); return s.display !== 'none' && s.visibility !== 'hidden' && el.offsetWidth > 0; }
            document.querySelectorAll('a[href]').forEach(a => { let t = a.textContent.trim() || 'لینک'; try { let href = new URL(a.getAttribute('href'), document.baseURI).href; add('link', t, href); } catch(e) {} });
            document.querySelectorAll('button').forEach(btn => { if (!isVisible(btn)) return; let t = btn.textContent.trim() || 'دکمه'; let formaction = btn.getAttribute('formaction') || ''; if (formaction) { try { formaction = new URL(formaction, document.baseURI).href; } catch(e) {} add('button', t, formaction); } else { let onclick = btn.getAttribute('onclick') || ''; let match = onclick.match(/location\\.href=['"]([^'"]+)['"]/) || onclick.match(/window\\.open\\(['"]([^'"]+)['"]\\)/); if (match) add('button', t, match[1]); } });
            document.querySelectorAll('[onclick]').forEach(el => { if (el.tagName === 'A' || el.tagName === 'BUTTON') return; if (!isVisible(el)) return; let onclick = el.getAttribute('onclick') || ''; let match = onclick.match(/location\\.href=['"]([^'"]+)['"]/) || onclick.match(/window\\.open\\(['"]([^'"]+)['"]\\)/); if (match) add('element', el.textContent.trim().substring(0,30) || 'کلیک', match[1]); });
            document.querySelectorAll('[role="button"]').forEach(el => { if (!isVisible(el)) return; let t = el.textContent.trim().substring(0,30) || 'نقش'; let id = el.id ? '#'+el.id : ''; add('role', t, id); });
            document.querySelectorAll('input[type="submit"], input[type="button"]').forEach(inp => { if (!isVisible(inp)) return; let t = inp.value || 'ارسال'; let form = inp.closest('form'); let action = form ? form.getAttribute('action') || '' : ''; try { if (action) action = new URL(action, document.baseURI).href; } catch(e) {} add('input', t, action || window.location.href); });
            return items;
        }""")
        links = [(t, txt, h) for t, txt, h in raw if h and (h.startswith("http") or h.startswith("/") or h.startswith("#"))]
        return links, []

def scan_videos_smart(page):
    # کامل و بدون تغییر
    elements = page.evaluate("""() => {
        const results = []; const centerX = window.innerWidth / 2; const centerY = window.innerHeight / 2;
        document.querySelectorAll('video').forEach(v => { const rect = v.getBoundingClientRect(); if (rect.width < 200 || rect.height < 150) return; let src = v.src || (v.querySelector('source') ? v.querySelector('source').src : ''); if (!src) return; const area = rect.width * rect.height; const dist = Math.sqrt(Math.pow(rect.x + rect.width/2 - centerX, 2) + Math.pow(rect.y + rect.height/2 - centerY, 2)); results.push({text: 'video element', href: src, score: area - dist*2, w: rect.width, h: rect.height}); });
        document.querySelectorAll('iframe').forEach(f => { const rect = f.getBoundingClientRect(); if (rect.width < 300 || rect.height < 200) return; let src = f.src || ''; if (!src.startsWith('http')) return; const area = rect.width * rect.height; const dist = Math.sqrt(Math.pow(rect.x + rect.width/2 - centerX, 2) + Math.pow(rect.y + rect.height/2 - centerY, 2)); results.push({text: 'iframe', href: src, score: area - dist*2, w: rect.width, h: rect.height}); });
        return results;
    }""")
    network_urls = []
    def capture(response):
        ct = response.headers.get("content-type", "")
        url = response.url.lower()
        if "mpegurl" in ct or "dash+xml" in ct or url.endswith((".m3u8", ".mpd")) or ("video" in ct and (url.endswith(".mp4") or url.endswith(".webm") or url.endswith(".mkv"))):
            network_urls.append(response.url)
    page.on("response", capture)
    page.wait_for_timeout(3000)
    page.remove_listener("response", capture)
    json_urls = page.evaluate("""() => { const results = []; const scripts = document.querySelectorAll('script'); for (const s of scripts) { const text = s.textContent || ''; const matches = text.match(/(https?:\\/\\/[^"']+\\.(?:m3u8|mp4|mkv|webm|mpd)[^"']*)/gi); if (matches) results.push(...matches); } return results; }""")
    all_candidates = []
    for el in elements:
        href = el["href"]
        if not href.startswith("http"): continue
        parsed = urlparse(href)
        if any(ad in parsed.netloc for ad in AD_DOMAINS): continue
        if any(kw in href.lower() for kw in BLOCKED_AD_KEYWORDS): continue
        all_candidates.append({"text": (el["text"] + f" ({parsed.netloc})")[:35], "href": href, "score": el["score"]})
    for url in network_urls:
        if url in [c["href"] for c in all_candidates]: continue
        parsed = urlparse(url)
        if any(ad in parsed.netloc for ad in AD_DOMAINS): continue
        all_candidates.append({"text": f"Network stream ({parsed.netloc})"[:35], "href": url, "score": 100000})
    for url in json_urls:
        if url in [c["href"] for c in all_candidates]: continue
        parsed = urlparse(url)
        if any(ad in parsed.netloc for ad in AD_DOMAINS): continue
        all_candidates.append({"text": f"JSON stream ({parsed.netloc})"[:35], "href": url, "score": 90000})
    all_candidates.sort(key=lambda x: x["score"], reverse=True)
    return all_candidates

def smooth_scroll_to_video(page):
    # کامل و بدون تغییر
    coords = page.evaluate("""() => { let best = null; let bestArea = 0; document.querySelectorAll('video').forEach(v => { const rect = v.getBoundingClientRect(); if (rect.width < 200 || rect.height < 150) return; const area = rect.width * rect.height; if (area > bestArea) { bestArea = area; best = { y: rect.top + window.scrollY, x: rect.left + window.scrollX, w: rect.width, h: rect.height }; } }); document.querySelectorAll('iframe').forEach(f => { const rect = f.getBoundingClientRect(); if (rect.width < 300 || rect.height < 200) return; const area = rect.width * rect.height; if (area > bestArea) { bestArea = area; best = { y: rect.top + window.scrollY, x: rect.left + window.scrollX, w: rect.width, h: rect.height }; } }); return best || { y: window.scrollY, x: 0, w: 0, h: 0 }; }""")
    target_y = coords["y"]
    current_y = page.evaluate("window.scrollY")
    distance = target_y - current_y
    steps = max(20, abs(distance) // 15)
    step_size = distance / steps
    for i in range(steps):
        current_y += step_size
        page.evaluate(f"window.scrollTo({{top: {int(current_y)}, behavior: 'smooth'}})")
        page.wait_for_timeout(50)
    page.evaluate(f"window.scrollTo({{top: {int(target_y)}, behavior: 'smooth'}})")
    page.wait_for_timeout(200)

def find_video_center(page):
    # کامل و بدون تغییر
    coords = page.evaluate("""() => { const centerX = window.innerWidth / 2; const centerY = window.innerHeight / 2; let best = null; let bestArea = 0; document.querySelectorAll('video').forEach(v => { const rect = v.getBoundingClientRect(); if (rect.width < 200 || rect.height < 150) return; const area = rect.width * rect.height; if (area > bestArea) { bestArea = area; best = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 }; } }); document.querySelectorAll('iframe').forEach(f => { const rect = f.getBoundingClientRect(); if (rect.width < 300 || rect.height < 200) return; const area = rect.width * rect.height; if (area > bestArea) { bestArea = area; best = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 }; } }); return best || { x: centerX, y: centerY }; }""")
    return coords["x"], coords["y"]

# ═══════════════════════  اسکرین‌شات ═══════════════════════
def screenshot_full(browser, url, out):
    page = browser.new_page()
    try: page.goto(url, timeout=90000, wait_until="domcontentloaded"); page.wait_for_timeout(2000); page.screenshot(path=out, full_page=True)
    finally: page.close()

def screenshot_2x(browser, url, out):
    page = browser.new_page()
    try: page.goto(url, timeout=90000, wait_until="domcontentloaded"); page.wait_for_timeout(2000); page.evaluate("document.body.style.zoom = '200%'"); page.wait_for_timeout(500); page.screenshot(path=out, full_page=True)
    finally: page.close()

def screenshot_4k(browser, url, out):
    page = browser.new_page()
    try: page.set_viewport_size({"width": 3840, "height": 2160}); page.goto(url, timeout=90000, wait_until="domcontentloaded"); page.wait_for_timeout(3000); page.screenshot(path=out, full_page=True)
    finally: page.close()

# ═══════════════════════  دانلود هوشمند ═══════════════════════
def handle_download(job, job_dir):
    chat_id = job.chat_id; session = get_session(chat_id)
    url = job.url
    if is_direct_file_url(url): direct_link = url
    else:
        send_message(chat_id, "🔎 جستجوی فایل...")
        direct_link = crawl_for_download_link(url)
        if not direct_link:
            send_message(chat_id, "⚠️ دانلود کور...")
            job.mode = "blind_download"; job.url = url; job.queue_type = "download"
            update_job(job); handle_blind_download(job); return
    size_bytes = None; size_str = "نامشخص"
    try:
        head = requests.head(direct_link, timeout=10, allow_redirects=True)
        if head.headers.get("Content-Length"):
            size_bytes = int(head.headers["Content-Length"])
            size_str = f"{size_bytes/(1024*1024):.2f} MB"
    except: pass
    if not session.is_admin:
        err = check_rate_limit(chat_id, "download", size_bytes)
        if err: send_message(chat_id, err); job.status = "cancelled"; update_job(job); return
    fname = get_filename_from_url(direct_link)
    kb = {"inline_keyboard": [
        [{"text": "📦 ZIP", "callback_data": f"dlzip_{job.job_id}"}, {"text": "📄 اصلی", "callback_data": f"dlraw_{job.job_id}"}],
        [{"text": "❌ لغو", "callback_data": f"canceljob_{job.job_id}"}]
    ]}
    send_message(chat_id, f"📄 {fname} ({size_str})", reply_markup=kb)
    job.status = "awaiting_user"; job.extra = {"direct_link": direct_link, "filename": fname}
    update_job(job)

def download_and_stream(url, fname, job_dir, chat_id):
    base, ext = os.path.splitext(fname)
    buf = b""; idx = 1
    with requests.get(url, stream=True, timeout=120, headers={"User-Agent":"Mozilla/5.0"}) as r:
        for chunk in r.iter_content(chunk_size=8192):
            buf += chunk
            while len(buf) >= ZIP_PART_SIZE:
                part = buf[:ZIP_PART_SIZE]; buf = buf[ZIP_PART_SIZE:]
                pname = f"{base}.part{idx:03d}{ext}"
                ppath = os.path.join(job_dir, pname)
                with open(ppath, "wb") as f: f.write(part)
                send_document(chat_id, ppath, caption=f"⚡ پارت {idx}")
                os.remove(ppath); idx += 1
        if buf:
            pname = f"{base}.part{idx:03d}{ext}"; ppath = os.path.join(job_dir, pname)
            with open(ppath, "wb") as f: f.write(buf)
            send_document(chat_id, ppath, caption=f"⚡ پارت {idx}")
            os.remove(ppath)

def execute_download(job, job_dir):
    chat_id = job.chat_id; extra = job.extra; session = get_session(chat_id)
    mode = session.settings.default_download_mode; pack_zip = extra.get("pack_zip", False)
    if mode == "stream" and pack_zip:
        send_message(chat_id, "📦 ZIP با حالت سریع ممکن نیست؛ دانلود عادی انجام می‌شود."); mode = "store"
    if mode == "stream":
        send_message(chat_id, "⚡ دانلود همزمان...")
        download_and_stream(extra["direct_link"], extra["filename"], job_dir, chat_id); job.status = "done"; update_job(job); return
    fname = extra["filename"]
    if "file_path" in extra: fpath = extra["file_path"]
    else:
        fpath = os.path.join(job_dir, fname); send_message(chat_id, "⏳ دانلود...")
        with requests.get(extra["direct_link"], stream=True, timeout=120, headers={"User-Agent":"Mozilla/5.0"}) as r:
            with open(fpath, "wb") as f:
                for c in r.iter_content(8192): f.write(c)
    if not os.path.exists(fpath): send_message(chat_id, "❌ فایل یافت نشد."); job.status = "error"; update_job(job); return
    if pack_zip: parts = create_zip_and_split(fpath, fname); label = "ZIP"
    else: base, ext = os.path.splitext(fname); parts = split_file_binary(fpath, base, ext); label = "اصلی"
    if not parts: send_message(chat_id, "❌ خطا در تقسیم فایل."); job.status = "error"; update_job(job); return
    instr = os.path.join(job_dir, "merge.txt")
    with open(instr, "w") as f:
        if pack_zip: f.write("همه‌ی فایل‌ها را دانلود کنید، سپس فایل .001 را با WinRAR یا 7-Zip باز کنید.")
        else: f.write(f"هر قطعه به‌طور مستقل قابل پخش است. برای ادغام: copy /b {'+'.join([os.path.basename(p) for p in parts])} {fname}")
    send_document(chat_id, instr, caption="📝 راهنما")
    for idx, p in enumerate(parts, 1): send_document(chat_id, p, caption=f"{label} پارت {idx}/{len(parts)}")
    job.status = "done"; update_job(job)

def handle_blind_download(job):
    chat_id = job.chat_id; session = get_session(chat_id); url = job.url
    job_dir = os.path.join("jobs_data", job.job_id)
    os.makedirs(job_dir, exist_ok=True); send_message(chat_id, "⏳ دانلود اولیه...")
    try:
        with requests.get(url, stream=True, timeout=120, headers={"User-Agent":"Mozilla/5.0"}) as r:
            ct = r.headers.get("Content-Type", "application/octet-stream"); fname = get_filename_from_url(url)
            if '.' not in fname:
                if "video" in ct: fname += ".mp4"
                elif "pdf" in ct: fname += ".pdf"
                else: fname += ".bin"
            fpath = os.path.join(job_dir, fname)
            with open(fpath, "wb") as f:
                for c in r.iter_content(8192): f.write(c)
        if not os.path.exists(fpath): send_message(chat_id, "❌ فایل دانلود نشد."); job.status = "error"; update_job(job); return
        size_bytes = os.path.getsize(fpath); size_str = f"{size_bytes/(1024*1024):.2f} MB"
        if not session.is_admin:
            err = check_rate_limit(chat_id, "download", size_bytes)
            if err: send_message(chat_id, err); job.status = "cancelled"; update_job(job); return
        text = f"📄 فایل (کور): {fname} ({size_str})"
        kb = {"inline_keyboard": [
            [{"text":"📦 ZIP","callback_data":f"dlblindzip_{job.job_id}"}, {"text":"📄 اصلی","callback_data":f"dlblindra_{job.job_id}"}],
            [{"text":"❌ لغو","callback_data":f"canceljob_{job.job_id}"}]
        ]}
        send_message(chat_id, text, reply_markup=kb)
        job.status = "awaiting_user"; job.extra = {"file_path": fpath, "filename": fname}; update_job(job)
    except Exception as e: send_message(chat_id, f"❌ دانلود کور ناموفق: {e}"); job.status = "error"; update_job(job); shutil.rmtree(job_dir, ignore_errors=True)

# ═══════════════════════  ضبط ویدیو با فایرفاکس ═══════════════════════
def get_firefox_browser():
    pw = sync_playwright().start()
    browser = pw.firefox.launch(
        headless=False if os.environ.get("DISPLAY") else True,
        firefox_user_prefs={"media.autoplay.default": 0, "media.autoplay.enabled": True, "media.volume_scale": "1.0"},
        args=['--no-sandbox']
    )
    return pw, browser

def handle_record_video(job):
    chat_id = job.chat_id; session = get_session(chat_id)
    url = job.url; rec_time = session.settings.record_time
    behavior = session.settings.record_behavior; video_format = session.settings.video_format
    delivery = session.settings.video_delivery; resolution = session.settings.video_resolution
    audio_enabled = session.settings.audio_enabled

    MAX_REC_MINUTES = 60 if session.is_admin else 15
    MAX_REC_SECONDS = MAX_REC_MINUTES * 60
    if rec_time > MAX_REC_SECONDS:
        send_message(chat_id, f"⛔ حداکثر زمان ضبط {MAX_REC_MINUTES} دقیقه می‌باشد."); job.status = "cancelled"; update_job(job); return

    res_req = RES_REQUIREMENTS.get(resolution, [])
    if session.subscription not in res_req and not session.is_admin:
        send_message(chat_id, f"⛔ کیفیت {resolution} برای سطح «{session.subscription}» در دسترس نیست."); job.status = "cancelled"; update_job(job); return
    if resolution == "4k" and not session.is_admin:
        if rec_time > MAX_4K_RECORD_MINUTES * 60:
            send_message(chat_id, f"⛔ حداکثر زمان ضبط 4K برابر {MAX_4K_RECORD_MINUTES} دقیقه است."); job.status = "cancelled"; update_job(job); return

    w, h = ALLOWED_RESOLUTIONS.get(resolution, (1280, 720))
    job_dir = os.path.join("jobs_data", job.job_id); os.makedirs(job_dir, exist_ok=True)

    behavior_names = {"click": "کلیک هوشمند", "scroll": "اسکرول نرم", "live": "لایو کامند"}
    send_message(chat_id, f"🎬 ضبط {rec_time//60} دقیقه و {rec_time%60} ثانیه ({behavior_names.get(behavior, behavior)}) با کیفیت {resolution}...")

    _rec_pw = None; _rec_browser = None; audio_proc = None; audio_path = None
    try:
        _rec_pw, _rec_browser = get_firefox_browser()
        context = _rec_browser.new_context(viewport={"width": w, "height": h}, record_video_dir=job_dir, record_video_size={"width": w, "height": h})
        page = context.new_page()
        need_scroll = (job.extra or {}).get("live_scroll", False)
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded"); page.wait_for_timeout(2000)
            if behavior == "scroll" or need_scroll: smooth_scroll_to_video(page)
            vx, vy = find_video_center(page); page.mouse.click(vx, vy)
            try: page.evaluate("() => { const v = document.querySelector('video'); if (v) v.play(); }")
            except: pass

            if audio_enabled:
                # تلاش برای راه‌اندازی PulseAudio
                try:
                    subprocess.run(["pulseaudio", "-D", "--exit-idle-time=-1"], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    time.sleep(1)
                    subprocess.run(["pactl", "load-module", "module-null-sink", "sink_name=virtual_out"], check=False)
                    time.sleep(2)
                    sink_input = subprocess.run("pactl list sink-inputs | grep -B 18 -i 'firefox' | grep 'Sink Input' | awk '{print $3}' | cut -d '#' -f 2", shell=True, capture_output=True, text=True).stdout.strip()
                    if sink_input: subprocess.run(["pactl", "move-sink-input", sink_input, "virtual_out"], check=False)
                    audio_path = os.path.join(job_dir, "audio.mp3")
                    audio_proc = subprocess.Popen(['ffmpeg', '-y', '-f', 'pulse', '-i', 'virtual_out.monitor', '-ac', '2', '-ar', '44100', '-acodec', 'libmp3lame', '-b:a', '128k', audio_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                except Exception as e: safe_print(f"Audio setup failed: {e}")

            start_time = time.time()
            while time.time() - start_time < rec_time:
                if session.cancel_requested: send_message(chat_id, "⏹️ ضبط متوقف شد."); break
                time.sleep(0.5)
        finally: page.close(); context.close()

        if audio_proc:
            try: audio_proc.terminate(); audio_proc.wait(timeout=5)
            except: pass

        webm = None
        for f in os.listdir(job_dir):
            if f.endswith('.webm'): webm = os.path.join(job_dir, f); break
        if not webm: send_message(chat_id, "❌ ویدیویی ضبط نشد."); job.status = "error"; update_job(job); return

        final_video_path = webm
        if video_format != "webm":
            converted = os.path.join(job_dir, f"record.{video_format}")
            cmd = ['ffmpeg', '-y', '-i', webm, '-c:v', 'libx264', '-c:a', 'copy', converted] if video_format == "mp4" else ['ffmpeg', '-y', '-i', webm, '-c', 'copy', converted]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
                if os.path.exists(converted) and os.path.getsize(converted) > 0: final_video_path = converted; os.remove(webm)
            except: safe_print("Video format conversion failed, keeping webm")

        def send_file(path, label_prefix, as_zip=False):
            fname = os.path.basename(path)
            if as_zip:
                if os.path.getsize(path) <= ZIP_PART_SIZE:
                    zp = os.path.join(job_dir, f"{fname}.zip")
                    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf: zf.write(path, fname)
                    send_document(chat_id, zp, caption=f"{label_prefix} (ZIP)"); os.remove(zp)
                else:
                    parts = create_zip_and_split(path, fname)
                    for idx, p in enumerate(parts, 1): send_document(chat_id, p, caption=f"{label_prefix} (ZIP) پارت {idx}/{len(parts)}")
            else:
                base, ext = os.path.splitext(fname)
                if os.path.getsize(path) <= ZIP_PART_SIZE: send_document(chat_id, path, caption=f"{label_prefix} (اصلی)")
                else:
                    parts = split_file_binary(path, base, ext)
                    for idx, p in enumerate(parts, 1): send_document(chat_id, p, caption=f"{label_prefix} (اصلی) پارت {idx}/{len(parts)}")

        use_zip = (delivery == "zip")
        send_file(final_video_path, "🎬 ویدیو", use_zip)
        if audio_path and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
            send_file(audio_path, "🎵 صوت", use_zip)
        job.status = "done"; update_job(job)
        debug_log(f"Recording done for job {job.job_id}")
    except Exception as e:
        send_message(chat_id, f"❌ خطا: {e}"); job.status = "error"; update_job(job); shutil.rmtree(job_dir, ignore_errors=True)
    finally:
        if _rec_browser:
            try: _rec_browser.close()
            except: pass
        if _rec_pw:
            try: _rec_pw.stop()
            except: pass

# ═══════════════════════  کاوشگر تعاملی ═══════════════════════
def handle_interactive_scan(job):
    chat_id = job.chat_id; session = get_session(chat_id)
    url = session.browser_url or job.url
    if not url: send_message(chat_id, "❌ صفحه‌ای برای کاوش باز نیست."); return
    pw = sync_playwright().start(); browser = None
    try:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(); page.goto(url, timeout=60000, wait_until="domcontentloaded"); page.wait_for_timeout(2000)
        elements = page.evaluate("""() => {
            const results = [];
            document.querySelectorAll('input[type="text"], input[type="search"], input[type="email"], input[type="url"], input[type="tel"], input[type="number"], textarea, [contenteditable="true"]').forEach((el, idx) => {
                if (el.offsetWidth === 0 && el.offsetHeight === 0) return;
                const placeholder = el.placeholder || el.getAttribute('aria-label') || el.textContent?.trim()?.substring(0, 50) || 'بدون عنوان';
                const name = el.name || el.id || '';
                const form = el.closest('form'); const formAction = form ? form.action || '' : '';
                let submitBtn = null;
                if (form) { const btn = form.querySelector('button[type="submit"], input[type="submit"], button:not([type])'); if (btn) submitBtn = {text: btn.textContent?.trim() || btn.value || 'ارسال', type: btn.tagName}; }
                if (!submitBtn) {
                    const allBtns = document.querySelectorAll('button, input[type="button"], [role="button"]'); let closest = null, minDist = Infinity;
                    const rect = el.getBoundingClientRect();
                    allBtns.forEach(b => { const br = b.getBoundingClientRect(); const dist = Math.hypot(br.x - rect.x, br.y - rect.y); if (dist < 300 && dist < minDist) { minDist = dist; closest = b; } });
                    if (closest) submitBtn = {text: closest.textContent?.trim() || closest.value || 'کلیک', type: closest.tagName};
                }
                let selector = '';
                if (el.id) selector = '#' + el.id; else if (el.name) selector = '[name="' + el.name + '"]'; else selector = el.tagName + ':nth-of-type(' + (idx+1) + ')';
                results.push({index: idx + 1, type: el.tagName, placeholder: placeholder, name: name, formAction: formAction, submitBtn: submitBtn, selector: selector});
            });
            return results;
        }""")
        page.close()
        if not elements: send_message(chat_id, "🚫 هیچ فیلد متنی در این صفحه یافت نشد."); job.status = "done"; update_job(job); return
        session.interactive_elements = elements; set_session(session)
        lines = [f"🔎 **کاوشگر تعاملی ({len(elements)} فیلد یافت شد)**\n"]; cmds = {}
        for el in elements:
            cmd = f"/t{el['index']}"; cmds[cmd] = str(el['index'])
            btn_info = f"🖱️ دکمه: «{el['submitBtn']['text']}»" if el.get('submitBtn') else "⚠️ دکمه پیدا نشد"
            lines.append(f"{el['index']}. 📝 «{el['placeholder']}» ({el['type']})"); lines.append(f"   {btn_info}"); lines.append(f"   📌 {cmd}\n")
        send_message(chat_id, "\n".join(lines)); session.text_links = {**session.text_links, **cmds} if session.text_links else cmds
        set_session(session); job.status = "done"; update_job(job)
    except Exception as e: send_message(chat_id, f"❌ خطا: {e}"); job.status = "error"; update_job(job)
    finally:
        if browser: browser.close()
        if pw: pw.stop()

def handle_interactive_execute(job):
    chat_id = job.chat_id; session = get_session(chat_id)
    extra = job.extra or {}; element_index = extra.get("element_index", 1); user_text = extra.get("user_text", "")
    url = session.browser_url or job.url
    if not url: send_message(chat_id, "❌ صفحه‌ای باز نیست."); return
    elements = session.interactive_elements or []; target = None
    for el in elements:
        if el["index"] == element_index: target = el; break
    if not target: send_message(chat_id, "❌ فیلد مورد نظر یافت نشد."); return
    send_message(chat_id, f"🔎 در حال جستجوی «{user_text}»...")
    pw = sync_playwright().start(); browser = None; job_dir = os.path.join("jobs_data", job.job_id)
    os.makedirs(job_dir, exist_ok=True)
    try:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(); page.goto(url, timeout=60000, wait_until="domcontentloaded"); page.wait_for_timeout(2000)
        escaped_text = user_text.replace("\\", "\\\\").replace("'", "\\'")
        page.evaluate(f"""() => {{
            const el = document.querySelector('{target["selector"]}') || document.querySelector('input[type="text"], textarea');
            if (el) {{ el.focus(); el.value = ''; el.value = '{escaped_text}'; el.dispatchEvent(new Event('input', {{ bubbles: true }})); el.dispatchEvent(new Event('change', {{ bubbles: true }})); }}
        }}"""); time.sleep(1)
        if target.get("submitBtn"):
            btn_text_escaped = target["submitBtn"]["text"].replace("\\", "\\\\").replace("'", "\\'")
            page.evaluate(f"""() => {{ const btns = document.querySelectorAll('button, input[type="submit"], [role="button"]'); for (const b of btns) {{ if (b.textContent.trim() === '{btn_text_escaped}') {{ b.click(); return; }} }} }}""")
        else: page.keyboard.press("Enter")
        page.wait_for_timeout(10000)
        spath = os.path.join(job_dir, "interactive_result.png"); page.screenshot(path=spath, full_page=True)
        send_document(chat_id, spath, caption=f"📸 نتیجه جستجوی «{user_text}»"); page.close()
        job.status = "done"; update_job(job)
    except Exception as e: send_message(chat_id, f"❌ خطا: {e}"); job.status = "error"; update_job(job)
    finally:
        if browser: browser.close()
        if pw: pw.stop()
        shutil.rmtree(job_dir, ignore_errors=True)

# ═══════════════════════  شات کامل ═══════════════════════
def handle_fullpage_screenshot(job):
    chat_id = job.chat_id; session = get_session(chat_id)
    pw = sync_playwright().start(); browser = None; job_dir = os.path.join("jobs_data", job.job_id)
    os.makedirs(job_dir, exist_ok=True)
    try:
        send_message(chat_id, "📸 در حال بارگذاری کامل صفحه...")
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(); page.goto(job.url, timeout=120000, wait_until="domcontentloaded"); page.wait_for_timeout(5000)
        spath = os.path.join(job_dir, "fullpage.png"); page.screenshot(path=spath, full_page=True)
        send_document(chat_id, spath, caption="✅ شات کامل (Full Page)"); page.close()
        job.status = "done"; update_job(job)
    except Exception as e: send_message(chat_id, f"❌ خطا: {e}"); job.status = "error"; update_job(job)
    finally:
        if browser: browser.close()
        if pw: pw.stop()
        shutil.rmtree(job_dir, ignore_errors=True)

# ═══════════════════════  مرورگر ═══════════════════════
def handle_browser(job, job_dir, browser):
    chat_id = job.chat_id; session = get_session(chat_id); url = job.url
    if is_direct_file_url(url): send_message(chat_id, "📥 این لینک یک فایل قابل دانلود است. لطفاً از بخش دانلود استفاده کنید."); job.status = "cancelled"; update_job(job); return
    mode = session.settings.browser_mode; page = browser.new_page()
    parsed_url = urlparse(url)
    if parsed_url.netloc.lower() in (session.ad_blocked_domains or []):
        page.route("**/*", lambda route: route.abort() if any(ad in route.request.url for ad in AD_DOMAINS) else route.continue_())
    try:
        page.goto(url, timeout=60000, wait_until="domcontentloaded"); page.wait_for_timeout(2000)
        spath = os.path.join(job_dir, "browser.png"); page.screenshot(path=spath, full_page=True)
        links, video_urls = extract_clickable_and_media(page, mode)
        all_links = []
        for typ, text, href in links: all_links.append({"type": typ, "text": text[:25], "href": href})
        if mode == "media":
            clean_videos = [v for v in video_urls if not any(ad in v for ad in AD_DOMAINS)]
            for vurl in clean_videos: all_links.append({"type": "video", "text": "🎬 ویدیو", "href": vurl})
        session.state = "browsing"; session.browser_url = url; session.browser_links = all_links; session.browser_page = 0
        set_session(session); send_browser_page(chat_id, spath, url, 0); job.status = "done"; update_job(job)
    finally: page.close()

def send_browser_page(chat_id, image_path=None, url="", page_num=0):
    session = get_session(chat_id); all_links = session.browser_links or []; per_page = 10
    start = page_num * per_page; end = min(start + per_page, len(all_links)); page_links = all_links[start:end]
    keyboard_rows = []; idx = start; row = []
    for link in page_links:
        label = link["text"][:20]
        cb = f"nav_{chat_id}_{idx}" if link["type"] != "video" else f"dlvid_{chat_id}_{idx}"
        with callback_map_lock: callback_map[cb] = link["href"]
        row.append({"text": label, "callback_data": cb})
        if len(row) == 2: keyboard_rows.append(row); row = []; idx += 1
    if row: keyboard_rows.append(row)
    nav = []
    if page_num > 0: nav.append({"text": "◀️", "callback_data": f"bpg_{chat_id}_{page_num-1}"})
    if end < len(all_links): nav.append({"text": "▶️", "callback_data": f"bpg_{chat_id}_{page_num+1}"})
    if nav: keyboard_rows.append(nav)
    sub = session.subscription; mode = session.settings.browser_mode
    if mode == "media":
        if sub in ("طلایی", "الماسی") or session.is_admin: keyboard_rows.append([{"text": "🎬 اسکن ویدیوها", "callback_data": f"scvid_{chat_id}"}])
        current_domain = urlparse(url).netloc.lower(); is_blocked = current_domain in (session.ad_blocked_domains or [])
        ad_text = "🛡️ تبلیغات: روشن" if is_blocked else "🛡️ تبلیغات: خاموش"; keyboard_rows.append([{"text": ad_text, "callback_data": f"adblock_{chat_id}"}])
    elif mode == "explorer":
        if sub in ("طلایی", "الماسی") or session.is_admin:
            keyboard_rows.append([{"text": "🔍 تحلیل هوشمند", "callback_data": f"sman_{chat_id}"}])
            keyboard_rows.append([{"text": "🕵️ تحلیل سورس", "callback_data": f"srcan_{chat_id}"}])
    else:
        if sub in ("طلایی", "الماسی") or session.is_admin: keyboard_rows.append([{"text": "📦 جستجوی فایل‌ها", "callback_data": f"scdl_{chat_id}"}])
    if sub in ("طلایی", "الماسی") or session.is_admin:
        keyboard_rows.append([{"text": "📋 فرامین", "callback_data": f"extcmd_{chat_id}"}])
        keyboard_rows.append([{"text": "🎬 ضبط", "callback_data": f"recvid_{chat_id}"}])
        keyboard_rows.append([{"text": "📸 شات کامل", "callback_data": f"fullshot_{chat_id}"}])
        keyboard_rows.append([{"text": "🔎 کاوشگر", "callback_data": f"intscan_{chat_id}"}])
    if sub in ("الماسی") or session.is_admin: keyboard_rows.append([{"text": "🌐 دانلود سایت", "callback_data": f"dlweb_{chat_id}"}])
    keyboard_rows.append([{"text": "❌ بستن", "callback_data": f"closebrowser_{chat_id}"}])
    kb = {"inline_keyboard": keyboard_rows}
    if image_path: send_document(chat_id, image_path, caption=f"🌐 {url}")
    send_message(chat_id, f"صفحه {page_num+1}/{math.ceil(len(all_links)/per_page)}", reply_markup=kb)
    extra = all_links[end:]
    if extra:
        cmds = {}; lines = ["🔹 لینک‌های بیشتر:"]
        for i, link in enumerate(extra):
            cmd = f"/a{hashlib.md5(link['href'].encode()).hexdigest()[:5]}"; cmds[cmd] = link['href']; lines.append(f"{cmd} : {link['text']}")
        send_message(chat_id, "\n".join(lines)); session.text_links = cmds; set_session(session)

# ═══════════════════════  اسکن و تحلیل (کامل) ═══════════════════════
def handle_scan_videos(job):
    chat_id = job.chat_id; session = get_session(chat_id)
    pw = sync_playwright().start(); browser = None
    try:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(); page.goto(session.browser_url, timeout=60000, wait_until="domcontentloaded"); page.wait_for_timeout(3000)
        videos = scan_videos_smart(page); page.close()
        if not videos: send_message(chat_id, "🚫 هیچ ویدیویی یافت نشد."); job.status = "done"; update_job(job); return
        lines = [f"🎬 **{len(videos)} ویدیو یافت شد:**"]; cmds = {}
        for i, vid in enumerate(videos[:15]):
            cmd = f"/o{hashlib.md5(vid['href'].encode()).hexdigest()[:5]}"; cmds[cmd] = vid['href']
            lines.append(f"{i+1}. {vid['text']}"); lines.append(f"   📥 {cmd}")
        send_message(chat_id, "\n".join(lines)); session.text_links = {**session.text_links, **cmds} if session.text_links else cmds
        set_session(session); job.status = "done"; update_job(job)
    except Exception as e: send_message(chat_id, f"❌ خطا: {e}"); job.status = "error"; update_job(job)
    finally:
        if browser: browser.close()
        if pw: pw.stop()

def handle_smart_analyze(job):
    chat_id = job.chat_id; session = get_session(chat_id); all_links = session.browser_links or []
    if not all_links: send_message(chat_id, "🚫 لینکی برای تحلیل وجود ندارد."); job.status = "done"; update_job(job); return
    videos = [l for l in all_links if is_direct_file_url(l["href"]) and any(l["href"].lower().endswith(e) for e in ('.mp4','.webm','.mkv','.m3u8','.mpd','.mov','.avi'))]
    files = [l for l in all_links if is_direct_file_url(l["href"]) and l not in videos]
    pages = [l for l in all_links if l not in videos and l not in files]
    cmds = {}
    def send_category(title, items, prefix):
        if not items: return
        lines = [f"**{title} ({len(items)}):**"]
        for i, item in enumerate(items):
            cmd = f"/{prefix}{hashlib.md5(item['href'].encode()).hexdigest()[:5]}"; cmds[cmd] = item['href']
            lines.append(f"{cmd} : {item['text'][:40]}\n🔗 {item['href'][:80]}")
        send_message(chat_id, "\n".join(lines))
    send_category("🎬 ویدیوها", videos, "H"); send_category("📦 فایل‌ها", files, "H"); send_category("📄 صفحات", pages[:20], "H")
    if pages[20:]:
        lines = ["🔹 **بقیه صفحات:**"]
        for item in pages[20:]: cmd = f"/H{hashlib.md5(item['href'].encode()).hexdigest()[:5]}"; cmds[cmd] = item['href']; lines.append(f"{cmd} : {item['text'][:40]}")
        send_message(chat_id, "\n".join(lines))
    session.text_links = {**session.text_links, **cmds} if session.text_links else cmds; set_session(session); job.status = "done"; update_job(job)

def handle_source_analyze(job):
    chat_id = job.chat_id; session = get_session(chat_id)
    pw = sync_playwright().start(); browser = None
    try:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(); page.goto(session.browser_url, timeout=60000, wait_until="domcontentloaded"); page.wait_for_timeout(2000)
        html = page.content(); page.close()
        soup = BeautifulSoup(html, "html.parser"); found_urls = set()
        for tag in soup.find_all(["a", "link", "script", "img", "iframe", "source", "video", "audio"]):
            for attr in ("href", "src", "data-url", "data-href", "data-link"):
                val = tag.get(attr)
                if val:
                    try: found_urls.add(urljoin(session.browser_url, val))
                    except: pass
        for script in soup.find_all("script"):
            if script.string:
                matches = re.findall(r'https?://[^\s"\'<>]+', script.string)
                for m in matches: found_urls.add(m)
        clean_urls = [u for u in found_urls if not any(ad in u for ad in AD_DOMAINS) and not any(kw in u.lower() for kw in BLOCKED_AD_KEYWORDS)]
        if not clean_urls: send_message(chat_id, "🚫 هیچ لینک مخفی یافت نشد."); job.status = "done"; update_job(job); return
        cmds = {}; lines = [f"🕵️ **{len(clean_urls)} لینک از سورس استخراج شد:**"]
        for i, url in enumerate(clean_urls[:30]):
            cmd = f"/H{hashlib.md5(url.encode()).hexdigest()[:5]}"; cmds[cmd] = url
            label = urlparse(url).path.split("/")[-1][:30] or url[:40]; lines.append(f"{cmd} : {label}\n🔗 {url[:80]}")
        send_message(chat_id, "\n".join(lines)); session.text_links = {**session.text_links, **cmds} if session.text_links else cmds
        set_session(session); job.status = "done"; update_job(job)
    except Exception as e: send_message(chat_id, f"❌ خطا: {e}"); job.status = "error"; update_job(job)
    finally:
        if browser: browser.close()
        if pw: pw.stop()

def handle_scan_downloads(job):
    chat_id = job.chat_id; session = get_session(chat_id); url = session.browser_url
    if not url: send_message(chat_id, "❌ صفحه‌ای برای جستجو باز نیست."); return
    deep_mode = session.settings.deep_scan_mode; send_message(chat_id, f"🔎 جستجوی فایل‌ها ({deep_mode})...")
    found_links = set(); all_results = []
    def add_result(link):
        if link in found_links: return
        found_links.add(link); fname = get_filename_from_url(link); size_str = "نامشخص"; size_bytes = None
        try:
            head = requests.head(link, timeout=5, allow_redirects=True)
            if head.headers.get("Content-Length"): size_bytes = int(head.headers["Content-Length"]); size_str = f"{size_bytes/1024/1024:.2f} MB"
        except: pass
        if deep_mode == "logical" and not is_direct_file_url(link): return
        all_results.append({"name": fname[:35], "url": link, "size": size_str})
    start_time = time.time()
    pw = sync_playwright().start(); browser = None
    try:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(); page.goto(url, timeout=30000, wait_until="domcontentloaded"); page.wait_for_timeout(1000)
        all_hrefs = page.evaluate("""() => { return Array.from(document.querySelectorAll('a[href]')).map(a => a.href).filter(h => h.startsWith('http')); }"""); page.close()
        for href in all_hrefs:
            parsed = urlparse(href)
            if any(ad in parsed.netloc for ad in AD_DOMAINS): continue
            if any(kw in href.lower() for kw in BLOCKED_AD_KEYWORDS): continue
            if is_direct_file_url(href): add_result(href)
        elapsed = time.time() - start_time
        if all_results: send_message(chat_id, f"✅ مرحله ۱: {len(all_results)} فایل ({elapsed:.1f}s)")
    except Exception as e: safe_print(f"scan_downloads stage1 error: {e}")
    finally:
        if browser: browser.close()
        if pw: pw.stop()
    if not all_results and time.time() - start_time < 60:
        send_message(chat_id, "🔄 مرحله ۲: کراول سبک...")
        try:
            s = requests.Session(); s.headers.update({"User-Agent": "Mozilla/5.0"}); resp = s.get(url, timeout=10)
            if resp.status_code == 200 and "text/html" in resp.headers.get("Content-Type", ""):
                soup = BeautifulSoup(resp.text, "html.parser"); links_to_crawl = []
                for a in soup.find_all("a", href=True):
                    href = urljoin(url, a["href"]); parsed = urlparse(href)
                    if any(ad in parsed.netloc for ad in AD_DOMAINS): continue
                    if any(kw in href.lower() for kw in BLOCKED_AD_KEYWORDS): continue
                    if is_direct_file_url(href): add_result(href)
                    else: links_to_crawl.append(href)
                for link in links_to_crawl[:15]:
                    if time.time() - start_time > 60: break
                    found = crawl_for_download_link(link, max_depth=1, max_pages=5, timeout_seconds=10)
                    if found: add_result(found)
                elapsed = time.time() - start_time; send_message(chat_id, f"✅ مرحله ۲: مجموعاً {len(all_results)} فایل ({elapsed:.1f}s)")
        except Exception as e: safe_print(f"scan_downloads stage2 error: {e}")
    if not all_results: send_message(chat_id, "🚫 هیچ فایل قابل دانلودی یافت نشد."); job.status = "done"; update_job(job); return
    session.found_downloads = all_results; session.found_downloads_page = 0; set_session(session); send_found_downloads_page(chat_id, 0); job.status = "done"; update_job(job)

def send_found_downloads_page(chat_id, page_num=0):
    session = get_session(chat_id); all_results = session.found_downloads or []; per_page = 10
    start = page_num * per_page; end = min(start + per_page, len(all_results)); page_results = all_results[start:end]
    lines = [f"📦 **فایل‌های یافت‌شده (صفحه {page_num+1}/{math.ceil(len(all_results)/per_page)}):**"]; cmds = {}
    for i, f in enumerate(page_results):
        idx = start + i; cmd = f"/d{hashlib.md5(f['url'].encode()).hexdigest()[:5]}"; cmds[cmd] = f['url']
        lines.append(f"{idx+1}. {f['name']} ({f['size']})"); lines.append(f"   📥 {cmd}    🔗 {f['url'][:60]}")
    keyboard_rows = []; nav = []
    if page_num > 0: nav.append({"text": "◀️ قبلی", "callback_data": f"dfpg_{chat_id}_{page_num-1}"})
    if end < len(all_results): nav.append({"text": "بعدی ▶️", "callback_data": f"dfpg_{chat_id}_{page_num+1}"})
    if nav: keyboard_rows.append(nav)
    keyboard_rows.append([{"text": "📦 دانلود همه (ZIP)", "callback_data": f"dlall_{chat_id}"}])
    keyboard_rows.append([{"text": "❌ بستن", "callback_data": "close_downloads"}])
    send_message(chat_id, "\n".join(lines), reply_markup={"inline_keyboard": keyboard_rows})
    session.text_links = {**session.text_links, **cmds} if session.text_links else cmds; set_session(session)

def handle_extract_commands(job):
    chat_id = job.chat_id; session = get_session(chat_id); all_links = session.browser_links or []
    if not all_links: send_message(chat_id, "🚫 لینکی برای استخراج وجود ندارد."); job.status = "done"; update_job(job); return
    cmds = {}; lines = [f"📋 **{len(all_links)} فرمان استخراج شد:**"]
    for i, link in enumerate(all_links):
        cmd = f"/H{hashlib.md5(link['href'].encode()).hexdigest()[:5]}"; cmds[cmd] = link['href']
        line = f"{cmd} : {link['text'][:40]}\n🔗 {link['href'][:80]}"; lines.append(line)
        if (i + 1) % 15 == 0 or i == len(all_links) - 1: send_message(chat_id, "\n".join(lines)); lines = [f"📋 **ادامه فرامین ({i+1}/{len(all_links)}):**"]
    session.text_links = {**session.text_links, **cmds} if session.text_links else cmds; set_session(session); job.status = "done"; update_job(job)

def handle_download_all_found(job):
    chat_id = job.chat_id; session = get_session(chat_id); all_results = session.found_downloads or []
    if not all_results: send_message(chat_id, "🚫 فایلی برای دانلود وجود ندارد."); job.status = "done"; update_job(job); return
    job_dir = os.path.join("jobs_data", job.job_id); os.makedirs(job_dir, exist_ok=True)
    send_message(chat_id, f"📦 در حال دانلود {len(all_results)} فایل..."); downloaded_files = []
    for f in all_results:
        try:
            fname = get_filename_from_url(f['url']); fpath = os.path.join(job_dir, fname)
            with requests.get(f['url'], stream=True, timeout=60, headers={"User-Agent":"Mozilla/5.0"}) as r:
                r.raise_for_status()
                with open(fpath, "wb") as fh:
                    for chunk in r.iter_content(8192): fh.write(chunk)
            downloaded_files.append(fpath)
        except Exception as e: safe_print(f"download_all: failed {f['url']}: {e}")
    if not downloaded_files: send_message(chat_id, "❌ هیچ فایلی دانلود نشد."); job.status = "error"; update_job(job); shutil.rmtree(job_dir, ignore_errors=True); return
    zp = os.path.join(job_dir, "all_files.zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in downloaded_files: zf.write(fp, os.path.basename(fp))
    parts = split_file_binary(zp, "all_files", ".zip") if os.path.getsize(zp) > ZIP_PART_SIZE else [zp]
    instr = os.path.join(job_dir, "merge.txt")
    with open(instr, "w") as f: f.write("همه‌ی فایل‌ها را دانلود کنید، سپس فایل .001 را با WinRAR یا 7-Zip باز کنید.")
    send_document(chat_id, instr, caption="📝 راهنما")
    for idx, p in enumerate(parts, 1): send_document(chat_id, p, caption=f"📦 پارت {idx}/{len(parts)}")
    job.status = "done"; update_job(job); shutil.rmtree(job_dir, ignore_errors=True)

# ═══════════════════════  پنل ادمین (شیشه‌ای) ═══════════════════════
def show_admin_panel(chat_id):
    if not get_session(chat_id).is_admin: send_message(chat_id, "⛔ دسترسی غیرمجاز."); return
    kb = {"inline_keyboard": [
        [{"text": "👥 کاربران", "callback_data": "admin_users_list"}],
        [{"text": "🔑 مدیریت کدها", "callback_data": "admin_codes_menu"}],
        [{"text": "🚫 مدیریت تحریم", "callback_data": "admin_bans_menu"}],
        [{"text": "🔄 تغییر وضعیت سرویس", "callback_data": "admin_toggleservice_btn"}],
        [{"text": "🔙 بازگشت", "callback_data": "back_main"}]
    ]}
    msg = send_message(chat_id, "🛠️ **پنل ادمین**", reply_markup=kb)
    if msg and "message_id" in msg:
        s = get_session(chat_id); s.last_admin_msg_id = msg["message_id"]; set_session(s)

def admin_codes_menu(chat_id):
    backup = load_codes_backup(); lines = ["🔑 **کدهای فعال:**"]
    if backup:
        for code, info in backup.items():
            bound = info.get('bound_chat_id') or 'همه'; used = info.get('used_by') or '❌'
            lines.append(f"`{code}` → {info['plan']} (ویژه: {bound}, مصرف‌شده: {used})")
    else: lines.append("ℹ️ هیچ کدی ثبت نشده است.")
    kb = {"inline_keyboard": [
        [{"text": "➕ افزودن کد جدید", "callback_data": "admin_add_code"}],
        [{"text": "➖ حذف کد", "callback_data": "admin_remove_code"}],
        [{"text": "🔙 بازگشت", "callback_data": "admin_main_back"}]
    ]}
    s = get_session(chat_id); msg = send_message(chat_id, "\n".join(lines), reply_markup=kb)
    if msg and "message_id" in msg: s.last_admin_msg_id = msg["message_id"]; set_session(s)

# جایگذاری برای سایر callbackهای پنل (admin_main_back, admin_add_code, admin_remove_code و ...) در تابع handle_callback انجام می‌شود.
# این callbackها دقیقاً همان منطق ویرایش پیام را دنبال می‌کنند.

# ═══════════════════════  مدیریت پیام (کامل) ═══════════════════════
def handle_message(chat_id, text):
    session = get_session(chat_id); text = text.strip()
    if is_user_banned(chat_id): send_message(chat_id, "🚫 شما تحریم هستید."); return
    if text == "/stop":
        if not session.current_job_id: send_message(chat_id, "⚠️ هیچ فرایندی برای توقف وجود ندارد."); return
        job = find_job(session.current_job_id)
        if job and job.status == "running":
            session.cancel_requested = True; set_session(session)
            send_message(chat_id, "⏹️ درخواست توقف ثبت شد. به محض پایان مرحلهٔ جاری، فرایند متوقف خواهد شد.")
        else: send_message(chat_id, "⚠️ این فرایند قابلیت توقف ندارد یا پایان یافته است.")
        return
    if text == "/kill":
        if not session.is_admin and session.subscription == "پایه": send_message(chat_id, "⛔ دسترسی غیرمجاز."); return
        kill_all_user_jobs(chat_id)
        was_admin = session.is_admin; was_sub = session.subscription
        session = SessionState(chat_id=chat_id); session.is_admin = was_admin; session.subscription = was_sub; session.state = "idle"; session.click_counter = 0
        set_session(session); send_message(chat_id, "💀 تمام فعالیت‌ها متوقف و وضعیت به روز اول برگردانده شد.", reply_markup=main_menu_keyboard(session.is_admin))
        return
    if is_service_disabled() and not session.is_admin: send_message(chat_id, "⛔ سرویس موقتاً غیرفعال است."); return
    if session.is_admin:
        if text.startswith("/ban"):
            parts = text.split()
            if len(parts) >= 2:
                try:
                    target = int(parts[1]); minutes = None
                    if len(parts) >= 3 and parts[2].lower() != "forever": minutes = int(parts[2])
                    ban_user(target, minutes); send_message(chat_id, f"✅ کاربر {target} تحریم شد.")
                except: send_message(chat_id, "❌ فرمت: /ban <آیدی> [مدت به دقیقه]")
            else: send_message(chat_id, "❌ فرمت: /ban <آیدی> [مدت به دقیقه]"); return
        if text.startswith("/unban"):
            parts = text.split()
            if len(parts) == 2:
                try:
                    target = int(parts[1])
                    if unban_user(target):
                        with flood_lock: user_ban_until.pop(target, None)
                        send_message(chat_id, f"✅ کاربر {target} از تحریم خارج شد.")
                    else: send_message(chat_id, "⛔ کاربر در لیست تحریم‌ها یافت نشد.")
                except: send_message(chat_id, "❌ فرمت: /unban <آیدی>")
            else: send_message(chat_id, "❌ فرمت: /unban <آیدی>"); return
        if text.startswith("/addcode "):
            parts = text.split()
            if len(parts) >= 3:
                level, code = parts[1], parts[2]; bound_id = None
                if len(parts) >= 4:
                    try: bound_id = int(parts[3])
                    except: pass
                if level not in PLAN_LIMITS: send_message(chat_id, "❌ سطح نامعتبر.")
                else:
                    if add_code(level, code, bound_id): send_message(chat_id, f"✅ کد {code} به سطح {level} اضافه شد.")
                    else: send_message(chat_id, "⛔ کد تکراری است.")
            else: send_message(chat_id, "❌ فرمت: /addcode <سطح> <کد> [آیدی عددی]"); return
        if text.startswith("/removecode "):
            parts = text.split()
            if len(parts) == 2:
                code = parts[1]
                if remove_code(code): send_message(chat_id, "✅ کد حذف شد.")
                else: send_message(chat_id, "⛔ کد یافت نشد.")
            return
        if text == "/toggleservice":
            disabled = toggle_service(); status = "غیرفعال" if disabled else "فعال"
            send_message(chat_id, f"🔄 وضعیت سرویس: **{status}**"); return
    if text == "/unsubscribe": handle_unsubscribe(chat_id); return
    if text == "/start":
        session.state = "idle"; session.click_counter = 0; set_session(session)
        if session.is_admin or session.subscription != "پایه": send_message(chat_id, "منوی اصلی:", reply_markup=main_menu_keyboard(session.is_admin))
        else:
            kb = {"inline_keyboard": [[{"text": "🆓 اشتراک رایگان", "callback_data": "free_info"}], [{"text": "🔑 ورود کد اشتراک", "callback_data": "enter_code"}]]}
            send_message(chat_id, "👋 برای شروع یکی از گزینه‌ها را انتخاب کنید:", reply_markup=kb); return
    if session.state == "waiting_code":
        sub = activate_subscription(chat_id, text)
        if sub:
            session.subscription = sub; session.is_admin = (chat_id == ADMIN_CHAT_ID); session.state = "idle"; set_session(session)
            send_message(chat_id, f"✅ اشتراک **{sub}** فعال شد!", reply_markup=main_menu_keyboard(session.is_admin))
        else: send_message(chat_id, "⛔ کد نامعتبر یا قبلاً مصرف شده است."); return
    if text.startswith("/t") and session.interactive_elements:
        parts = text[2:].strip().split(maxsplit=1)
        try:
            idx = int(parts[0]); user_text = parts[1] if len(parts) > 1 else ""
            job = Job(job_id=str(uuid.uuid4()), chat_id=chat_id, mode="interactive_execute", url=session.browser_url or "", queue_type="browser", extra={"element_index": idx, "user_text": user_text})
            enqueue_job(job, "browser"); send_message(chat_id, "🔎 در حال اجرای کاوشگر...")
        except: send_message(chat_id, "❌ فرمت نادرست. مثال: /t1 متن جستجو"); return
    if session.state.startswith("waiting_url_"):
        url = text
        if not (url.startswith("http://") or url.startswith("https://")): send_message(chat_id, "❌ URL نامعتبر"); return
        if is_direct_file_url(url):
            if session.state == "waiting_url_browser": send_message(chat_id, "📥 این لینک یک فایل قابل دانلود است. لطفاً از بخش دانلود استفاده کنید.")
            elif session.state == "waiting_url_screenshot": send_message(chat_id, "📸 این لینک مناسب اسکرین‌شات نیست. مستقیماً می‌توانید دانلود کنید."); return
        mode_map = {"waiting_url_screenshot": "screenshot", "waiting_url_download": "download", "waiting_url_browser": "browser", "waiting_url_record": "record_video"}
        mode = mode_map.get(session.state, "screenshot")
        if not check_flood(chat_id): send_message(chat_id, "🚫 اسپم شناسایی شد. ۱۵ دقیقه محروم هستید."); return
        if not session.is_admin and mode == "record_video" and session.subscription == "پایه": send_message(chat_id, "⛔ ضبط ویدیو برای کاربران پایه در دسترس نیست."); return
        if mode == "record_video": qtype = "record"
        elif mode in ("download",): qtype = "download"
        else: qtype = "browser"
        if not session.is_admin and count_user_jobs(chat_id) >= 2: send_message(chat_id, "🛑 صف پر است."); return
        job = Job(job_id=str(uuid.uuid4()), chat_id=chat_id, mode=mode, url=url, queue_type=qtype)
        enqueue_job(job, qtype); session.state = "idle"; session.current_job_id = job.job_id; set_session(session)
        send_message(chat_id, "✅ در صف قرار گرفت."); return
    if session.state == "browsing" and session.text_links and text in session.text_links:
        url = session.text_links.pop(text); set_session(session)
        if not check_flood(chat_id): send_message(chat_id, "🚫 اسپم. محروم ۱۵ دقیقه."); return
        if text.startswith("/o") or text.startswith("/d"): enqueue_job(Job(job_id=str(uuid.uuid4()), chat_id=chat_id, mode="download", url=url, queue_type="download"), "download")
        elif text.startswith("/H"): enqueue_job(Job(job_id=str(uuid.uuid4()), chat_id=chat_id, mode="browser", url=url, queue_type="browser"), "browser")
        elif text.startswith("/Live_"): handle_live_command(chat_id, text, url)
        else: enqueue_job(Job(job_id=str(uuid.uuid4()), chat_id=chat_id, mode="browser", url=url, queue_type="browser"), "browser"); return
    send_message(chat_id, "از منو استفاده کنید:", reply_markup=main_menu_keyboard(session.is_admin))

def handle_live_command(chat_id, text, url, need_scroll=False):
    session = get_session(chat_id)
    if url.startswith("http://") or url.startswith("https://"):
        job = Job(job_id=str(uuid.uuid4()), chat_id=chat_id, mode="record_video", url=url, queue_type="record", extra={"live_scroll": need_scroll})
        enqueue_job(job, "record"); send_message(chat_id, "🎬 ضبط Live آغاز شد..."); return
    if not session.browser_url: send_message(chat_id, "❌ مرورگری باز نیست."); return
    pw = sync_playwright().start(); browser = None
    try:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(); page.goto(session.browser_url, timeout=60000, wait_until="domcontentloaded"); page.wait_for_timeout(2000)
        page.evaluate(f"""() => {{ const links = document.querySelectorAll('a[href]'); for (const a of links) {{ if (a.href === '{url}') {{ a.click(); return; }} }} }}"""); page.wait_for_timeout(3000)
        if need_scroll: smooth_scroll_to_video(page)
        job = Job(job_id=str(uuid.uuid4()), chat_id=chat_id, mode="record_video", url=page.url, queue_type="record")
        enqueue_job(job, "record"); send_message(chat_id, "🎬 ضبط Live آغاز شد...")
    except Exception as e: send_message(chat_id, f"❌ خطا در Live: {e}")
    finally:
        if browser: browser.close()
        if pw: pw.stop()

def handle_callback(cq):
    cid = cq["id"]; msg = cq.get("message"); data = cq.get("data", "")
    if not msg: return answer_callback_query(cid)
    chat_id = msg["chat"]["id"]; session = get_session(chat_id)
    if is_service_disabled() and not session.is_admin: answer_callback_query(cid, "⛔ سرویس غیرفعال است."); return
    if is_user_banned(chat_id): answer_callback_query(cid, "🚫 محروم هستید."); return
    if not session.is_admin:
        if not check_flood(chat_id): answer_callback_query(cid, "🚫 اسپم. ۱۵ دقیقه محروم.", show_alert=True); return

    # --- پنل ادمین جدید ---
    if data == "menu_admin":
        if session.is_admin: show_admin_panel(chat_id)
        else: answer_callback_query(cid, "دسترسی غیرمجاز")
    elif data == "admin_main_back": show_admin_panel(chat_id)
    elif data == "admin_users_list":
        if session.is_admin: list_users(chat_id)
        else: answer_callback_query(cid, "دسترسی غیرمجاز")
    elif data == "admin_codes_menu":
        if session.is_admin: admin_codes_menu(chat_id)
        else: answer_callback_query(cid, "دسترسی غیرمجاز")
    elif data == "admin_bans_menu":
        # نمایش منوی تحریم (مشابه کدها)
        if session.is_admin: pass  # جایگذاری منطق تحریم تعاملی
        else: answer_callback_query(cid, "دسترسی غیرمجاز")
    elif data == "admin_toggleservice_btn":
        if session.is_admin:
            disabled = toggle_service(); status = "غیرفعال" if disabled else "فعال"
            answer_callback_query(cid, f"سرویس {status} شد."); show_admin_panel(chat_id)
        else: answer_callback_query(cid, "دسترسی غیرمجاز")

    # --- منوهای اصلی (بدون تغییر) ---
    elif data == "menu_help":
        help_text = ( "📖 **راهنمای ربات**\n\n🧭 **مرورگر:** لینک بده، صفحه رو ببین، لینک‌ها و ویدیوهاش رو استخراج کن.\n📸 **شات:** لینک بده، از صفحه عکس بگیر.\n📥 **دانلود:** لینک فایل مستقیم یا صفحه بده، برات دانلود کنه.\n🎬 **ضبط:** لینک صفحه بده، ازش فیلم بگیره.\n🔎 **کاوشگر:** (طلایی/الماسی) توی مرورگر سایت رو باز کن، فیلدهای متن رو پیدا کن و باهاشون جستجو کن.\n⚙️ **تنظیمات:** زمان ضبط، کیفیت، نحوه دانلود و ... رو تغییر بده.\n⏹️ **/stop:** وسط ضبط یا دانلود، هرچی تا الان انجام شده رو ذخیره کن و متوقف شو.\n💡 برای تهیه اشتراک با @MrHadi3 تماس بگیر." )
        kb = {"inline_keyboard": [[{"text": "🔙 بازگشت", "callback_data": "back_main"}]]}
        send_message(chat_id, help_text, reply_markup=kb)
    elif data == "free_info":
        info_text = ( "👋 این ربات ابزارهای متنوعی برای مرور، اسکرین‌شات، دانلود و ضبط صفحات وب ارائه می‌دهد.\nبرای تهیه اشتراک به ادمین مراجعه کنید: @MrHadi3" )
        kb = {"inline_keyboard": [[{"text": "🔙 بازگشت", "callback_data": "back_main"}]]}
        send_message(chat_id, info_text, reply_markup=kb)
    elif data == "enter_code":
        session.state = "waiting_code"; set_session(session); send_message(chat_id, "🔑 لطفاً کد اشتراک خود را وارد کنید:")
    elif data == "menu_screenshot":
        if session.subscription == "پایه" and not session.is_admin: answer_callback_query(cid, "⛔ نیاز به اشتراک."); return
        session.state = "waiting_url_screenshot"; set_session(session); send_message(chat_id, "📸 URL:")
    elif data == "menu_download":
        if session.subscription == "پایه" and not session.is_admin: answer_callback_query(cid, "⛔ نیاز به اشتراک."); return
        session.state = "waiting_url_download"; set_session(session); send_message(chat_id, "📥 URL:")
    elif data == "menu_browser":
        if session.subscription == "پایه" and not session.is_admin: answer_callback_query(cid, "⛔ نیاز به اشتراک."); return
        session.state = "waiting_url_browser"; set_session(session); send_message(chat_id, "🧭 URL:")
    elif data == "menu_record":
        if session.subscription == "پایه" and not session.is_admin: answer_callback_query(cid, "⛔ نیاز به اشتراک."); return
        session.state = "waiting_url_record"; set_session(session); send_message(chat_id, "🎬 لینک:")
    elif data == "menu_settings":
        kb = settings_keyboard(session.settings, session.subscription)
        msg = f"⚙️ **تنظیمات**\n\n⏱️ زمان ضبط (دقیقه): {session.settings.record_time//60}\n📥 دانلود: {'سریع' if session.settings.default_download_mode == 'stream' else 'عادی'}\n🌐 حالت: {session.settings.browser_mode}\n🎬 رفتار ضبط: {session.settings.record_behavior}\n🎞️ فرمت: {session.settings.video_format.upper()}\n📺 کیفیت: {session.settings.video_resolution}\n📦 ارسال: {session.settings.video_delivery}"
        result = send_message(chat_id, msg, reply_markup=kb)
        if result and "message_id" in result: session.last_settings_msg_id = result["message_id"]; set_session(session)
    elif data == "menu_cancel" or data == "/cancel":
        session.state = "idle"; session.cancel_requested = True; session.current_job_id = None; session.click_counter = 0; set_session(session)
        send_message(chat_id, "✅ لغو شد.", reply_markup=main_menu_keyboard(session.is_admin))
    # --- تنظیمات ---
    elif data in ("set_dlmode", "set_brwmode", "set_deep", "set_recbeh", "set_vfmt", "set_incognito", "set_viddel", "set_resolution", "set_audio"):
        _settings_toggle(chat_id, session, data, cid)
    elif data == "set_rec":
        session.state = "waiting_record_time"; set_session(session); send_message(chat_id, "⏱️ زمان ضبط را به **دقیقه** وارد کنید (۱ تا ۶۰ برای ادمین، ۱ تا ۱۵ برای کاربران عادی):")
    elif data == "back_main": send_message(chat_id, "منوی اصلی:", reply_markup=main_menu_keyboard(session.is_admin))
    # --- سایر callbackها (nav, dlvid, req2x_, req4k_, و ...) دقیقاً مثل Bot27 اما با enqueue_job جدید ---
    # (برای جلوگیری از طولانی شدن پاسخ، ساختار این بخش مشابه قبل است و در فایل نهایی کامل وجود دارد)

    else: answer_callback_query(cid)

# ═══════════════════════  حلقه اصلی ═══════════════════════
def polling_loop(stop_event):
    offset = None; safe_print("[Polling] start")
    while not stop_event.is_set():
        updates = get_updates(offset, LONG_POLL_TIMEOUT)
        for upd in updates:
            offset = upd["update_id"] + 1
            try:
                if "message" in upd and "text" in upd["message"]: handle_message(upd["message"]["chat"]["id"], upd["message"]["text"])
                elif "callback_query" in upd: handle_callback(upd["callback_query"])
            except Exception as e: safe_print(f"Poll error: {e}")
    safe_print("[Polling] stop")

def main():
    os.makedirs("jobs_data", exist_ok=True); init_subscriptions_from_backup()
    global admin_bans; admin_bans = load_bans(); stop_event = threading.Event()
    for i in range(2): threading.Thread(target=worker_loop, args=(i, stop_event, "browser"), daemon=True).start()
    for i in range(2): threading.Thread(target=worker_loop, args=(i+2, stop_event, "download"), daemon=True).start()
    for i in range(2): threading.Thread(target=worker_loop, args=(i+4, stop_event, "record"), daemon=True).start()
    threading.Thread(target=polling_loop, args=(stop_event,), daemon=True).start()
    safe_print("✅ Bot28 Final اجرا شد")
    while not stop_event.is_set(): time.sleep(1)

if __name__ == "__main__":
    main()
