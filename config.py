import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-key-for-internal-use-only-123")
    DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/planner.db")
    ENABLE_SCREENSHOT_UPLOAD = (
        os.environ.get("ENABLE_SCREENSHOT_UPLOAD", "true").lower() == "true"
    )
    GA_MEASUREMENT_ID = os.environ.get("GA_MEASUREMENT_ID")
    EXTERNAL_API_SECRET = os.environ.get("EXTERNAL_API_SECRET", "mN4!pQs6JrYwV9")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB upload limit for images
    # Admin PIN to protect event creation
    ADMIN_PIN = os.environ.get("ADMIN_PIN", "kvk2025")
    SUPERADMIN_SECRET = os.environ.get("SUPERADMIN_SECRET", "")
    # Groq API key for free AI image analysis (get yours at console.groq.com)
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

