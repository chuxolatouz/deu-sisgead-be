from api.routes.projects import (
    _normalize_project_payload,
    _normalize_project_requirements,
)


def test_normaliza_requerimientos_camel_case_y_espacios():
    payload = _normalize_project_payload({
        "materialesNecesarios": "  Toldos, sillas y mesas.  ",
        "recursosHumanos": "  Personal DEU (27 personas) ",
        "logistica": " Agua, hielo y refrigerios. ",
    })

    normalized = _normalize_project_requirements(payload)

    assert normalized == {
        "materiales_necesarios": "Toldos, sillas y mesas.",
        "recursos_humanos": "Personal DEU (27 personas)",
        "logistica": "Agua, hielo y refrigerios.",
    }


def test_requerimientos_son_opcionales_en_proyectos_nuevos():
    normalized = _normalize_project_requirements({}, include_defaults=True)

    assert normalized == {
        "materiales_necesarios": "",
        "recursos_humanos": "",
        "logistica": "",
    }
