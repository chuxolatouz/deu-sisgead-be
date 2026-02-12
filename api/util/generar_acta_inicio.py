from datetime import datetime

import pdfkit
from jinja2 import Template


PLACEHOLDER = "(POR DEFINIR)"


def _safe_text(value, fallback=PLACEHOLDER):
    if value is None:
        return fallback
    if isinstance(value, str) and not value.strip():
        return fallback
    return value


def generar_acta_inicio_pdf(proyecto, departamento=None, recursos=None, firmantes=None):
    recursos = recursos or []
    firmantes = firmantes or []
    fecha_emision = datetime.now().strftime("%d/%m/%Y")

    objetivos_especificos = proyecto.get("objetivos_especificos") or []
    if isinstance(objetivos_especificos, str):
        objetivos_especificos = [objetivos_especificos]

    html_template = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
      <meta charset="UTF-8" />
      <style>
        body { font-family: Arial, sans-serif; margin: 24px 40px; font-size: 12px; }
        h1 { text-align: center; font-size: 18px; }
        h2 { font-size: 14px; margin-top: 16px; border-bottom: 1px solid #ccc; }
        table { width: 100%; border-collapse: collapse; margin: 8px 0; }
        th, td { border: 1px solid #777; padding: 6px; }
      </style>
    </head>
    <body>
      <h1>ACTA DE CONSTITUCION DEL PROYECTO</h1>

      <h2>INFORMACION GENERAL DEL PROYECTO</h2>
      <table>
        <tr><th>Nombre del proyecto</th><td>{{ nombre }}</td></tr>
        <tr><th>Codigo</th><td>{{ codigo }}</td></tr>
        <tr><th>Departamento</th><td>{{ departamento }}</td></tr>
        <tr><th>Fecha de emision</th><td>{{ fecha_emision }}</td></tr>
      </table>

      <h2>DESCRIPCION DEL PROYECTO</h2>
      <p>{{ descripcion }}</p>

      <h2>OBJETIVOS</h2>
      <p><strong>Objetivo general:</strong> {{ objetivo_general }}</p>
      {% if objetivos_especificos %}
      <ul>
        {% for obj in objetivos_especificos %}
        <li>{{ obj }}</li>
        {% endfor %}
      </ul>
      {% else %}
      <p>{{ placeholder }}</p>
      {% endif %}

      <h2>JUSTIFICACION</h2>
      <p>{{ justificacion }}</p>

      <h2>ALCANCE</h2>
      <p><strong>Alcance del producto:</strong> {{ alcance_producto }}</p>
      <p><strong>Alcance del proyecto:</strong> {{ alcance_proyecto }}</p>
    </body>
    </html>
    """

    html = Template(html_template).render(
        placeholder=PLACEHOLDER,
        nombre=_safe_text(proyecto.get("nombre"), "N/A"),
        codigo=_safe_text(proyecto.get("codigo"), "N/A"),
        departamento=_safe_text(departamento, "N/A"),
        fecha_emision=fecha_emision,
        descripcion=_safe_text(proyecto.get("descripcion")),
        objetivo_general=_safe_text(proyecto.get("objetivo_general")),
        objetivos_especificos=objetivos_especificos,
        justificacion=_safe_text(proyecto.get("justificacion")),
        alcance_producto=_safe_text(proyecto.get("alcance_producto")),
        alcance_proyecto=_safe_text(proyecto.get("alcance_proyecto")),
        recursos=recursos,
        firmantes=firmantes,
    )

    return pdfkit.from_string(html, False)
