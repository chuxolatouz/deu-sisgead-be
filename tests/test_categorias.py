# tests/test_categorias.py
import unittest
from uuid import uuid4

from api.index import app


class CategoriaTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.client.testing = True

        unique_email = f"admin_categorias_{uuid4().hex[:8]}@example.com"
        self.user = {
            "nombre": "Admin",
            "email": unique_email,
            "password": "admin123",
            "rol": "super_admin",
        }
        self.client.post("/registrar", json=self.user)

        login_data = {
            "email": self.user["email"],
            "password": self.user["password"]
        }
        res = self.client.post("/login", json=login_data)
        self.token = res.get_json()["token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def test_01_crear_categoria(self):
        category_name = f"Recursos Humanos {uuid4().hex[:6]}"
        data = {
            "nombre": category_name
        }
        res = self.client.post("/categorias", json=data, headers=self.headers)
        self.assertEqual(res.status_code, 201)
        self.assertIn("message", res.get_json())
        self.assertEqual(res.get_json()["message"], "Categoría creada con éxito")

    def test_02_listar_categorias(self):
        create_res = self.client.post(
            "/categorias",
            json={"nombre": f"Categoría Listado {uuid4().hex[:6]}"},
            headers=self.headers,
        )
        self.assertEqual(create_res.status_code, 201)
        res = self.client.get("/mostrar_categorias?includeInactive=true&includeDeleted=true", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIsInstance(data, list)
        self.assertGreaterEqual(len(data), 1)


if __name__ == '__main__':
    unittest.main()
