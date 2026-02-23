from flask import make_response, request, jsonify, current_app
from jose import jwt
from functools import wraps
from api.util.utils import obtener_contexto_departamento_desde_header


def _to_camel_case(value):
    parts = value.split("_")
    if len(parts) <= 1:
        return value
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _to_snake_case(value):
    output = []
    for idx, ch in enumerate(value):
        if ch.isupper() and idx > 0:
            output.append("_")
        output.append(ch.lower())
    return "".join(output)


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
            data = request.get_json(silent=True) or {}
            for field, datatype in schema.items():
                alt_field = _to_camel_case(field) if "_" in field else _to_snake_case(field)
                selected_field = None
                if field in data:
                    selected_field = field
                elif alt_field in data:
                    selected_field = alt_field

                if not selected_field:
                    return jsonify({"message": f"El campo '{field}' es requerido"}), 400

                if selected_field != field and field not in data:
                    data[field] = data[selected_field]

                if not isinstance(data[selected_field], datatype):
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
            data["departmentId"] = contexto_dept
            # Marcar que está usando contexto temporal
            data["_using_dept_context"] = True
            print(f"[DEBUG] Contexto de departamento aplicado: {contexto_dept} para usuario {data.get('email', 'N/A')}")

        return f(data, *args, **kwargs)

    return decorated
