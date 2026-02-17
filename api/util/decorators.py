from flask import make_response, request, jsonify, current_app
from jose import jwt
from functools import wraps
from api.util.utils import obtener_contexto_departamento_desde_header
from bson import ObjectId


def allow_cors(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        resp = make_response(f(*args, **kwargs))
        resp.headers['Access-Control-Allow-Origin'] = '*'
        return resp
    return decorated


def validar_datos(schema):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            data = request.get_json()
            for field, datatype in schema.items():
                if field not in data:
                    return jsonify({"message": f"El campo '{field}' es requerido"}), 400
                if not isinstance(data[field], datatype):
                    return jsonify({"message": f"El campo '{field}' debe ser de tipo {datatype.__name__}"}), 400
            return func(*args, **kwargs)
        return wrapper
    return decorator

# Definir una función decoradora para proteger rutas que requieren autenticación


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization")
        if not token:
            return jsonify({"message": "Token no proporcionado"}), 403

        try:
            token = token.split()[1]
            data = jwt.decode(
                token, key=current_app.config["SECRET_KEY"], algorithms=['HS256'])
            print(data)
        except Exception as e:
            print("Error decoding token: %s", str(e))

            return jsonify({"message": "Token no es válido o ha expirado"}), 403

        # Agregar contexto de departamento si es super_admin y tiene el header
        contexto_dept = obtener_contexto_departamento_desde_header(data)
        if contexto_dept:
            data["departamento_id"] = contexto_dept
            # Marcar que está usando contexto temporal
            data["_using_dept_context"] = True
            print(f"[DEBUG] Contexto de departamento aplicado: {contexto_dept} para usuario {data.get('email', 'N/A')}")

        return f(data, *args, **kwargs)

    return decorated


def admin_required(f):
    """
    Decorador para verificar que el usuario tenga rol de super_admin.
    Debe usarse después de token_required.
    """
    @wraps(f)
    def decorated(data_token, *args, **kwargs):
        if data_token.get("role") != "super_admin":
            return jsonify({"message": "Acceso denegado. Se requiere rol de administrador"}), 403
        return f(data_token, *args, **kwargs)
    return decorated


def apply_department_filter(user_data):
    """
    Función helper para aplicar filtro de departamento basado en el rol del usuario.
    
    Args:
        user_data: Datos del usuario decodificados del token JWT
        
    Returns:
        dict: Filtro MongoDB para departamentos o None si es super_admin
    """
    # Super admin ve todo (sin filtro)
    if user_data.get("role") == "super_admin":
        return None
    
    # Admin de departamento y usuarios normales ven solo su(s) departamento(s)
    # Obtener departamentos del usuario
    user_departments = []
    
    # Si el usuario tiene departamento_id en el token
    if "departamento_id" in user_data and user_data["departamento_id"]:
        try:
            dept_id = ObjectId(user_data["departamento_id"])
            user_departments.append(dept_id)
        except Exception:
            pass
    
    # Si no tiene departamentos asignados, no puede ver nada
    if not user_departments:
        # Retornar un filtro que no coincida con nada
        return {"departments": {"$in": [ObjectId("000000000000000000000000")]}}
    
    # Filtrar por cuentas que tengan al menos uno de los departamentos del usuario
    return {"departments": {"$in": user_departments}}
