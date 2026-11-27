from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Inscripcion, Actividad


# ==========================================
# 1. CONTROL AUTOMÁTICO DE ESTADO Y CUPOS (RF-11, RF-12)
# ==========================================
@receiver([post_save, post_delete], sender=Inscripcion)
def actualizar_estado_cupos_actividad(sender, instance, **kwargs):
    """
    Recalcula el estado de la actividad ('ABIERTA' o 'LLENA')
    cada vez que una inscripción se crea, confirma, cancela o elimina.
    """
    actividad = instance.actividad

    # Si la actividad ya fue cancelada o finalizada por el creador, no se altera su estado
    if actividad.estado in ['CANCELADA', 'FINALIZADA']:
        return

    # Si se llenaron los cupos, se cierra automáticamente la convocatoria (RF-12)
    if actividad.cupos_disponibles <= 0:
        if actividad.estado != 'LLENA':
            actividad.estado = 'LLENA'
            actividad.save(update_fields=['estado'])
    else:
        # Si se liberó un cupo por abandono o expulsión, vuelve a abrirse (RF-11)
        if actividad.estado == 'LLENA':
            actividad.estado = 'ABIERTA'
            actividad.save(update_fields=['estado'])


# ==========================================
# 2. REPUTACIÓN (RF-18, RF-19)
# ==========================================
# La reputación ya no se guarda: Usuario.reputacion_promedio es una propiedad que
# calcula el promedio de las calificaciones recibidas al consultarla. Por eso se
# eliminó la señal que intentaba asignarla (provocaba AttributeError / error 500).
