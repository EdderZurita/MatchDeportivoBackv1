import logging
import re
import time
import uuid

from .logging_utils import request_id_ctx

logger = logging.getLogger('core.request')

# Solo se acepta un X-Request-ID "razonable" (evita inyeccion de saltos de linea en logs).
_REQUEST_ID_RE = re.compile(r'^[A-Za-z0-9._-]{1,64}$')
# Rutas que no se registran (ruido de monitoreo).
_SIN_LOG = ('/health/',)


class RequestIdMiddleware:
    """
    - Asigna un request-id (o reutiliza el X-Request-ID entrante si es valido).
    - Lo devuelve en la cabecera de respuesta X-Request-ID.
    - Registra UNA linea por peticion: metodo, ruta, status, duracion y usuario.
    No registra cuerpos, cabeceras ni query strings (pueden contener tokens/OTP).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        entrante = request.META.get('HTTP_X_REQUEST_ID', '')
        request_id = entrante if _REQUEST_ID_RE.match(entrante) else uuid.uuid4().hex[:16]
        token = request_id_ctx.set(request_id)
        request.request_id = request_id
        inicio = time.monotonic()
        status_code = 500
        try:
            response = self.get_response(request)
            status_code = response.status_code
            response['X-Request-ID'] = request_id
            return response
        finally:
            if request.path not in _SIN_LOG:
                duracion_ms = int((time.monotonic() - inicio) * 1000)
                user = getattr(request, 'user', None)
                user_id = user.pk if getattr(user, 'is_authenticated', False) else '-'
                nivel = logging.ERROR if status_code >= 500 else (
                    logging.WARNING if status_code >= 400 else logging.INFO)
                logger.log(nivel, '%s %s -> %s %sms user=%s',
                           request.method, request.path, status_code, duracion_ms, user_id)
            request_id_ctx.reset(token)
