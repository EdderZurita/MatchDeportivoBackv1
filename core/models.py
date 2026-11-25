import secrets
from datetime import timedelta
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator


# ==========================================
# 1. ENTIDAD USUARIO (RF-1, RF-3, RF-19, RF-26)
# ==========================================
class Usuario(AbstractUser):
    NIVEL_CHOICES = [
        ('PRINCIPIANTE', 'Principiante'),
        ('INTERMEDIO', 'Intermedio'),
        ('AVANZADO', 'Avanzado'),
    ]

    email = models.EmailField(unique=True)
    telefono = models.CharField(max_length=20, blank=True, null=True)
    comuna = models.CharField(max_length=100, blank=True, null=True)
    deporte_favorito = models.CharField(max_length=50, blank=True, null=True)
    nivel_habilidad = models.CharField(max_length=20, choices=NIVEL_CHOICES, default='PRINCIPIANTE')
    biografia = models.TextField(max_length=400, blank=True, null=True)
    foto_perfil = models.URLField(blank=True, null=True)
    
    # Estados de negocio y reputación
    es_premium = models.BooleanField(default=False)
    @property
    def reputacion_promedio(self):
        promedio = self.calificaciones_recibidas.aggregate(models.Avg('puntuacion'))['puntuacion__avg']
        return round(promedio, 2) if promedio is not None else 5.00 

    def __str__(self):
        return f"{self.username} ({self.get_full_name() or self.email})"


# ==========================================
# 2. ENTIDAD ACTIVIDAD / PARTIDO (RF-8, RF-9, RF-12, RF-13, RF-15)
# ==========================================
class Actividad(models.Model):
    DEPORTE_CHOICES = [
        ('FUTBOL', 'Fútbol / Pichanga'),
        ('BASKETBOL', 'Básquetbol'),
        ('PADEL', 'Pádel'),
        ('TENIS', 'Tenis'),
        ('VOLEIBOL', 'Voleibol'),
        ('RUNNING', 'Running'),
        ('OTRO', 'Otro'),
    ]

    ESTADO_CHOICES = [
        ('ABIERTA', 'Abierta'),
        ('LLENA', 'Llena'),
        ('FINALIZADA', 'Finalizada'),
        ('CANCELADA', 'Cancelada'),
    ]

    organizador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='actividades_creadas'
    )
    titulo = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True, null=True)
    deporte = models.CharField(max_length=30, choices=DEPORTE_CHOICES, default='FUTBOL')
    nivel_esperado = models.CharField(
        max_length=20,
        choices=Usuario.NIVEL_CHOICES,
        default='INTERMEDIO'
    )
    fecha_hora = models.DateTimeField()

    # Ubicación para el mapa interactivo (MapTiler / OpenStreetMap)
    direccion = models.CharField(max_length=255, help_text="Dirección o nombre del recinto deportivo")
    comuna = models.CharField(max_length=100)
    latitud = models.DecimalField(max_digits=9, decimal_places=6)
    longitud = models.DecimalField(max_digits=9, decimal_places=6)

    # Control de aforo y estado
    cupos_totales = models.PositiveIntegerField(default=10)
    estado = models.CharField(max_length=20, choices=ESTADO_CHOICES, default='ABIERTA')

    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Actividad"
        verbose_name_plural = "Actividades"
        ordering = ['-fecha_hora']

    def __str__(self):
        return f"{self.titulo} - {self.get_deporte_display()} ({self.fecha_hora.strftime('%d/%m %H:%M')})"

    @property
    def cupos_disponibles(self):
        """Calcula las vacantes libres descontando inscripciones confirmadas."""
        confirmados = self.inscripciones.filter(estado='CONFIRMADA').count()
        return max(0, self.cupos_totales - confirmados)


# ==========================================
# 3. ENTIDAD INSCRIPCIÓN (RF-10, RF-11, RF-12, RF-14)
# ==========================================
class Inscripcion(models.Model):
    ESTADO_INSCRIPCION = [
        ('CONFIRMADA', 'Confirmada'),
        ('PENDIENTE', 'Pendiente de Aprobación'),
        ('RECHAZADA', 'Rechazada'),
        ('EN_ESPERA', 'En Lista de Espera'),
        ('CANCELADA', 'Cancelada / Abandonada'),
        ('EXPULSADA', 'Expulsado por Organizador'),
    ]

    actividad = models.ForeignKey(
        Actividad,
        on_delete=models.CASCADE,
        related_name='inscripciones'
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='mis_inscripciones'
    )
    estado = models.CharField(max_length=20, choices=ESTADO_INSCRIPCION, default='CONFIRMADA')
    fecha_inscripcion = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Inscripción"
        verbose_name_plural = "Inscripciones"
        unique_together = ('actividad', 'usuario')

    def __str__(self):
        return f"{self.usuario.username} -> {self.actividad.titulo} [{self.estado}]"


# ==========================================
# 4. ENTIDAD AMISTAD Y RED SOCIAL (RF-5, RF-6, RF-7)
# ==========================================
class Amistad(models.Model):
    ESTADO_AMISTAD = [
        ('PENDIENTE', 'Pendiente'),
        ('ACEPTADA', 'Aceptada'),
        ('RECHAZADA', 'Rechazada'),
    ]

    solicitante = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='amistades_solicitadas'
    )
    receptor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='amistades_recibidas'
    )
    estado = models.CharField(max_length=20, choices=ESTADO_AMISTAD, default='PENDIENTE')
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Amistad"
        verbose_name_plural = "Amistades"
        unique_together = ('solicitante', 'receptor')

    def __str__(self):
        return f"{self.solicitante.username} -> {self.receptor.username} ({self.estado})"


# ==========================================
# 5. ENTIDAD MENSAJE / CHAT UNIFICADO (RF-16)
# ==========================================
class Mensaje(models.Model):
    # Chat Grupal por Actividad (opcional)
    actividad = models.ForeignKey(
        Actividad,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='mensajes'
    )
    # Emisor del mensaje
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='mensajes_enviados'
    )
    # Receptor en chat 1 a 1 (opcional)
    receptor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='mensajes_recibidos'
    )

    contenido = models.TextField(max_length=500)
    fecha_envio = models.DateTimeField(auto_now_add=True)

    # Marca de auditoría de seguridad
    sospechoso = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Mensaje"
        verbose_name_plural = "Mensajes"
        ordering = ['fecha_envio']

    def __str__(self):
        if self.actividad:
            return f"[{self.actividad.titulo}] {self.usuario.username}: {self.contenido[:25]}"
        destino = self.receptor.username if self.receptor else 'Privado'
        return f"[1 a 1 -> {destino}] {self.usuario.username}: {self.contenido[:25]}"

# ==========================================
# 6. ENTIDAD VALORACIÓN DE JUGADOR (RF-18, RF-19)
# ==========================================
class ValoracionJugador(models.Model):
    evaluador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='evaluaciones_realizadas'
    )
    evaluado = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='evaluaciones_recibidas'
    )
    actividad = models.ForeignKey(
        Actividad,
        on_delete=models.CASCADE,
        related_name='valoraciones_jugadores'
    )
    puntuacion = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Puntaje de 1 a 5"
    )
    comentario = models.TextField(max_length=300, blank=True, null=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Valoración de Jugador"
        verbose_name_plural = "Valoraciones de Jugadores"
        unique_together = ('evaluador', 'evaluado', 'actividad')

    def __str__(self):
        return f"{self.evaluador.username} valoró a {self.evaluado.username} con {self.puntuacion}★"


# ==========================================
# 7. ENTIDAD RESEÑA DE ACTIVIDAD (RF-27)
# ==========================================
class ResenaActividad(models.Model):
    actividad = models.ForeignKey(
        Actividad,
        on_delete=models.CASCADE,
        related_name='resenas'
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='resenas_actividades'
    )
    puntuacion = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Calificación de la actividad (1 a 5)"
    )
    comentario = models.TextField(max_length=500)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Reseña de Actividad"
        verbose_name_plural = "Reseñas de Actividades"
        unique_together = ('actividad', 'usuario')

    def __str__(self):
        return f"Reseña de {self.usuario.username} en {self.actividad.titulo}: {self.puntuacion}★"




# ==========================================
# 9. ENTIDAD SUSCRIPCIÓN PREMIUM (RF-22, RF-23, RF-24, RNF-6)
# ==========================================
class Suscripcion(models.Model):
    ESTADO_SUSCRIPCION = [
        ('PENDIENTE', 'Pendiente de activación'),
        ('ACTIVA', 'Activa'),
        ('CANCELADA', 'Cancelada por el usuario'),
        ('VENCIDA', 'Vencida / Impaga / Morosa'),
    ]

    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='suscripcion'
    )
    # Identificadores de Flow PSP (Cumple estándar PCI-DSS al almacenar únicamente tokens y referencias)
    flow_customer_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    flow_subscription_id = models.CharField(max_length=100, blank=True, null=True, unique=True)

    # Token temporal de registro o transacción emitido por Flow para trazabilidad y conciliación
    flow_token = models.CharField(max_length=255, blank=True, null=True, db_index=True)

    estado = models.CharField(max_length=20, choices=ESTADO_SUSCRIPCION, default='PENDIENTE')
    fecha_inicio = models.DateTimeField(null=True, blank=True)
    fecha_vencimiento = models.DateTimeField(null=True, blank=True)
    
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Suscripción Premium"
        verbose_name_plural = "Suscripciones Premium"
        ordering = ['-creado_en']

    def __str__(self):
        return f"Suscripción de {self.usuario.username} [{self.estado}]"



    # ==========================================
# 10. RECUPERACIÓN DE CONTRASEÑA (CÓDIGO OTP)
# ==========================================
class CodigoRecuperacion(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='codigos_recuperacion'
    )
    codigo = models.CharField(max_length=6)
    creado_en = models.DateTimeField(auto_now_add=True)
    usado = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Código de Recuperación'
        verbose_name_plural = 'Códigos de Recuperación'
        ordering = ['-creado_en']

    @classmethod
    def generar_codigo(cls, usuario):
        """Invalida códigos anteriores no usados y genera uno nuevo de 6 dígitos."""
        cls.objects.filter(usuario=usuario, usado=False).update(usado=True)
        nuevo_codigo = f'{secrets.randbelow(900000) + 100000}'
        return cls.objects.create(usuario=usuario, codigo=nuevo_codigo)

    def es_valido(self):
        """El código tiene 15 minutos de validez y solo puede ser usado una vez."""
        expira = self.creado_en + timedelta(minutes=15)
        return not self.usado and timezone.now() <= expira

    def __str__(self):
        return f"{self.usuario.username} - {self.codigo} (Usado: {self.usado})"



class DispositivoFCM(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='dispositivos_fcm'
    )
    token = models.CharField(max_length=255, unique=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.usuario.username} - {self.token[:15]}..."


from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


# ----------------------------------------------------
# RF-18 y RF-19: CALIFICACIONES Y REPUTACIÓN DINÁMICA
# ----------------------------------------------------
class Calificacion(models.Model):
    evaluador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='calificaciones_emitidas'
    )
    evaluado = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='calificaciones_recibidas'
    )
    actividad = models.ForeignKey(
        'Actividad',
        on_delete=models.CASCADE,
        related_name='calificaciones'
    )
    puntuacion = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comentario = models.TextField(blank=True, null=True, max_length=300)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Un usuario solo puede calificar una vez al mismo compañero por partido
        unique_together = ('evaluador', 'evaluado', 'actividad')
        ordering = ['-creado_en']

    def __str__(self):
        return f"{self.evaluador.username} -> {self.evaluado.username} ({self.puntuacion}★)"


# ----------------------------------------------------
# RF-20: REPORTE DE CONDUCTA O CONTENIDO INAPROPIADO
# ----------------------------------------------------
class Reporte(models.Model):
    MOTIVOS = [
        ('CONDUCTA_ANTIDEPORTIVA', 'Conducta antideportiva o agresiva'),
        ('INASISTENCIA', 'No asistió al partido sin avisar'),
        ('LENGUAJE_INAPROPIADO', 'Acoso o insultos en el chat'),
        ('SPAM_FRAUDE', 'Spam o intento de estafa'),
        ('OTRO', 'Otro motivo'),
    ]

    ESTADOS = [
        ('PENDIENTE', 'Pendiente de revisión'),
        ('REVISADO', 'Revisado por moderación'),
        ('DESESTIMADO', 'Desestimado'),
    ]

    reportante = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reportes_enviados'
    )
    reportado = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reportes_recibidos'
    )
    actividad = models.ForeignKey(
        'Actividad',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reportes'
    )
    motivo = models.CharField(max_length=30, choices=MOTIVOS)
    descripcion = models.TextField(max_length=500)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='PENDIENTE')
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado_en']

    def __str__(self):
        return f"Reporte: {self.reportante.username} contra {self.reportado.username} [{self.motivo}]"


# ----------------------------------------------------
# RF-21: BLOQUEO ENTRE USUARIOS
# ----------------------------------------------------
class Bloqueo(models.Model):
    bloqueador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bloqueos_realizados'
    )
    bloqueado = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bloqueos_sufridos'
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('bloqueador', 'bloqueado')
        ordering = ['-creado_en']

    def __str__(self):
        return f"{self.bloqueador.username} bloqueó a {self.bloqueado.username}"

class RegistroActividad(models.Model):
    NIVELES = [
        ('INFO', 'Información'),
        ('SUCCESS', 'Éxito'),
        ('WARNING', 'Advertencia'),
        ('ERROR', 'Error Crítico'),
    ]

    MODULOS = [
        ('PAGOS', 'Pasarela de Pagos / Flow'),
        ('ACTIVIDADES', 'Partidos y Convocatorias'),
        ('USUARIOS', 'Cuentas y Autenticación'),
        ('AMISTADES', 'Red Social y Contactos'),
        ('SISTEMA', 'Operaciones del Sistema'),
    ]

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs_generados'
    )
    modulo = models.CharField(max_length=20, choices=MODULOS, default='SISTEMA')
    nivel = models.CharField(max_length=10, choices=NIVELES, default='INFO')
    accion = models.CharField(max_length=120)  # Ej: 'SUSCRIPCION_ACTIVADA', 'INTENTO_LIMITE_GRATIS'
    descripcion = models.TextField()
    ip_origen = models.GenericIPAddressField(null=True, blank=True)
    fecha_hora = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Registro de Auditoría'
        verbose_name_plural = 'Registros de Auditoría (Logs)'
        ordering = ['-fecha_hora']

    def __str__(self):
        user_str = self.usuario.username if self.usuario else 'Sistema'
        return f"[{self.fecha_hora.strftime('%d/%m/%Y %H:%M')}] [{self.nivel}] {user_str} - {self.accion}"