from flask import Blueprint, request, jsonify
from bson import json_util
import json
import random
import string
from api.extensions import mongo
from api.util.decorators import allow_cors, validar_datos

categories_bp = Blueprint('categories', __name__)

@categories_bp.route("/mostrar_categorias", methods=["GET"])
@allow_cors
def obtener_categorias():
    """
    Listar categorías con búsqueda opcional
    ---
    tags:
      - Categorías
    parameters:
      - in: query
        name: text
        type: string
        description: Texto para buscar en el nombre de la categoría
        example: "Material"
    responses:
      200:
        description: Lista de categorías
        schema:
          type: array
          items:
            type: object
            properties:
              _id:
                type: string
                example: "507f1f77bcf86cd799439011"
              nombre:
                type: string
                example: "Material de Oficina"
              color:
                type: string
                example: "FF5733"
                description: Color en formato hexadecimal
    """
    search_text = request.args.get("text")
    if search_text:
        cursor = mongo.db.categorias.find(
            {"nombre": {"$regex": search_text, "$options": "i"}}
        )
    else:
        cursor = mongo.db.categorias.find()
    
    list_cursor = list(cursor)
    list_dump = json.dumps(list_cursor, default=json_util.default, ensure_ascii=False)
    list_json = json.loads(list_dump.replace("\\", ""))
    return jsonify(list_json), 200

@categories_bp.route("/categorias", methods=["POST"])
@allow_cors
@validar_datos({"nombre": str})
def crear_categorias():
    """
    Crear una nueva categoría
    ---
    tags:
      - Categorías
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - nombre
          properties:
            nombre:
              type: string
              description: Nombre de la categoría
              example: "Material de Oficina"
    responses:
      201:
        description: Categoría creada con éxito
        schema:
          type: object
          properties:
            message:
              type: string
              example: "Categoría creada con éxito"
            _id:
              type: string
              example: "507f1f77bcf86cd799439011"
      400:
        description: Datos inválidos
        schema:
          type: object
          properties:
            message:
              type: string
    """
    data = request.get_json()
    nombre = data["nombre"]
    color = "".join(random.choices(string.hexdigits[:-6], k=6))
    categoria = {"nombre": nombre, "color": color}
    categoria_insertada = mongo.db.categorias.insert_one(categoria)
    
    return jsonify({"message": "Categoría creada con éxito", "_id": str(categoria_insertada.inserted_id)}), 201
