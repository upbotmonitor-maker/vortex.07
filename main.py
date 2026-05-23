import os
import re
import time
import threading
import base64
import json
import urllib.request
from groq import Groq
from flask import Flask
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

# Bütün ortam değişkenlerini temizleyip (boşlukları silip) bir sözlüğe alıyoruz
env_clean = {k.strip().upper(): v.strip() for k, v in os.environ.items() if v}

# Ortam Değişkenleri
GROQ_API_KEY = env_clean.get("GROQ_API_KEY")
YOUTUBE_CHANNEL_ID = env_clean.get("YOUTUBE_CHANNEL_ID")
CHECK_INTERVAL = 600
LAST_VIDEO_FILE = "last_video.txt"

# Esnek JSON yakalama (boşluklu veya hatalı yazımlara karşı korumalı)
TOKEN_JSON_STR = env_clean.get("TOKEN_JSON")
CLIENT_SECRETS_JSON_STR = env_clean.get("CLIENT_SECRETS_JSON")

groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot aktif ve çalışıyor.", 200

def authenticate():
    if not TOKEN_JSON_STR or not CLIENT_SECRETS_JSON_STR:
        print("HATA: Render üzerindeki TOKEN_JSON veya CLIENT_SECRETS_JSON değişkenleri bulunamadı!")
        print("Mevcut algılanan temiz anahtarlar:", list(env_clean.keys()))
        return None

    try:
        token_data = json.loads(TOKEN_JSON_STR)
        client_data = json.loads(CLIENT_SECRETS_JSON_STR)
        
        client_id = client_data.get("installed", {}).get("client_id")
        client_secret = client_data.get("installed", {}).get("client_secret")

        print("Token ve Client Secret bilgileri Render hafızasından başarıyla çözüldü.")
        
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES
        )
    except Exception as e:
        print(f"HATA: JSON verileri parse edilirken hata oluştu: {e}")
        return None

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                print("Token başarıyla yenilendi.")
            except Exception as e:
                print(f"Token yenilenirken hata oluştu: {e}")
                
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
    print(f"\nOtomasyon döngüsü başladı. Takip edilen kanal ID: {YOUTUBE_CHANNEL_ID}")
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
            print(f"Döngü hatası: {e}")

        print(f"{CHECK_INTERVAL // 60} dakika sonra tekrar kontrol edilecek...")
        time.sleep(CHECK_INTERVAL)

def main():
    print("Bot başlatılıyor...")
    creds = authenticate()
    if not creds:
        print("Kimlik doğrulaması başarısız oldu, bot başlatılamıyor.")
        return
        
    youtube = build("youtube", "v3", credentials=creds)
    print("YouTube API bağlantısı kuruldu.")

    try:
        request = youtube.channels().list(part="snippet", mine=True)
        response = request.execute()
        if response.get("items"):
            channel = response["items"][0]["snippet"]
            print(f"Giriş yapılan (Yorum atacak) kanal: {channel['title']}")
    except Exception as e:
        print(f"Kanal bilgisi alınamadı (Yine de devam ediliyor): {e}")

    bot_thread = threading.Thread(target=auto_comment_loop, args=(youtube,), daemon=True)
    bot_thread.start()

    print("\nFlask sunucusu başlatılıyor (port 5000)...")
    app.run(host="0.0.0.0", port=5000)

if __name__ == "__main__":
    main()
