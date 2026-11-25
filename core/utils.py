import logging

from .models import RegistroActividad

audit_logger = logging.getLogger('core.audit')

def registrar_log(accion, descripcion, modulo='SISTEMA', nivel='INFO', usuario=None, request=None):
    """Guarda un registro inmutable en el historial de auditoría."""
    ip = None
    if request:
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR')

        if not usuario and request.user.is_authenticated:
            usuario = request.user

    # Siempre a consola (con request-id), aunque falle el guardado en base de datos.
    nivel_log = {'ERROR': logging.ERROR, 'WARNING': logging.WARNING}.get(nivel, logging.INFO)
    audit_logger.log(nivel_log, '%s/%s usuario=%s ip=%s | %s',
                     modulo, accion, getattr(usuario, 'pk', None), ip, descripcion)

    # El registro de auditoria nunca debe tumbar la operacion de negocio.
    try:
        RegistroActividad.objects.create(
            usuario=usuario,
            modulo=modulo,
            nivel=nivel,
            accion=accion,
            descripcion=descripcion,
            ip_origen=ip
        )
    except Exception:
        audit_logger.exception('No se pudo guardar RegistroActividad accion=%s', accion)
