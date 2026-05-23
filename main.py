import os
import re
import time
import threading
import base64
import urllib.request
from groq import Groq
from flask import Flask
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
CLIENT_SECRETS_FILE = "client_secrets.json"
TOKEN_FILE = "token.json"

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
YOUTUBE_CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID")
CHECK_INTERVAL = 600
LAST_VIDEO_FILE = "last_video.txt"

client_secrets_env = os.environ.get("CLIENT_SECRETS_JSON")
if client_secrets_env and not os.path.exists(CLIENT_SECRETS_FILE):
    with open(CLIENT_SECRETS_FILE, "w") as f:
        f.write(client_secrets_env)

groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot aktif ve çalışıyor.", 200

def authenticate():
    creds = None

    token_json_env = os.environ.get("TOKEN_JSON")
    if token_json_env and not os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "w") as f:
            f.write(token_json_env)
        print("Token environment variable'dan yüklendi.")

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            print("Token yenilendi.")
        else:
            flow = Flow.from_client_secrets_file(
                CLIENT_SECRETS_FILE,
                scopes=SCOPES,
                redirect_uri="urn:ietf:wg:oauth:2.0:oob"
            )
            auth_url, _ = flow.authorization_url(prompt="consent")
            print("\n--- GOOGLE GİRİŞİ ---")
            print("Aşağıdaki linke tıkla ve Google hesabınla giriş yap:\n")
            print(auth_url)
            print("\nGiriş yaptıktan sonra sana gösterilen kodu buraya yapıştır:")
            code = input("Kod: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials
            print("Giriş başarılı.")

        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
            print(f"Token '{TOKEN_FILE}' dosyasına kaydedildi.")

    return creds

def extract_video_id(url):
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?\/]|$)",
        r"(?:youtu\.be\/)([0-9A-Za-z_-]{11})",
        r"(?:shorts\/)([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def load_last_video_id():
    if os.path.exists(LAST_VIDEO_FILE):
        with open(LAST_VIDEO_FILE, "r") as f:
            return f.read().strip()
    return None

def save_last_video_id(video_id):
    with open(LAST_VIDEO_FILE, "w") as f:
        f.write(video_id)

def get_thumbnail_base64(video_id):
    urls = [
        f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
    ]
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                data = response.read()
                return base64.b64encode(data).decode("utf-8")
        except Exception:
            continue
    return None

def comment_on_video(youtube, video_url, comment_text):
    video_id = extract_video_id(video_url)
    if not video_id:
        print("Hata: Geçerli bir YouTube video URL'si girilemedi.")
        return

    request = youtube.commentThreads().insert(
        part="snippet",
        body={
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {
                    "snippet": {
                        "textOriginal": comment_text
                    }
                }
            }
        }
    )
    response = request.execute()
    comment_id = response["snippet"]["topLevelComment"]["id"]
    print(f"\nYorum başarıyla gönderildi!")
    print(f"Video ID : {video_id}")
    print(f"Yorum ID : {comment_id}")
    print(f"Yorum    : {comment_text}")

def get_latest_video(youtube, channel_id):
    request = youtube.search().list(
        part="snippet",
        channelId=channel_id,
        order="date",
        type="video",
        maxResults=1
    )
    response = request.execute()
    items = response.get("items", [])
    if not items:
        return None, None, None, None
    item = items[0]
    video_id = item["id"]["videoId"]
    title = item["snippet"]["title"]
    description = item["snippet"]["description"]
    thumbnail_url = item["snippet"]["thumbnails"].get("high", {}).get("url", "")
    return video_id, title, description, thumbnail_url

def generate_comment(title, description, video_id):
    thumbnail_b64 = get_thumbnail_base64(video_id)

    base_prompt = (
        f"Sen bu YouTube kanalın gerçek bir takipçisisin. "
        f"Aşağıdaki videoya gerçek bir insan gibi, samimi, sıcak ve doğal bir Türkçe yorum yaz. "
        f"Yorumda mutlaka emoji kullan (2-4 tane). "
        f"Yorum 1-2 cümle olsun, abartılı veya robotik görünmesin. "
        f"Sadece yorumu yaz, başka açıklama ekleme.\n\n"
        f"Video Başlığı: {title}\n"
        f"Video Açıklaması: {description[:300]}"
    )

    if thumbnail_b64:
        print("Kapak fotoğrafı alındı, vision modeli kullanılıyor...")
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": base_prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{thumbnail_b64}"
                        }
                    }
                ]
            }
        ]
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=messages,
            max_tokens=150
        )
    else:
        print("Kapak fotoğrafı alınamadı, metin modeli kullanılıyor...")
        messages = [
            {
                "role": "user",
                "content": base_prompt
            }
        ]
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=150
        )

    return response.choices[0].message.content.strip()

def auto_comment_loop(youtube):
    print(f"\nOtomasyon döngüsü başladı. Kanal: {YOUTUBE_CHANNEL_ID}")
    last_commented_video_id = load_last_video_id()
    if last_commented_video_id:
        print(f"Hafızadan yüklendi, son yorum atılan video: {last_commented_video_id}")

    while True:
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] Kanal kontrol ediliyor...")
            video_id, title, description, thumbnail_url = get_latest_video(youtube, YOUTUBE_CHANNEL_ID)

            if video_id and video_id != last_commented_video_id:
                print(f"Yeni video bulundu: {title} ({video_id})")
                print(f"Kapak: {thumbnail_url}")
                comment_text = generate_comment(title, description, video_id)
                print(f"Groq yorumu: {comment_text}")

                video_url = f"https://www.youtube.com/watch?v={video_id}"
                comment_on_video(youtube, video_url, comment_text)
                last_commented_video_id = video_id
                save_last_video_id(video_id)
            else:
                print("Yeni video yok, bekleniyor...")

        except Exception as e:
            print(f"Hata: {e}")

        print(f"{CHECK_INTERVAL // 60} dakika sonra tekrar kontrol edilecek...")
        time.sleep(CHECK_INTERVAL)

def main():
    print("Bot başlatılıyor...")
    creds = authenticate()
    youtube = build("youtube", "v3", credentials=creds)
    print("YouTube API bağlantısı kuruldu.")

    request = youtube.channels().list(part="snippet", mine=True)
    response = request.execute()
    if response.get("items"):
        channel = response["items"][0]["snippet"]
        print(f"Giriş yapılan kanal: {channel['title']}")

    bot_thread = threading.Thread(target=auto_comment_loop, args=(youtube,), daemon=True)
    bot_thread.start()

    print("\nFlask sunucusu başlatılıyor (port 5000)...")
    app.run(host="0.0.0.0", port=5000)

if __name__ == "__main__":
    main()
