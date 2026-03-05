import unittest
from datetime import datetime
from uuid import uuid4

from api.index import app


class ProjectCategoriesManagementTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.client.testing = True

        admin_email = f"admin_cat_{uuid4().hex[:8]}@example.com"
        user_email = f"user_cat_{uuid4().hex[:8]}@example.com"

        self.admin_user = {
            "nombre": "Admin Categorias",
            "email": admin_email,
            "password": "admin123",
            "rol": "super_admin",
        }
        self.normal_user = {
            "nombre": "Usuario Base",
            "email": user_email,
            "password": "user123",
            "rol": "usuario",
        }

        self.client.post("/registrar", json=self.admin_user)
        self.client.post("/registrar", json=self.normal_user)

        admin_login = self.client.post(
            "/login",
            json={"email": self.admin_user["email"], "password": self.admin_user["password"]},
        )
        user_login = self.client.post(
            "/login",
            json={"email": self.normal_user["email"], "password": self.normal_user["password"]},
        )

        self.admin_headers = {"Authorization": f"Bearer {admin_login.get_json().get('token')}"}
        self.user_headers = {"Authorization": f"Bearer {user_login.get_json().get('token')}"}

    def _create_category(self, name, headers=None):
        response = self.client.post(
            "/categorias",
            json={"nombre": name},
            headers=headers or self.admin_headers,
        )
        return response

    def _create_project(self, name, category_ref):
        payload = {
            "nombre": name,
            "descripcion": f"Descripción {name}",
            "fecha_inicio": "2026-01-01",
            "fecha_fin": "2026-12-31",
            "categoria": category_ref,
        }
        return self.client.post("/crear_proyecto", json=payload, headers=self.admin_headers)

    def test_create_category_and_duplicate_conflict(self):
        category_name = f"Categoria-{uuid4().hex[:8]}"
        first = self._create_category(category_name)
        self.assertEqual(first.status_code, 201)
        first_data = first.get_json()
        self.assertIn("category", first_data)
        self.assertTrue(first_data["category"]["activo"])
        self.assertFalse(first_data["category"]["eliminado"])

        duplicate = self._create_category(category_name)
        self.assertEqual(duplicate.status_code, 409)

    def test_only_super_admin_can_manage_categories(self):
        category_name = f"Categoria-NoAdmin-{uuid4().hex[:8]}"
        response = self._create_category(category_name, headers=self.user_headers)
        self.assertEqual(response.status_code, 403)

    def test_change_state_delete_and_restore_category(self):
        category_name = f"Categoria-Estado-{uuid4().hex[:8]}"
        created = self._create_category(category_name)
        self.assertEqual(created.status_code, 201)
        category_id = created.get_json()["_id"]

        disabled = self.client.patch(
            f"/categorias/{category_id}/estado",
            json={"activo": False},
            headers=self.admin_headers,
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.get_json()["category"]["activo"])

        deleted = self.client.delete(f"/categorias/{category_id}", headers=self.admin_headers)
        self.assertEqual(deleted.status_code, 200)

        restored = self.client.post(f"/categorias/{category_id}/restaurar", headers=self.admin_headers)
        self.assertEqual(restored.status_code, 200)
        self.assertTrue(restored.get_json()["category"]["activo"])
        self.assertFalse(restored.get_json()["category"]["eliminado"])

    def test_project_category_rules_for_inactive_and_deleted(self):
        active_name = f"Categoria-Proyecto-Activa-{uuid4().hex[:8]}"
        active_response = self._create_category(active_name)
        self.assertEqual(active_response.status_code, 201)
        active_id = active_response.get_json()["_id"]

        project_name = f"Proyecto-{uuid4().hex[:8]}"
        project_response = self._create_project(project_name, active_id)
        self.assertEqual(project_response.status_code, 201)
        project_id = project_response.get_json()["_id"]

        disable_response = self.client.patch(
            f"/categorias/{active_id}/estado",
            json={"activo": False},
            headers=self.admin_headers,
        )
        self.assertEqual(disable_response.status_code, 200)

        create_with_inactive = self._create_project(f"Proyecto-Inactivo-{uuid4().hex[:8]}", active_id)
        self.assertEqual(create_with_inactive.status_code, 400)

        keep_same_inactive = self.client.put(
            f"/actualizar_proyecto/{project_id}",
            json={
                "nombre": project_name,
                "descripcion": f"Descripción actualizada {datetime.utcnow().isoformat()}",
                "categoria": active_id,
            },
            headers=self.admin_headers,
        )
        self.assertEqual(keep_same_inactive.status_code, 200)

        another_name = f"Categoria-Proyecto-Inactiva-{uuid4().hex[:8]}"
        another_response = self._create_category(another_name)
        self.assertEqual(another_response.status_code, 201)
        another_id = another_response.get_json()["_id"]
        self.client.patch(
            f"/categorias/{another_id}/estado",
            json={"activo": False},
            headers=self.admin_headers,
        )

        switch_to_other_inactive = self.client.put(
            f"/actualizar_proyecto/{project_id}",
            json={
                "nombre": project_name,
                "descripcion": "Intento de cambio a categoría inactiva",
                "categoria": another_id,
            },
            headers=self.admin_headers,
        )
        self.assertEqual(switch_to_other_inactive.status_code, 400)

        deleted_name = f"Categoria-Proyecto-Eliminada-{uuid4().hex[:8]}"
        deleted_response = self._create_category(deleted_name)
        self.assertEqual(deleted_response.status_code, 201)
        deleted_id = deleted_response.get_json()["_id"]
        self.client.delete(f"/categorias/{deleted_id}", headers=self.admin_headers)

        switch_to_deleted = self.client.put(
            f"/actualizar_proyecto/{project_id}",
            json={
                "nombre": project_name,
                "descripcion": "Intento de cambio a categoría eliminada",
                "categoria": deleted_id,
            },
            headers=self.admin_headers,
        )
        self.assertEqual(switch_to_deleted.status_code, 400)


if __name__ == "__main__":
    unittest.main()
