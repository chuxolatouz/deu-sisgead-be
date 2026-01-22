from flask import Blueprint, request, jsonify, render_template, current_app
from api.extensions import mail
from flask_mail import Message
import threading
import re
import logging

notifications_bp = Blueprint('notifications', __name__)
logger = logging.getLogger(__name__)

def validate_email(email):
    """Valida que el email tenga un formato válido."""
    if not email:
        return False
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def send_async_email(app, mail_obj, subject, recipient, body, is_html=True, sender=None):
    """
    Envía un email de forma asíncrona en un hilo separado.
    Maneja errores y los registra en logs.
    """
    try:
        with app.app_context():
            msg = Message(
                subject=subject,
                recipients=[recipient],
                sender=sender or current_app.config.get('MAIL_DEFAULT_SENDER')
            )
            if is_html:
                msg.html = body
            else:
                msg.body = body
            mail_obj.send(msg)
            logger.info(f"Email enviado exitosamente a {recipient} con asunto: {subject}")
    except Exception as e:
        logger.error(f"Error al enviar email a {recipient}: {str(e)}", exc_info=True)
        # Re-lanzar la excepción para que pueda ser manejada si es necesario
        raise

def send_email_notification_thread(app, subject, recipient, template_name=None, template_vars=None, body=None, is_html=True, sender=None):
    """
    Prepara y envía un email en un hilo separado.
    Soporta tanto templates como body directo.
    """
    try:
        # Si hay template, renderizarlo
        if template_name:
            if not template_vars:
                template_vars = {}
            # Agregar variables por defecto si no están presentes
            if 'titulo' not in template_vars:
                template_vars['titulo'] = subject
            if 'fecha' not in template_vars:
                from datetime import datetime
                template_vars['fecha'] = datetime.now().strftime('%d/%m/%Y')
            if 'plataforma' not in template_vars:
                template_vars['plataforma'] = 'DEU Sistema Administrativo'
            
            email_body = render_template(f'emails/{template_name}', **template_vars)
        elif body:
            email_body = body
        else:
            raise ValueError("Debe proporcionarse template_name o body")
        
        # Crear y ejecutar el thread
        thr = threading.Thread(
            target=send_async_email,
            args=[app, mail, subject, recipient, email_body, is_html, sender],
            daemon=True  # El thread se termina cuando la app principal termina
        )
        thr.start()
        return thr
    except Exception as e:
        logger.error(f"Error al preparar email para {recipient}: {str(e)}", exc_info=True)
        raise

@notifications_bp.route("/send-notification", methods=["POST"])
def send_notification():
    """
    Endpoint para enviar notificaciones por email.
    
    Parámetros requeridos:
    - recipient: Email del destinatario
    - subject: Asunto del email
    
    Parámetros opcionales (uno de los dos es requerido):
    - template: Nombre del template HTML (ej: "notificaciones.html")
    - body: Contenido directo del email (texto o HTML)
    
    - variables: Diccionario con variables para el template (solo si se usa template)
    - is_html: Boolean indicando si el body es HTML (default: True)
    - sender: Email del remitente (opcional, usa MAIL_DEFAULT_SENDER por defecto)
    """
    data = request.get_json()
    
    if not data:
        return jsonify({"message": "No se proporcionaron datos"}), 400
    
    recipient = data.get("recipient")
    subject = data.get("subject")
    template_name = data.get("template")
    body = data.get("body")
    variables = data.get("variables", {})
    is_html = data.get("is_html", True)
    sender = data.get("sender")
    
    # Validaciones
    if not recipient:
        return jsonify({"message": "El campo 'recipient' es requerido"}), 400
    
    if not validate_email(recipient):
        return jsonify({"message": "El formato del email del destinatario no es válido"}), 400
    
    if not subject:
        return jsonify({"message": "El campo 'subject' es requerido"}), 400
    
    if not template_name and not body:
        return jsonify({"message": "Debe proporcionarse 'template' o 'body'"}), 400
    
    if template_name and body:
        return jsonify({"message": "No se puede proporcionar 'template' y 'body' al mismo tiempo"}), 400
    
    # Validar configuración de mail
    if not current_app.config.get('MAIL_SERVER'):
        logger.warning("MAIL_SERVER no está configurado")
        return jsonify({"message": "El servidor de correo no está configurado"}), 500
    
    try:
        app = current_app._get_current_object()
        send_email_notification_thread(
            app=app,
            subject=subject,
            recipient=recipient,
            template_name=template_name,
            template_vars=variables,
            body=body,
            is_html=is_html,
            sender=sender
        )
        return jsonify({
            "message": "Email en cola para envío",
            "recipient": recipient,
            "subject": subject
        }), 200
    except Exception as e:
        logger.error(f"Error al procesar solicitud de email: {str(e)}", exc_info=True)
        return jsonify({"message": f"Error al procesar la solicitud: {str(e)}"}), 500
