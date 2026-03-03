import os
from dotenv import load_dotenv

load_dotenv()

def norm_https(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    # Telegram WebApp only HTTPS
    if not url.startswith("https://"):
        url = url.replace("http://", "https://", 1)
    return url.rstrip("/")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Railway domain (must be https://...)
WEBHOOK_URL = norm_https(os.getenv("WEBHOOK_URL", ""))

# Admin panel token (your: hisobchiAI_2026_new)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

# Admin username (without @)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "justyaviz7").strip().lstrip("@")

# Telegram Payments provider (optional)
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "").strip()

# Optional: OpenAI for voice STT (if you want voice -> text auto)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# Currency
CURRENCY = "UZS"
