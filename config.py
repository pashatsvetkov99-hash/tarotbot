import os
import sys
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY")  # base64(client_id:client_secret)
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
FREE_USES = int(os.getenv("FREE_USES", "1"))
STARS_PRICE = int(os.getenv("STARS_PRICE", "50"))
PAID_CREDITS = int(os.getenv("PAID_CREDITS", "5"))
MAX_SITUATION_LEN = 500
DB_PATH = os.getenv("DB_PATH", "tarot.db")
ADMIN_IDS: set[int] = {1139252768}

# Startup validation
_missing = []
if not BOT_TOKEN:
    _missing.append("BOT_TOKEN")
if not GIGACHAT_AUTH_KEY:
    _missing.append("GIGACHAT_AUTH_KEY")
if _missing:
    print(f"ERROR: Missing env vars: {', '.join(_missing)}")
    sys.exit(1)