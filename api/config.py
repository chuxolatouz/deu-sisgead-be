import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev_key_fallback")
    MONGO_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/enii")
    
    # Mail Config
    MAIL_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("SMTP_PORT", 465))
    MAIL_USERNAME = os.getenv("SMTP_USER")
    MAIL_PASSWORD = os.getenv("SMTP_PASSWORD")
    MAIL_DEFAULT_SENDER = os.getenv("EMAIL_SENDER")
    MAIL_USE_TLS = False
    MAIL_USE_SSL = True
