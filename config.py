import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")   # https://xxx.up.railway.app
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")               # hisobchiAI_2026_new
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "")  # bo‘sh bo‘lsa manual
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "justyaviz7").lstrip("@")
