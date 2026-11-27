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


DIAS_CICLO_PREMIUM = 30


def fin_del_periodo_pagado(suscripcion, ahora=None):
    """Fecha en que termina el ciclo de 30 días ya pagado por el usuario (RF-24).

    Flow renueva el cobro cada ciclo sin avisar a la app, por eso se calcula desde
    fecha_inicio: el primer múltiplo de 30 días posterior a ``ahora``.
    """
    from datetime import timedelta
    from django.utils import timezone
    ahora = ahora or timezone.now()
    if suscripcion.fecha_inicio:
        ciclo = timedelta(days=DIAS_CICLO_PREMIUM)
        transcurrido = ahora - suscripcion.fecha_inicio
        ciclos = max(1, -(-transcurrido // ciclo))  # redondeo hacia arriba
        fin = suscripcion.fecha_inicio + ciclo * ciclos
        if fin > ahora:
            return fin
        return fin + ciclo
    return suscripcion.fecha_vencimiento or (ahora + timedelta(days=DIAS_CICLO_PREMIUM))


def expirar_premium_si_corresponde(usuario, ahora=None):
    """Quita Premium si la suscripción fue cancelada y su período pagado ya terminó.

    Devuelve True si el usuario perdió Premium en esta llamada.
    """
    from django.utils import timezone
    ahora = ahora or timezone.now()
    if not getattr(usuario, 'es_premium', False):
        return False
    sub = getattr(usuario, 'suscripcion', None)
    if sub is None or sub.estado != 'CANCELADA':
        return False
    if sub.fecha_vencimiento and sub.fecha_vencimiento > ahora:
        return False
    usuario.es_premium = False
    usuario.save(update_fields=['es_premium'])
    registrar_log(
        accion='PREMIUM_EXPIRADO',
        descripcion=f'Premium de {usuario.username} terminó al vencer el período pagado tras cancelar.',
        modulo='PAGOS',
        nivel='INFO',
        usuario=usuario,
    )
    return True
