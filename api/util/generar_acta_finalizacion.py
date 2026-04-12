import pdfkit
from jinja2 import Template


def _safe_text(value, fallback="N/A"):
    if value is None:
        return fallback
    if isinstance(value, str) and not value.strip():
        return fallback
    return str(value)


def _format_bs(value):
    try:
        numeric = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        numeric = 0.0
    formatted = f"{numeric:,.2f}"
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"Bs. {formatted}"


def generar_acta_finalizacion_pdf(proyecto, movements=None, logs=None, budgets=None):
    movements = movements or []
    logs = logs or []
    budgets = budgets or []

    project_name = _safe_text(proyecto.get("nombre", ""), "Proyecto")
    description = _safe_text(proyecto.get("descripcion", ""))
    start_date = _safe_text(proyecto.get("fecha_inicio", ""))
    end_date = _safe_text(proyecto.get("fecha_fin", ""))
    objective = _safe_text(proyecto.get("objetivo_general", "No especificado"))
    objectives = proyecto.get("objetivos_especificos") or []
    if isinstance(objectives, str):
        objectives = [objectives]

    normalized_movements = []
    for item in movements:
        if not isinstance(item, dict):
            continue
        normalized_movements.append(
            {
                "tipo": _safe_text(item.get("type") or item.get("title"), "Movimiento"),
                "usuario": _safe_text(item.get("user") or item.get("actorName"), "N/A"),
                "monto": _format_bs(item.get("amount", 0)),
            }
        )

    normalized_logs = []
    for item in logs:
        if not isinstance(item, dict):
            continue
        normalized_logs.append(
            {
                "fecha": _safe_text(item.get("fecha") or item.get("fecha_creacion"), "N/A"),
                "mensaje": _safe_text(item.get("mensaje") or item.get("message"), "N/A"),
            }
        )

    normalized_budgets = []
    for item in budgets:
        if not isinstance(item, dict):
            continue
        normalized_budgets.append(
            {
                "descripcion": _safe_text(item.get("descripcion"), "N/A"),
                "monto": _format_bs(item.get("monto_aprobado", item.get("monto", 0))),
            }
        )

    html_template = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="utf-8">
        <title>Acta de Finalización</title>
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
            .card {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                overflow: hidden;
                margin-bottom: 10px;
            }
            .card-row {
                display: table;
                width: 100%;
                border-bottom: 1px solid #e2e8f0;
            }
            .card-row:last-child {
                border-bottom: none;
            }
            .card-label, .card-value {
                display: table-cell;
                padding: 6px 8px;
                vertical-align: top;
            }
            .card-label {
                width: 35%;
                font-weight: bold;
                color: #111827;
                background: #f8fafc;
            }
            .card-value {
                width: 65%;
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
            p {
                margin: 0 0 8px 0;
                text-align: justify;
            }
            ul {
                margin: 0 0 8px 16px;
                padding: 0;
            }
            li {
                margin-bottom: 4px;
            }
        </style>
    </head>
    <body>
        <div class="header">
            <p>Universidad Central de Venezuela</p>
            <p>Facultad de Ciencias - Escuela de Computacion</p>
            <p>Direccion de Extension Universitaria</p>
        </div>
        <h1>ACTA DE FINALIZACION DEL PROYECTO</h1>
        <p class="subtitle">Documento de cierre administrativo y financiero</p>

        <h2>INFORMACION GENERAL</h2>
        <div class="card">
            <div class="card-row">
                <div class="card-label">Nombre del proyecto</div>
                <div class="card-value">{{ nombre }}</div>
            </div>
            <div class="card-row">
                <div class="card-label">Descripcion</div>
                <div class="card-value">{{ descripcion }}</div>
            </div>
            <div class="card-row">
                <div class="card-label">Fecha de inicio</div>
                <div class="card-value">{{ fecha_inicio }}</div>
            </div>
            <div class="card-row">
                <div class="card-label">Fecha de finalizacion</div>
                <div class="card-value">{{ fecha_fin }}</div>
            </div>
            <div class="card-row">
                <div class="card-label">Saldo inicial asignado</div>
                <div class="card-value">{{ saldo_inicial }}</div>
            </div>
            <div class="card-row">
                <div class="card-label">Saldo final</div>
                <div class="card-value">{{ saldo_final }}</div>
            </div>
        </div>

        <h2>OBJETIVO GENERAL</h2>
        <p>{{ objetivo_general }}</p>

        <h2>OBJETIVOS ESPECIFICOS</h2>
        {% if objetivos_especificos %}
            <ul>
                {% for objetivo in objetivos_especificos %}
                    <li>{{ objetivo }}</li>
                {% endfor %}
            </ul>
        {% else %}
            <p>No se han definido objetivos especificos.</p>
        {% endif %}

        <h2>MOVIMIENTOS MONETARIOS</h2>
        {% if movements %}
            <table>
                <thead>
                    <tr>
                        <th style="width: 46%;">Movimiento</th>
                        <th style="width: 24%;">Usuario</th>
                        <th style="width: 30%;">Monto</th>
                    </tr>
                </thead>
                <tbody>
                    {% for mov in movements %}
                    <tr>
                        <td>{{ mov.tipo }}</td>
                        <td>{{ mov.usuario }}</td>
                        <td>{{ mov.monto }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        {% else %}
            <p>No se registraron movimientos monetarios.</p>
        {% endif %}

        <h2>BITACORA DEL PROYECTO</h2>
        {% if logs %}
            <table>
                <thead>
                    <tr>
                        <th style="width: 24%;">Fecha</th>
                        <th style="width: 76%;">Mensaje</th>
                    </tr>
                </thead>
                <tbody>
                    {% for log in logs %}
                    <tr>
                        <td>{{ log.fecha }}</td>
                        <td>{{ log.mensaje }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        {% else %}
            <p>No se registraron logs.</p>
        {% endif %}

        <h2>ACTIVIDADES ASOCIADAS</h2>
        {% if budgets %}
            <table>
                <thead>
                    <tr>
                        <th style="width: 72%;">Descripcion</th>
                        <th style="width: 28%;">Monto</th>
                    </tr>
                </thead>
                <tbody>
                    {% for budget in budgets %}
                    <tr>
                        <td>{{ budget.descripcion }}</td>
                        <td>{{ budget.monto }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        {% else %}
            <p>No se registraron actividades asociadas.</p>
        {% endif %}
    </body>
    </html>
    """

    html = Template(html_template).render(
        nombre=project_name,
        descripcion=description,
        fecha_inicio=start_date,
        fecha_fin=end_date,
        objetivo_general=objective,
        objetivos_especificos=objectives,
        saldo_inicial=_format_bs(proyecto.get("balance_inicial", 0)),
        saldo_final=_format_bs(proyecto.get("balance", 0)),
        movements=normalized_movements,
        logs=normalized_logs,
        budgets=normalized_budgets,
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
    pdf_bytes = pdfkit.from_string(html, False, options=options)
    return pdf_bytes
