from flask import Flask
import os
from api.config import Config
from api.extensions import mongo, bcrypt, cors, mail
from api.util.common import CustomJSONEncoder
from flasgger import Swagger

def create_app(config_class=Config):
    app = Flask(__name__, template_folder=os.path.join(os.getcwd(), 'api', 'templates'))
    app.config.from_object(config_class)

    # Initialize extensions
    mongo.init_app(app)
    bcrypt.init_app(app)
    # CORS configuration based on original index.py
    # CORS(app, supports_credentials=True, resources={r"/*": {"origins": "http://localhost:3000"}})
    # Allowing all for now based on 'allow_cors' decorator usage or explicit config
    cors.init_app(app, supports_credentials=True, resources={r"/*": {"origins": "*"}}) # TODO: Restrict in production
    
    # Initialize Swagger with config
    swagger = Swagger(app, config=app.config.get('SWAGGER'))
    
    mail.init_app(app)

    # Custom JSON Encoder
    app.json_encoder = CustomJSONEncoder 
    # For newer Flask versions (2.3+), use app.json.cls = CustomJSONEncoder
    # Assuming Flask 3.0.2 from requirements.txt, checking if we need to set provider
    try:
        app.json_encoder = CustomJSONEncoder
        app.json.cls = CustomJSONEncoder
    except AttributeError:
        pass # Handle version differences

    # Register Blueprints
    from api.routes.auth import auth_bp
    from api.routes.users import users_bp
    from api.routes.departments import departments_bp
    from api.routes.categories import categories_bp
    from api.routes.projects import projects_bp
    from api.routes.documents import documents_bp
    from api.routes.rules import rules_bp
    from api.routes.reports import reports_bp
    from api.routes.notifications import notifications_bp
    from api.routes.accounts import accounts_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(departments_bp)
    app.register_blueprint(categories_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(rules_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(accounts_bp)

    @app.route("/", methods=["GET"])
    def index():
        return "pong"

    @app.errorhandler(400)
    def error_400(e):
        return {"message": "Solicitud incorrecta"}, 400

    @app.errorhandler(401)
    def error_401(e):
        return {"message": "No autorizado"}, 401

    @app.errorhandler(404)
    def error_404(e):
        return {"message": "No encontrado"}, 404

    @app.errorhandler(500)
    def error_500(e):
        return {"message": "Error interno del servidor"}, 500

    return app
