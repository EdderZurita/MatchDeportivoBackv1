"""Sincronización del Premium con el estado real de la suscripción en Flow (C3).

Flow cobra las renovaciones por su cuenta y no avisa a la app. Por eso, cuando el período
guardado de un usuario Premium termina, se consulta ``subscription/get`` una vez (con un
intervalo mínimo entre consultas) y se decide:

* al día (status activo y sin mora)  -> se extiende ``fecha_vencimiento`` con el ``period_end`` de Flow;
* moroso (``morose == 1``)           -> VENCIDA, se quita Premium;
* ya no activa, o con cancelación al fin del período -> CANCELADA, Premium hasta ``period_end``.

Si Flow no responde o la respuesta no es la esperada, NO se toca nada: es preferible dejar
Premium un rato de más que quitárselo a alguien que sí está pagando.
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .flow_service import FlowService
from .utils import expirar_premium_si_corresponde, registrar_log

logger = logging.getLogger(__name__)

ZONA_FLOW = ZoneInfo('America/Santiago')
FORMATO_FECHA_FLOW = '%Y-%m-%d %H:%M:%S'
FLOW_STATUS_ACTIVA = 1   # subscription/get: status 1 = suscripción activa
FLOW_MOROSA = 1          # subscription/get: morose 1 = uno o más importes vencidos

SIN_CAMBIOS = 'SIN_CAMBIOS'
RENOVADA = 'RENOVADA'
CANCELADA = 'CANCELADA'
MOROSA = 'MOROSA'
ERROR = 'ERROR'


def _parsear_fecha_flow(valor):
    """Flow entrega fechas 'yyyy-mm-dd hh:mm:ss' (hora de Chile). None si no se entiende."""
    if not valor:
        return None
    try:
        return datetime.strptime(str(valor)[:19], FORMATO_FECHA_FLOW).replace(tzinfo=ZONA_FLOW)
    except ValueError:
        return None


def _respuesta_valida(datos):
    return isinstance(datos, dict) and 'code' not in datos and 'status' in datos and 'morose' in datos


def sincronizar_con_flow(usuario, sub, ahora=None):
    """Consulta Flow y actualiza la suscripción. Devuelve uno de los códigos de este módulo."""
    ahora = ahora or timezone.now()
    if not sub.flow_subscription_id:
        return SIN_CAMBIOS

    datos = FlowService.obtener_suscripcion(sub.flow_subscription_id)
    if not _respuesta_valida(datos):
        logger.warning('sync premium: respuesta inesperada de Flow para usuario=%s', usuario.pk)
        return ERROR

    periodo_fin = _parsear_fecha_flow(datos.get('period_end'))
    gracia = timedelta(days=settings.PREMIUM_GRACIA_DIAS)

    # 1) Mora: el importe venció sin pagarse -> fuera de Premium ahora.
    if int(datos.get('morose') or 0) == FLOW_MOROSA:
        sub.estado = 'VENCIDA'
        sub.save(update_fields=['estado', 'actualizado_en'])
        usuario.es_premium = False
        usuario.save(update_fields=['es_premium'])
        registrar_log(accion='PREMIUM_MOROSO', modulo='PAGOS', nivel='WARNING', usuario=usuario,
                      descripcion=f'{usuario.username} perdió Premium: Flow informa un importe vencido sin pagar.')
        return MOROSA

    # 2) Ya no activa en Flow, o cancelada para el fin del período: conserva lo pagado.
    if int(datos.get('status') or 0) != FLOW_STATUS_ACTIVA or int(datos.get('cancel_at_period_end') or 0) == 1:
        sub.estado = 'CANCELADA'
        if periodo_fin:
            sub.fecha_vencimiento = periodo_fin + gracia
        sub.save(update_fields=['estado', 'fecha_vencimiento', 'actualizado_en'])
        registrar_log(accion='PREMIUM_CANCELADO_EN_FLOW', modulo='PAGOS', nivel='INFO', usuario=usuario,
                      descripcion=f'Flow informa la suscripción de {usuario.username} sin renovación; '
                                  f'Premium hasta {sub.fecha_vencimiento:%d-%m-%Y}.')
        expirar_premium_si_corresponde(usuario, ahora)
        return CANCELADA

    # 3) Al día: se extiende con el período real que informa Flow.
    if periodo_fin is None:
        logger.warning('sync premium: Flow no informó period_end para usuario=%s', usuario.pk)
        return ERROR
    nuevo_vencimiento = periodo_fin + gracia
    if sub.fecha_vencimiento is None or nuevo_vencimiento > sub.fecha_vencimiento:
        sub.fecha_vencimiento = nuevo_vencimiento
        sub.save(update_fields=['fecha_vencimiento', 'actualizado_en'])
        registrar_log(accion='PREMIUM_RENOVADO', modulo='PAGOS', nivel='INFO', usuario=usuario,
                      descripcion=f'Renovación detectada para {usuario.username}; Premium hasta '
                                  f'{nuevo_vencimiento:%d-%m-%Y}.')
        return RENOVADA
    return SIN_CAMBIOS


def verificar_premium(usuario, ahora=None):
    """Punto de entrada por request: mantiene al día el Premium de un usuario autenticado."""
    if not getattr(usuario, 'es_premium', False):
        return
    ahora = ahora or timezone.now()
    sub = getattr(usuario, 'suscripcion', None)
    if sub is None:
        return

    if sub.estado == 'CANCELADA':
        expirar_premium_si_corresponde(usuario, ahora)
        return

    if (settings.PREMIUM_SYNC_ENABLED and sub.estado == 'ACTIVA'
            and sub.fecha_vencimiento and sub.fecha_vencimiento <= ahora):
        # cache.add es atómico: solo la primera petición de cada intervalo consulta a Flow.
        if cache.add(f'premium_sync:{sub.pk}', 1, settings.PREMIUM_SYNC_INTERVALO_SEG):
            try:
                sincronizar_con_flow(usuario, sub, ahora)
            except Exception:  # nunca romper la autenticación por un problema de sincronización
                logger.exception('sync premium falló usuario=%s', usuario.pk)
