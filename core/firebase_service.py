import logging
import os
import firebase_admin
from firebase_admin import credentials, messaging
from django.conf import settings
from .models import DispositivoFCM

logger = logging.getLogger(__name__)

# 1. Ruta hacia el archivo JSON en la raíz del proyecto
cred_path = os.path.join(settings.BASE_DIR, 'firebase_credentials.json')

# 2. Inicialización única de Firebase
if not firebase_admin._apps:
    if os.path.exists(cred_path):
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    else:
        logger.warning("firebase_credentials.json no encontrado. Notificaciones push inactivas.")


def enviar_notificacion_push(usuario, titulo, cuerpo, data=None):
    """
    Envía una notificación push a todos los dispositivos registrados de un usuario
    usando send_each_for_multicast compatible con versiones modernas de firebase-admin.
    """
    tokens = list(DispositivoFCM.objects.filter(usuario=usuario).values_list('token', flat=True))
    if not tokens:
        return

    # Mensaje Multicast
    mensaje = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=titulo,
            body=cuerpo,
        ),
        data={str(k): str(v) for k, v in (data or {}).items()},
        tokens=tokens,
    )

    try:
        # MÉTODO ACTUALIZADO: send_each_for_multicast
        response = messaging.send_each_for_multicast(mensaje)
        logger.info("FCM enviado: %s mensajes entregados.", response.success_count)

        # Limpieza de tokens vencidos o apps desinstaladas
        if response.failure_count > 0:
            tokens_invalidos = [
                tokens[idx] for idx, res in enumerate(response.responses) if not res.success
            ]
            if tokens_invalidos:
                DispositivoFCM.objects.filter(token__in=tokens_invalidos).delete()

    except Exception as e:
        logger.exception("Error enviando notificación FCM")