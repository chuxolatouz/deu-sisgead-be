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


def generar_informe_actividad_pdf(proyecto, data):
    html_template = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
      <meta charset="UTF-8" />
      <style>
        body { font-family: Arial, sans-serif; margin: 24px 40px; font-size: 12px; }
        h1 { text-align: center; font-size: 18px; }
        table { width: 100%; border-collapse: collapse; margin: 8px 0; }
        th, td { border: 1px solid #777; padding: 6px; }
      </style>
    </head>
    <body>
      <h1>INFORME DE ACTIVIDAD</h1>
      <table>
        <tr><th>FECHA</th><td>{{ fecha }}</td></tr>
        <tr><th>NOMBRE DE LA ACTIVIDAD</th><td>{{ nombre_actividad }}</td></tr>
        <tr><th>UBICACION</th><td>{{ ubicacion }}</td></tr>
      </table>
      <table>
        <tr><th>OBJETIVO</th><td>{{ objetivo }}</td></tr>
        <tr><th>LINEA ESTRATEGICA</th><td>{{ linea_estrategica }}</td></tr>
        <tr><th>DESCRIPCION</th><td>{{ descripcion }}</td></tr>
      </table>
      <table>
        <tr><th>RECURSOS HUMANOS UTILIZADOS</th><td>{{ recursos_humanos }}</td></tr>
        <tr><th>RECURSOS UTILIZADOS</th><td>{{ recursos }}</td></tr>
        <tr><th>RESULTADOS OBTENIDOS</th><td>{{ resultados }}</td></tr>
      </table>
      <table>
        <tr><th>LOGROS</th><th>LIMITACIONES</th><th>LECCIONES</th><th>LINEAS DE ACCION</th></tr>
        <tr><td>{{ logros }}</td><td>{{ limitaciones }}</td><td>{{ lecciones }}</td><td>{{ lineas_accion }}</td></tr>
      </table>
    </body>
    </html>
    """

    defaults = {
        "fecha": datetime.now().strftime("%d-%m-%Y"),
        "nombre_actividad": proyecto.get("nombre") or PLACEHOLDER,
        "ubicacion": PLACEHOLDER,
        "objetivo": proyecto.get("objetivo_general") or PLACEHOLDER,
        "linea_estrategica": PLACEHOLDER,
        "descripcion": proyecto.get("descripcion") or PLACEHOLDER,
        "recursos_humanos": PLACEHOLDER,
        "recursos": PLACEHOLDER,
        "resultados": PLACEHOLDER,
        "logros": PLACEHOLDER,
        "limitaciones": PLACEHOLDER,
        "lecciones": PLACEHOLDER,
        "lineas_accion": PLACEHOLDER,
    }

    context = {key: _safe_text(data.get(key, defaults[key]), defaults[key]) for key in defaults}
    html = Template(html_template).render(**context)
    return pdfkit.from_string(html, False)
