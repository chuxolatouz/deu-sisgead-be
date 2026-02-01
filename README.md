# DEU Sistema Administrativo - Backend

Backend API REST desarrollado con Flask para el sistema de gestión administrativa de proyectos, presupuestos, usuarios y departamentos.

## 📋 Tabla de Contenidos

- [Requisitos Previos](#requisitos-previos)
- [Instalación](#instalación)
- [Configuración](#configuración)
- [Ejecución](#ejecución)
- [Estructura del Proyecto](#estructura-del-proyecto)
- [API Endpoints](#api-endpoints)
- [Testing](#testing)
- [Tecnologías](#tecnologías)

## 🔧 Requisitos Previos

- Python 3.8 o superior
- MongoDB (local o remoto)
- `wkhtmltopdf` para generación de PDFs:
  ```bash
  # Ubuntu/Debian
  sudo apt install wkhtmltopdf
  
  # macOS
  brew install wkhtmltopdf
  ```

## 📦 Instalación

1. Clonar el repositorio (si aplica)

2. Crear un entorno virtual:
   ```bash
   python -m venv venv
   source venv/bin/activate  # En Windows: venv\Scripts\activate
   ```

3. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```

## ⚙️ Configuración

El proyecto utiliza variables de entorno para la configuración. Crea un archivo `.env` en la raíz del proyecto o configura las siguientes variables:

### Variables de Entorno Requeridas

```bash
# Base de datos MongoDB
MONGODB_URI=mongodb://localhost:27017/enii

# Seguridad
SECRET_KEY=tu_clave_secreta_aqui

# Configuración de Email (SMTP)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=465
SMTP_USER=tu_email@gmail.com
SMTP_PASSWORD=tu_contraseña_de_aplicacion
EMAIL_SENDER=tu_email@gmail.com
```

### Configuración por Defecto

Si no se configuran las variables de entorno, el sistema usará valores por defecto:
- MongoDB: `mongodb://localhost:27017/enii`
- SMTP Server: `smtp.gmail.com`
- SMTP Port: `465`

## 🚀 Ejecución

### Modo Desarrollo

```bash
flask --app ./api/__init__.py --debug run
```

O usando el archivo `index.py`:

```bash
flask --app ./api/index.py --debug run
```

El servidor se ejecutará en `http://localhost:5000` por defecto.

### Modo Producción

Para producción, se recomienda usar un servidor WSGI como Gunicorn:

```bash
gunicorn api.__init__:create_app
```

## 📁 Estructura del Proyecto

```
deu-sisgead-be/
├── api/
│   ├── __init__.py          # Factory de la aplicación Flask
│   ├── index.py             # Punto de entrada alternativo
│   ├── config.py            # Configuración de la aplicación
│   ├── extensions.py        # Extensiones de Flask (MongoDB, Bcrypt, CORS, etc.)
│   ├── routes/              # Blueprints de rutas
│   │   ├── auth.py          # Autenticación (login, registro)
│   │   ├── users.py         # Gestión de usuarios
│   │   ├── departments.py   # Gestión de departamentos
│   │   ├── categories.py    # Gestión de categorías
│   │   ├── projects.py      # Gestión de proyectos
│   │   ├── documents.py     # Gestión de presupuestos/documentos
│   │   ├── rules.py         # Gestión de reglas de distribución
│   │   ├── reports.py       # Reportes y estadísticas
│   │   └── notifications.py # Sistema de notificaciones por email
│   ├── templates/
│   │   └── emails/
│   │       └── notificaciones.html  # Template HTML para emails
│   └── util/                # Utilidades y helpers
│       ├── backblaze.py     # Integración con Backblaze B2
│       ├── common.py        # Funciones comunes (logs, JSON encoder)
│       ├── decorators.py    # Decoradores personalizados (auth, validación, CORS)
│       ├── utils.py         # Utilidades generales
│       ├── generar_acta_inicio.py      # Generación de PDFs de acta de inicio
│       └── generar_acta_finalizacion.py # Generación de PDFs de acta de finalización
├── tests/                   # Tests unitarios e integración
│   ├── conftest.py          # Configuración de pytest
│   ├── test_auth.py
│   ├── test_categorias.py
│   ├── test_presupuestos.py
│   ├── test_proyectos.py
│   ├── test_reglas_fijas.py
│   └── test_users.py
├── requirements.txt         # Dependencias de Python
├── pytest.ini              # Configuración de pytest
└── README.md               # Este archivo
```

## 🔌 API Endpoints

### Autenticación (`/auth`)
- `POST /registrar` - Registrar nuevo usuario
- `POST /login` - Iniciar sesión
- `POST /olvido_contraseña` - Recuperar contraseña

### Usuarios (`/users`)
- `GET /mostrar_usuarios` - Listar usuarios (con paginación)
- `PUT /actualizar_usuario/<id>` - Actualizar usuario
- `DELETE /eliminar_usuario/<id>` - Eliminar usuario

### Departamentos (`/departments`)
- `GET /departamentos` - Listar departamentos
- `POST /crear_departamento` - Crear departamento
- `GET /departamentos/<id>/proyectos` - Proyectos de un departamento
- `GET /departamentos/<id>/usuarios` - Usuarios de un departamento

### Categorías (`/categories`)
- `GET /mostrar_categorias` - Listar categorías
- `POST /categorias` - Crear categoría

### Proyectos (`/projects`)
- `GET /mostrar_proyectos` - Listar proyectos (con paginación)
- `POST /crear_proyecto` - Crear proyecto
- `GET /proyecto/<id>` - Obtener proyecto por ID
- `PUT /actualizar_proyecto/<id>` - Actualizar proyecto
- `POST /eliminar_proyecto` - Eliminar proyecto
- `POST /finalizar_proyecto` - Finalizar proyecto
- `PATCH /asignar_balance` - Asignar balance a proyecto
- `PATCH /asignar_usuario_proyecto` - Asignar usuario a proyecto
- `PATCH /eliminar_usuario_proyecto` - Eliminar usuario de proyecto
- `POST /asignar_regla_distribucion` - Asignar regla de distribución
- `GET /proyecto/<id>/acciones` - Movimientos del proyecto
- `GET /proyecto/<id>/logs` - Logs del proyecto
- `GET /proyecto/<id>/objetivos` - Objetivos específicos

### Documentos/Presupuestos (`/documents`)
- `GET /proyecto/<id>/documentos` - Listar presupuestos (con paginación)
- `POST /documento_crear` - Crear presupuesto
- `PUT /actualizar_documento/<id>` - Actualizar presupuesto
- `POST /eliminar_documento` - Eliminar presupuesto
- `POST /completar_presupuesto` - Completar presupuesto

### Reglas (`/rules`)
- `GET /mostrar_solicitudes` - Listar solicitudes de reglas
- `POST /crear_solicitud_regla_fija` - Crear solicitud de regla
- `POST /completar_solicitud_regla_fija/<id>` - Completar solicitud
- `POST /eliminar_solicitud_regla_fija/<id>` - Eliminar solicitud

### Reportes (`/reports`)
- `GET /proyecto/<id>/reporte` - Reporte de proyecto
- `GET /reporte/proyecto/<id>` - Reporte detallado de proyecto

### Notificaciones (`/notifications`)
- `POST /send-notification` - Enviar notificación por email

## 🧪 Testing

El proyecto utiliza `pytest` para testing. Para ejecutar los tests:

```bash
# Ejecutar todos los tests
pytest

# Ejecutar tests con cobertura
pytest --cov=api

# Ejecutar un test específico
pytest tests/test_proyectos.py
```

## 🛠️ Tecnologías

- **Flask 3.0.2** - Framework web
- **Flask-PyMongo** - Integración con MongoDB
- **Flask-Bcrypt** - Hashing de contraseñas
- **Flask-CORS** - Manejo de CORS
- **Flask-Mail** - Envío de emails
- **PyJWT** - Autenticación con JWT
- **python-jose** - Utilidades JWT adicionales
- **pdfkit** - Generación de PDFs
- **b2sdk** - Integración con Backblaze B2 para almacenamiento
- **pytest** - Framework de testing

## 📝 Notas Adicionales

- El sistema utiliza MongoDB como base de datos NoSQL
- La autenticación se realiza mediante JWT tokens
- Los archivos se almacenan en Backblaze B2
- El sistema genera PDFs para actas de inicio y finalización de proyectos
- Los emails se envían de forma asíncrona usando threads
- La paginación se implementa usando `page` (0-indexed) y `limit` como parámetros

## 🔒 Seguridad

- Las contraseñas se hashean con bcrypt antes de almacenarse
- Los endpoints protegidos requieren autenticación mediante JWT
- Se valida la entrada de datos en endpoints críticos
- CORS configurado (actualmente permite todos los orígenes - revisar en producción)

## 📧 Sistema de Notificaciones

El sistema incluye un módulo completo de notificaciones por email que soporta:
- Envío con templates HTML
- Envío con contenido directo
- Validación de emails
- Envío asíncrono
- Manejo de errores y logging
