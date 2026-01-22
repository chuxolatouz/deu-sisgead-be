from datetime import datetime, timezone
import json
from bson import ObjectId
from api.extensions import mongo

class JSONEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        return super().default(o)

class CustomJSONEncoder(JSONEncoder):
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        elif isinstance(o, datetime):
            return o.isoformat()
        return JSONEncoder.default(self, o)

def agregar_log(id_proyecto, mensaje):
    data = {}
    data["id_proyecto"] = ObjectId(id_proyecto)
    data["fecha_creacion"] = datetime.now(timezone.utc)
    data["mensaje"] = mensaje
    mongo.db.logs.insert_one(data)
    return "registro agregado"
