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
    
    # Swagger/Flasgger Config
    SWAGGER = {
        'title': 'DEU SISGEAD API',
        'uiversion': 3,
        'version': '1.0.0',
        'description': 'API REST para el Sistema de Gestión Administrativa de Proyectos, Presupuestos, Usuarios y Departamentos',
        'termsOfService': '',
        'hide_top_bar': False,
        'specs_route': '/apidocs/',
        'static_url_path': '/flasgger_static',
        'headers': [],
        'specs': [
            {
                'endpoint': 'apispec',
                'route': '/apispec.json',
                'rule_filter': lambda rule: True,
                'model_filter': lambda tag: True,
            }
        ]
    }
