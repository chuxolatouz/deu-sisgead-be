from jinja2 import Template
import pdfkit
from datetime import datetime

def generar_acta_inicio_pdf(proyecto):
    html_template = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Acta de Inicio</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 40px; }
            h1, h2 { text-align: center; }
            .seccion { margin-bottom: 20px; }
            .bold { font-weight: bold; }
            ul { padding-left: 20px; }
        </style>
    </head>
    <body>
        <h1>Universidad Central de Venezuela</h1>
        <h2>Facultad de Ciencias - ENII</h2>
        <h2>Acta de Inicio del Proyecto</h2>
        <div class="seccion">
            <p><span class="bold">Nombre del Proyecto:</span> {{ nombre }}</p>
            <p><span class="bold">Descripción:</span> {{ descripcion }}</p>
            <p><span class="bold">Fecha de Inicio:</span> {{ fecha }}</p>
            <p><span class="bold">Balance Inicial:</span> Bs. {{ balance }}</p>
        </div>
        <div class="seccion">
            <h2>Objetivo General</h2>
            <p>{{ objetivo_general }}</p>

            <h2>Objetivos Específicos</h2>
            <ul>
            {% for obj in objetivos_especificos %}
                <li>{{ obj }}</li>
            {% endfor %}
            </ul>
            <p class="bold">Miembros del Proyecto:</p>
            <ul>
                {% for m in miembros %}
                    <li>{{ m }}</li>
                {% endfor %}
            </ul>
        </div>
    </body>
    </html>
    """

    miembros = []
    for m in proyecto.get("miembros", []):
        nombre = m["usuario"].get("nombre", "Desconocido")
        rol = m["role"]["label"]
        fecha = m.get("fecha_ingreso", "Sin fecha")
        miembros.append(f"{nombre} como {rol}, ingresó el {fecha}")

    html = Template(html_template).render(
        nombre=proyecto.get("nombre", "Sin nombre"),
        descripcion=proyecto.get("descripcion", "Sin descripción"),
        fecha=proyecto.get("fecha_inicio", "Sin fecha"),
        objetivo_general=proyecto.get("objetivo_general", ""),
        objetivos_especificos=proyecto.get("objetivos_especificos", []),
        balance=proyecto.get("balance_inicial", "0.00"),
        miembros=miembros
    )

    pdf = pdfkit.from_string(html, False)
    return pdf