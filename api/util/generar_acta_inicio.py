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


def _format_bs(value):
    try:
        numeric = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        numeric = 0.0
    formatted = f"{numeric:,.2f}"
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"Bs. {formatted}"


def generar_acta_inicio_pdf(proyecto, departamento=None, recursos=None, firmantes=None):
    recursos = recursos or []
    firmantes = firmantes or []
    fecha_emision = datetime.now().strftime("%d/%m/%Y")

    objetivos_especificos = proyecto.get("objetivos_especificos") or []
    if isinstance(objetivos_especificos, str):
        objetivos_especificos = [objetivos_especificos]

    recursos_normalizados = []
    for item in recursos:
        if not isinstance(item, dict):
            continue
        recursos_normalizados.append(
            {
                "cuenta": _safe_text(item.get("cuenta"), "N/A"),
                "descripcion": _safe_text(item.get("descripcion"), PLACEHOLDER),
                "monto": _format_bs(item.get("monto", 0)),
            }
        )

    firmantes_normalizados = []
    for item in firmantes:
        if not isinstance(item, dict):
            continue
        firmantes_normalizados.append(
            {
                "entidad": _safe_text(item.get("entidad"), "N/A"),
                "nombre": _safe_text(item.get("nombre"), PLACEHOLDER),
                "cargo": _safe_text(item.get("cargo"), PLACEHOLDER),
                "correo": _safe_text(item.get("correo"), PLACEHOLDER),
                "telefono": _safe_text(item.get("telefono"), PLACEHOLDER),
            }
        )

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
        .title {
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
        .info-card {
          border: 1px solid #cbd5e1;
          border-radius: 6px;
          overflow: hidden;
          margin-bottom: 10px;
        }
        .info-row {
          display: table;
          width: 100%;
          border-bottom: 1px solid #e2e8f0;
        }
        .info-row:last-child {
          border-bottom: none;
        }
        .info-label, .info-value {
          display: table-cell;
          padding: 6px 8px;
          vertical-align: top;
        }
        .info-label {
          width: 35%;
          font-weight: bold;
          color: #111827;
          background: #f8fafc;
        }
        .info-value {
          width: 65%;
        }
        p {
          margin: 0 0 8px 0;
          text-align: justify;
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
        }
        ul {
          margin: 0 0 8px 16px;
          padding: 0;
        }
        li {
          margin-bottom: 4px;
        }
        .signer {
          margin: 10px 0 12px 0;
          border-top: 1px solid #cbd5e1;
          padding-top: 6px;
        }
        .signer strong {
          color: #0f172a;
        }
      </style>
    </head>
    <body>
      <div class="header">
        <p>Universidad Central de Venezuela</p>
        <p>Facultad de Ciencias - Escuela de Computacion</p>
        <p>Direccion de Extension Universitaria</p>
      </div>
      <h1 class="title">ACTA DE CONSTITUCION DEL PROYECTO</h1>
      <p class="subtitle">Documento de formalizacion de inicio</p>

      <h2>INFORMACION GENERAL DEL PROYECTO</h2>
      <div class="info-card">
        <div class="info-row">
          <div class="info-label">Nombre del proyecto</div>
          <div class="info-value">{{ nombre }}</div>
        </div>
        <div class="info-row">
          <div class="info-label">Codigo</div>
          <div class="info-value">{{ codigo }}</div>
        </div>
        <div class="info-row">
          <div class="info-label">Departamento</div>
          <div class="info-value">{{ departamento }}</div>
        </div>
        <div class="info-row">
          <div class="info-label">Fecha de emision</div>
          <div class="info-value">{{ fecha_emision }}</div>
        </div>
      </div>

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

      <h2>RECURSOS ASIGNADOS</h2>
      {% if recursos %}
      <table>
        <thead>
          <tr>
            <th style="width: 26%;">Cuenta</th>
            <th style="width: 50%;">Descripcion</th>
            <th style="width: 24%;">Monto</th>
          </tr>
        </thead>
        <tbody>
          {% for item in recursos %}
          <tr>
            <td>{{ item.cuenta }}</td>
            <td>{{ item.descripcion }}</td>
            <td>{{ item.monto }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% else %}
      <p>{{ placeholder }}</p>
      {% endif %}

      <h2>ACEPTACION Y FIRMAS</h2>
      {% if firmantes %}
        {% for firmante in firmantes %}
        <div class="signer">
          <p><strong>{{ firmante.entidad }}</strong></p>
          <p>{{ firmante.nombre }} - {{ firmante.cargo }}</p>
          <p>Correo: {{ firmante.correo }} | Telefono: {{ firmante.telefono }}</p>
        </div>
        {% endfor %}
      {% else %}
      <p>{{ placeholder }}</p>
      {% endif %}
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
        recursos=recursos_normalizados,
        firmantes=firmantes_normalizados,
    )

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
