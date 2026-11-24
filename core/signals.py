from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.db.models import Avg
from .models import Inscripcion, Actividad, ValoracionJugador, Usuario


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
# 2. RECÁLCULO AUTOMÁTICO DE REPUTACIÓN (RF-18, RF-19)
# ==========================================
@receiver([post_save, post_delete], sender=ValoracionJugador)
def actualizar_reputacion_usuario(sender, instance, **kwargs):
    """
    Calcula la media aritmética de todas las puntuaciones recibidas (1 a 5★)
    y actualiza la reputación promedio del jugador evaluado en la base de datos.
    """
    jugador = instance.evaluado
    promedio = ValoracionJugador.objects.filter(evaluado=jugador).aggregate(Avg('puntuacion'))['puntuacion__avg']

    if promedio is not None:
        jugador.reputacion_promedio = round(promedio, 2)
    else:
        # Si no tiene valoraciones (o se borraron), conserva la calificación base
        jugador.reputacion_promedio = 5.00

    jugador.save(update_fields=['reputacion_promedio'])