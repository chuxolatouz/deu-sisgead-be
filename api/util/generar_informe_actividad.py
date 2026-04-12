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
        body {
          font-family: Arial, sans-serif;
          margin: 20px 28px;
          font-size: 11px;
          color: #1f2937;
          line-height: 1.45;
        }
        .header {
          border-bottom: 2px solid #0f172a;
          margin-bottom: 14px;
          padding-bottom: 8px;
          text-align: center;
        }
        .header p {
          margin: 0;
          font-size: 11px;
        }
        h1 {
          text-align: center;
          font-size: 18px;
          margin: 8px 0 2px 0;
          color: #111827;
        }
        .subtitle {
          text-align: center;
          margin: 0 0 12px 0;
          font-size: 10px;
          color: #4b5563;
        }
        h2 {
          font-size: 13px;
          margin: 14px 0 8px 0;
          color: #0f172a;
          border-bottom: 1px solid #d1d5db;
          padding-bottom: 4px;
        }
        table {
          width: 100%;
          border-collapse: collapse;
          margin: 8px 0;
        }
        th, td {
          border: 1px solid #94a3b8;
          padding: 6px;
          text-align: left;
          vertical-align: top;
        }
        th {
          background: #eef2f7;
          color: #0f172a;
          font-weight: bold;
          width: 30%;
        }
        .wide th {
          width: auto;
        }
        p {
          margin: 0 0 8px 0;
          text-align: justify;
        }
      </style>
    </head>
    <body>
      <div class="header">
        <p>Universidad Central de Venezuela</p>
        <p>Facultad de Ciencias - Escuela de Computacion</p>
        <p>Direccion de Extension Universitaria</p>
      </div>
      <h1>INFORME DE ACTIVIDAD</h1>
      <p class="subtitle">Seguimiento operativo del proyecto</p>

      <h2>IDENTIFICACION</h2>
      <table>
        <tr><th>FECHA</th><td>{{ fecha }}</td></tr>
        <tr><th>NOMBRE DE LA ACTIVIDAD</th><td>{{ nombre_actividad }}</td></tr>
        <tr><th>UBICACION</th><td>{{ ubicacion }}</td></tr>
        <tr><th>PROYECTO RELACIONADO</th><td>{{ proyecto_nombre }}</td></tr>
      </table>

      <h2>CONTEXTO</h2>
      <table>
        <tr><th>OBJETIVO</th><td>{{ objetivo }}</td></tr>
        <tr><th>LINEA ESTRATEGICA</th><td>{{ linea_estrategica }}</td></tr>
        <tr><th>DESCRIPCION</th><td>{{ descripcion }}</td></tr>
      </table>

      <h2>EJECUCION</h2>
      <table>
        <tr><th>RECURSOS HUMANOS UTILIZADOS</th><td>{{ recursos_humanos }}</td></tr>
        <tr><th>RECURSOS UTILIZADOS</th><td>{{ recursos }}</td></tr>
        <tr><th>RESULTADOS OBTENIDOS</th><td>{{ resultados }}</td></tr>
      </table>

      <h2>EVALUACION</h2>
      <table class="wide">
        <tr><th>LOGROS</th><th>LIMITACIONES</th><th>LECCIONES</th><th>LINEAS DE ACCION</th></tr>
        <tr><td>{{ logros }}</td><td>{{ limitaciones }}</td><td>{{ lecciones }}</td><td>{{ lineas_accion }}</td></tr>
      </table>

      <h2>SOPORTES</h2>
      <table>
        <tr><th>REGISTRO FOTOGRAFICO</th><td>{{ registro_fotografico }}</td></tr>
        <tr><th>FACTURA</th><td>{{ factura }}</td></tr>
        <tr><th>MONTO DE LA ACTIVIDAD</th><td>{{ presupuesto }}</td></tr>
        <tr><th>RETENCIONES DE IMPUESTO</th><td>{{ retenciones_impuesto }}</td></tr>
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
        "registro_fotografico": PLACEHOLDER,
        "factura": PLACEHOLDER,
        "presupuesto": PLACEHOLDER,
        "retenciones_impuesto": PLACEHOLDER,
        "proyecto_nombre": proyecto.get("nombre") or PLACEHOLDER,
    }

    context = {key: _safe_text(data.get(key, defaults[key]), defaults[key]) for key in defaults}
    html = Template(html_template).render(**context)
    options = {
        "encoding": "UTF-8",
        "page-size": "A4",
        "margin-top": "12mm",
        "margin-right": "12mm",
        "margin-bottom": "14mm",
        "margin-left": "12mm",
        "print-media-type": "",
    }
    return pdfkit.from_string(html, False, options=options)
