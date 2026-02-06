---
name: DEU SISGEAD Backend Context
description: Comprehensive context about the Flask backend API architecture, patterns, and conventions
---

# DEU SISGEAD Backend Context

This skill provides comprehensive context about the backend Flask API for the DEU Sistema Administrativo project.

## 🏗️ Architecture Overview

### Technology Stack
- **Framework**: Flask 3.0.2 with application factory pattern
- **Database**: MongoDB (via Flask-PyMongo)
- **Authentication**: JWT tokens (PyJWT + python-jose)
- **Security**: Flask-Bcrypt for password hashing
- **CORS**: Flask-CORS (currently allows all origins - TODO: restrict in production)
- **Email**: Flask-Mail with SMTP (Gmail by default)
- **Documentation**: Flasgger (Swagger/OpenAPI)
- **File Storage**: Backblaze B2
- **PDF Generation**: pdfkit with wkhtmltopdf
- **Testing**: pytest

### Project Structure

```
deu-sisgead-be/
├── api/
│   ├── __init__.py          # Application factory (create_app)
│   ├── config.py            # Configuration class with env vars
│   ├── extensions.py        # Flask extensions (mongo, bcrypt, cors, mail)
│   ├── routes/              # Blueprint modules
│   │   ├── auth.py          # Authentication endpoints
│   │   ├── users.py         # User management
│   │   ├── departments.py   # Department management
│   │   ├── categories.py    # Category management
│   │   ├── projects.py      # Project management (largest module)
│   │   ├── documents.py     # Budget/document management
│   │   ├── rules.py         # Distribution rules
│   │   ├── reports.py       # Reports and statistics
│   │   └── notifications.py # Email notifications
│   ├── util/                # Utilities
│   │   ├── common.py        # CustomJSONEncoder, logging
│   │   ├── decorators.py    # @token_required, @validate_json, @allow_cors
│   │   ├── utils.py         # General utilities
│   │   ├── backblaze.py     # B2 file storage integration
│   │   ├── generar_acta_inicio.py      # PDF generation
│   │   └── generar_acta_finalizacion.py
│   └── templates/emails/    # HTML email templates
└── tests/                   # pytest test suite
```

## 🔑 Key Patterns & Conventions

### 1. Application Factory Pattern

The app is created using the factory pattern in `api/__init__.py`:

```python
def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # Initialize extensions
    mongo.init_app(app)
    bcrypt.init_app(app)
    cors.init_app(app)
    mail.init_app(app)
    
    # Register blueprints
    app.register_blueprint(auth_bp)
    # ... more blueprints
    
    return app
```

### 2. Blueprint Organization

Each route module defines a blueprint with a specific prefix:

- `auth_bp` → `/auth`
- `users_bp` → `/users`
- `departments_bp` → `/departments`
- `categories_bp` → `/categories`
- `projects_bp` → `/projects`
- `documents_bp` → `/documents`
- `rules_bp` → `/rules`
- `reports_bp` → `/reports`
- `notifications_bp` → `/notifications`

### 3. Authentication & Authorization

**JWT Token Pattern:**
- Tokens are generated on login with user data
- Protected endpoints use `@token_required` decorator
- Decorator extracts user info from token and passes to route handler

**Decorator Usage:**
```python
from api.util.decorators import token_required

@projects_bp.route('/mostrar_proyectos', methods=['GET'])
@token_required
def mostrar_proyectos(current_user):
    # current_user contains decoded JWT data
    pass
```

**User Roles:**
- `usuario` - Regular user
- `admin_departamento` - Department admin
- `super_admin` - System administrator

### 4. Database Access Pattern

**MongoDB Collections:**
- `users` - User accounts
- `departments` - Organizational departments
- `categories` - Project categories
- `projects` - Projects with budgets and activities
- `presupuestos` - Budget documents
- `reglas_fijas` - Distribution rules
- `solicitudes_reglas_fijas` - Rule requests

**Access Pattern:**
```python
from api.extensions import mongo

# Find one
user = mongo.db.users.find_one({"_id": ObjectId(user_id)})

# Find many with pagination
projects = mongo.db.projects.find(query).skip(page * limit).limit(limit)

# Insert
result = mongo.db.projects.insert_one(project_data)

# Update
mongo.db.projects.update_one({"_id": ObjectId(id)}, {"$set": update_data})
```

### 5. Pagination Pattern

**Standard pagination across all list endpoints:**
- `page` parameter (0-indexed)
- `limit` parameter (default: 10)
- Returns: `{"data": [...], "total": count, "page": page, "limit": limit}`

```python
page = int(request.args.get('page', 0))
limit = int(request.args.get('limit', 10))
total = mongo.db.collection.count_documents(query)
items = list(mongo.db.collection.find(query).skip(page * limit).limit(limit))
return jsonify({"data": items, "total": total, "page": page, "limit": limit})
```

### 6. Error Handling

**Standard error responses:**
```python
return jsonify({"message": "Error description"}), status_code
```

**Common status codes:**
- 200 - Success
- 201 - Created
- 400 - Bad request / validation error
- 401 - Unauthorized (missing/invalid token)
- 404 - Not found
- 500 - Internal server error

### 7. Validation Pattern

Use `@validate_json` decorator for request validation:

```python
from api.util.decorators import validate_json

@projects_bp.route('/crear_proyecto', methods=['POST'])
@token_required
@validate_json(['nombre', 'departamento_id', 'categoria_id'])
def crear_proyecto(current_user):
    data = request.get_json()
    # Required fields are guaranteed to exist
```

### 8. Custom JSON Encoding

**CustomJSONEncoder** in `api/util/common.py` handles:
- `ObjectId` → string conversion
- `datetime` → ISO format string
- `Decimal128` → float conversion

This is automatically applied to all JSON responses.

### 9. Logging Pattern

Use the logging utility from `api/util/common.py`:

```python
from api.util.common import log_info, log_error

log_info("Project created", {"project_id": str(project_id)})
log_error("Failed to create project", {"error": str(e)})
```

### 10. Email Notifications

**Async email sending:**
```python
from api.routes.notifications import send_email_async

send_email_async(
    to_email="user@example.com",
    subject="Subject",
    template_name="notificaciones.html",  # or None for plain text
    template_data={"key": "value"}
)
```

## 🔐 Environment Variables

**Required:**
- `MONGODB_URI` - MongoDB connection string
- `SECRET_KEY` - JWT signing key
- `SMTP_SERVER` - Email server (default: smtp.gmail.com)
- `SMTP_PORT` - Email port (default: 465)
- `SMTP_USER` - Email username
- `SMTP_PASSWORD` - Email password (app password for Gmail)
- `EMAIL_SENDER` - From email address

## 📊 Data Models

### Project Structure
```python
{
    "_id": ObjectId,
    "nombre": str,
    "descripcion": str,
    "departamento_id": ObjectId,
    "categoria_id": ObjectId,
    "fecha_inicio": datetime,
    "fecha_fin": datetime,
    "estado": "new" | "in_progress" | "finished",
    "balance": float,
    "miembros": [ObjectId],  # User IDs
    "objetivos_especificos": [str],
    "presupuestos": [ObjectId],  # Budget document IDs
    "movimientos": [{"tipo": str, "monto": float, "fecha": datetime, ...}],
    "logs": [{"accion": str, "usuario": str, "fecha": datetime, ...}]
}
```

### User Structure
```python
{
    "_id": ObjectId,
    "nombre": str,
    "apellido": str,
    "email": str,
    "password": str,  # bcrypt hashed
    "rol": "usuario" | "admin_departamento" | "super_admin",
    "departamento_id": ObjectId,
    "fecha_creacion": datetime
}
```

### Budget/Document Structure
```python
{
    "_id": ObjectId,
    "proyecto_id": ObjectId,
    "nombre": str,
    "descripcion": str,
    "monto": float,
    "estado": "new" | "in_progress" | "finished",
    "tipo": "egreso" | "ingreso",
    "categoria": str,
    "archivo_url": str,  # Backblaze B2 URL
    "fecha_creacion": datetime,
    "fecha_completado": datetime
}
```

## 🧪 Testing

**Run tests:**
```bash
pytest                    # All tests
pytest --cov=api         # With coverage
pytest tests/test_proyectos.py  # Specific module
```

**Test structure:**
- `conftest.py` - Fixtures and test configuration
- Each module has corresponding test file
- Uses pytest fixtures for app, client, and database setup

## 🚀 Running the Application

**Development:**
```bash
flask --app ./api/__init__.py --debug run
```

**Production:**
```bash
gunicorn api.__init__:create_app
```

## 📝 Swagger Documentation

- **URL**: `/apidocs/`
- **Spec**: `/apispec.json`
- All endpoints should have YAML docstrings for Swagger

**Example:**
```python
@projects_bp.route('/proyecto/<id>', methods=['GET'])
def obtener_proyecto(id):
    """
    Obtener proyecto por ID
    ---
    tags:
      - Proyectos
    parameters:
      - name: id
        in: path
        type: string
        required: true
    responses:
      200:
        description: Proyecto encontrado
      404:
        description: Proyecto no encontrado
    """
```

## 🎯 Common Tasks

### Adding a New Endpoint
1. Create route in appropriate blueprint file in `api/routes/`
2. Add decorators: `@token_required`, `@validate_json` if needed
3. Implement business logic
4. Add Swagger documentation
5. Write tests in `tests/`

### Adding a New Blueprint
1. Create file in `api/routes/`
2. Define blueprint: `bp = Blueprint('name', __name__)`
3. Register in `api/__init__.py`: `app.register_blueprint(bp)`

### Working with MongoDB
- Always use `ObjectId()` for ID conversions
- Use `find_one()` for single documents
- Use `find()` with `.skip()` and `.limit()` for pagination
- CustomJSONEncoder handles ObjectId serialization automatically

## ⚠️ Important Notes

- **Currency**: Venezuelan Bolívares (Bs.) - backend may send as strings with comma decimals
- **CORS**: Currently allows all origins (`*`) - restrict in production
- **File Storage**: Uses Backblaze B2, not local filesystem
- **PDF Generation**: Requires `wkhtmltopdf` system dependency
- **Email**: Async sending via threads to avoid blocking
- **Pagination**: 0-indexed pages (page=0 is first page)
