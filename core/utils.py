from .models import RegistroActividad

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

    RegistroActividad.objects.create(
        usuario=usuario,
        modulo=modulo,
        nivel=nivel,
        accion=accion,
        descripcion=descripcion,
        ip_origen=ip
    )