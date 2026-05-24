import os
import re
import time
import threading
import base64
import urllib.request
from groq import Groq
from flask import Flask
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
CLIENT_SECRETS_FILE = "client_secrets.json"
TOKEN_FILE = "token.json"
LAST_VIDEO_FILE = "last_video.txt"

GROQ_API_KEY      = os.environ.get("GROQ_API_KEY")
YOUTUBE_CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID")
CHECK_INTERVAL    = 600

CLIENT_ID = "944243685513-v680a62u3vj3gqsncr12k8gnbka0l1gq.apps.googleusercontent.com"
TOKEN_URI = "https://oauth2.googleapis.com/token"

groq_client = Groq(api_key=GROQ_API_KEY)

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot aktif ve çalışıyor.", 200

def get_env_token():
    for key in ("TOKEN-JSON", "TOKEN_JSON"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return None

def get_env_client_secret():
    for key in ("CLIENT_SECRETS_JSON", "CLIENT-SECRETS-JSON"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return None

def authenticate():
    access_token    = get_env_token()
    client_secret   = get_env_client_secret()

    if access_token and client_secret:
        print("Render env var'larından kimlik bilgileri okundu.")
        refresh_token = os.environ.get("REFRESH_TOKEN", "").strip() or None
        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=TOKEN_URI,
            client_id=CLIENT_ID,
            client_secret=client_secret,
            scopes=SCOPES
        )
        if creds.expired or not creds.valid:
            if refresh_token:
                try:
                    creds.refresh(Request())
                    print("Access token otomatik yenilendi.")
                except Exception as e:
                    print(f"Token yenileme başarısız: {e}")
            else:
                print("Uyarı: REFRESH_TOKEN bulunamadı, token süresi dolunca bot durabilir.")
        return creds

    if os.path.exists(TOKEN_FILE):
        print("Yerel token.json dosyasından giriş yapılıyor...")
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                print("Token yenilendi.")
                with open(TOKEN_FILE, "w") as f:
                    f.write(creds.to_json())
        return creds

    print("\n--- GOOGLE GİRİŞİ ---")
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri="urn:ietf:wg:oauth:2.0:oob"
    )
    auth_url, _ = flow.authorization_url(prompt="consent")
    print("Aşağıdaki linke tıkla ve Google hesabınla giriş yap:\n")
    print(auth_url)
    print("\nGiriş yaptıktan sonra sana gösterilen kodu buraya yapıştır:")
    code = input("Kod: ").strip()
    flow.fetch_token(code=code)
    creds = flow.credentials
    print("Giriş başarılı.")
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
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
                return base64.b64encode(response.read()).decode("utf-8")
        except Exception:
            continue
    return None

def comment_on_video(youtube, video_url, comment_text):
    video_id = extract_video_id(video_url)
    if not video_id:
        print("Hata: Geçerli bir YouTube video URL'si girilemedi.")
        return
    response = youtube.commentThreads().insert(
        part="snippet",
        body={
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {
                    "snippet": {"textOriginal": comment_text}
                }
            }
        }
    ).execute()
    comment_id = response["snippet"]["topLevelComment"]["id"]
    print(f"\nYorum başarıyla gönderildi!")
    print(f"Video ID : {video_id}")
    print(f"Yorum ID : {comment_id}")
    print(f"Yorum    : {comment_text}")

def get_latest_video(youtube, channel_id):
    response = youtube.search().list(
        part="snippet",
        channelId=channel_id,
        order="date",
        type="video",
        maxResults=1
    ).execute()
    items = response.get("items", [])
    if not items:
        return None, None, None, None
    item = items[0]
    return (
        item["id"]["videoId"],
        item["snippet"]["title"],
        item["snippet"]["description"],
        item["snippet"]["thumbnails"].get("high", {}).get("url", "")
    )

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
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": base_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{thumbnail_b64}"}}
            ]
        }]
        response = groq_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=messages,
            max_tokens=150
        )
    else:
        print("Kapak fotoğrafı alınamadı, metin modeli kullanılıyor...")
        messages = [{"role": "user", "content": base_prompt}]
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
        print(f"Hafızadan yüklendi, son video: {last_commented_video_id}")

    while True:
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] Kanal kontrol ediliyor...")
            video_id, title, description, thumbnail_url = get_latest_video(youtube, YOUTUBE_CHANNEL_ID)

            if video_id and video_id != last_commented_video_id:
                print(f"Yeni video: {title} ({video_id})")
                comment_text = generate_comment(title, description, video_id)
                print(f"Groq yorumu: {comment_text}")
                comment_on_video(youtube, f"https://www.youtube.com/watch?v={video_id}", comment_text)
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

    response = youtube.channels().list(part="snippet", mine=True).execute()
    if response.get("items"):
        print(f"Giriş yapılan kanal: {response['items'][0]['snippet']['title']}")

    threading.Thread(target=auto_comment_loop, args=(youtube,), daemon=True).start()

    print("\nFlask sunucusu başlatılıyor (port 5000)...")
    app.run(host="0.0.0.0", port=5000)

if __name__ == "__main__":
    main()
