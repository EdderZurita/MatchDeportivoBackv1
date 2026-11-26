from rest_framework import serializers
from django.utils import timezone

from .models import (
    Usuario,
    Actividad,
    Inscripcion,
    Amistad,
    Mensaje,
    ValoracionJugador,
    ResenaActividad,
    Calificacion,
    Reporte,
    Bloqueo,
    Suscripcion,
    RegistroActividad,
)


# ==========================================
# 1. USUARIOS Y AUTENTICACIÓN (RF-1, RF-3, RF-5)
# ==========================================
class UsuarioSerializer(serializers.ModelSerializer):
    reputacion_promedio = serializers.FloatField(read_only=True)

    class Meta:
        model = Usuario
        fields = (
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'telefono',
            'comuna',
            'deporte_favorito',
            'nivel_habilidad',
            'biografia',
            'foto_perfil',
            'es_premium',
            'reputacion_promedio',
            'is_staff',      
            'is_superuser',
        )
        read_only_fields = ('id', 'es_premium', 'reputacion_promedio', 'is_staff', 'is_superuser')

    # Campos privados: solo los ve el propio usuario o un administrador (hotfix C6).
    CAMPOS_PRIVADOS = ('email', 'telefono', 'is_staff', 'is_superuser')

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        solicitante = getattr(request, 'user', None)
        es_propio = bool(solicitante and getattr(solicitante, 'pk', None) == instance.pk)
        es_admin = bool(solicitante and getattr(solicitante, 'is_staff', False))
        if not (es_propio or es_admin):
            for campo in self.CAMPOS_PRIVADOS:
                data.pop(campo, None)
        return data


class RegistroUsuarioSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = Usuario
        fields = (
            'id',
            'username',
            'email',
            'password',
            'first_name',
            'last_name',
            'comuna',
            'deporte_favorito',
            'nivel_habilidad',
        )

    def create(self, validated_data):
        return Usuario.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', ''),
            comuna=validated_data.get('comuna', ''),
            deporte_favorito=validated_data.get('deporte_favorito', ''),
            nivel_habilidad=validated_data.get('nivel_habilidad', 'PRINCIPIANTE'),
        )


# ==========================================
# 2. ACTIVIDADES Y CONVOCATORIAS (RF-8 a RF-15)
# ==========================================
class ActividadSerializer(serializers.ModelSerializer):
    organizador_nombre = serializers.ReadOnlyField(source='organizador.username')
    organizador_es_premium = serializers.BooleanField(source='organizador.es_premium', read_only=True)
    deporte_display = serializers.ReadOnlyField(source='get_deporte_display')
    cupos_disponibles = serializers.ReadOnlyField()

    class Meta:
        model = Actividad
        fields = '__all__'
        read_only_fields = ('organizador', 'creado_en', 'actualizado_en')

    def validate_fecha_hora(self, value):
        if value < timezone.now():
            raise serializers.ValidationError("La fecha y hora del partido no puede ser en el pasado.")
        return value


class InscripcionSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.ReadOnlyField(source='usuario.username')
    actividad_titulo = serializers.ReadOnlyField(source='actividad.titulo')

    class Meta:
        model = Inscripcion
        fields = '__all__'
        read_only_fields = ('usuario', 'fecha_inscripcion')


# ==========================================
# 3. RED SOCIAL Y AMISTADES (RF-6, RF-7)
# ==========================================
class AmistadSerializer(serializers.ModelSerializer):
    solicitante_username = serializers.ReadOnlyField(source='solicitante.username')
    receptor_username = serializers.ReadOnlyField(source='receptor.username')

    class Meta:
        model = Amistad
        fields = '__all__'
        read_only_fields = ('solicitante', 'creado_en')


# ==========================================
# 4. CHAT Y MENSAJERÍA (RF-16)
# ==========================================
class MensajeSerializer(serializers.ModelSerializer):
    emisor_username = serializers.CharField(source='usuario.username', read_only=True)
    receptor_username = serializers.CharField(source='receptor.username', read_only=True)

    class Meta:
        model = Mensaje
        fields = [
            'id',
            'actividad',
            'usuario',
            'emisor_username',
            'receptor',
            'receptor_username',
            'contenido',
            'fecha_envio',
            'sospechoso',
        ]
        read_only_fields = ['id', 'usuario', 'fecha_envio', 'sospechoso']


# ==========================================
# 5. REPUTACIÓN Y VALORACIONES (RF-18, RF-19, RF-27)
# ==========================================
class ValoracionJugadorSerializer(serializers.ModelSerializer):
    evaluador_nombre = serializers.ReadOnlyField(source='evaluador.username')
    evaluado_nombre = serializers.ReadOnlyField(source='evaluado.username')

    class Meta:
        model = ValoracionJugador
        fields = '__all__'
        read_only_fields = ('evaluador', 'creado_en')


class ResenaActividadSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.ReadOnlyField(source='usuario.username')

    class Meta:
        model = ResenaActividad
        fields = '__all__'
        read_only_fields = ('usuario', 'creado_en')


class CalificacionSerializer(serializers.ModelSerializer):
    evaluador_nombre = serializers.ReadOnlyField(source='evaluador.username')
    evaluado_nombre = serializers.ReadOnlyField(source='evaluado.username')

    class Meta:
        model = Calificacion
        fields = [
            'id',
            'evaluador',
            'evaluador_nombre',
            'evaluado',
            'evaluado_nombre',
            'actividad',
            'puntuacion',
            'comentario',
            'creado_en',
        ]
        read_only_fields = ['evaluador', 'creado_en']


# ==========================================
# 6. MODERACIÓN Y SEGURIDAD (RF-20, RF-21)
# ==========================================
class ReporteSerializer(serializers.ModelSerializer):
    reportante_nombre = serializers.ReadOnlyField(source='reportante.username')
    reportado_nombre = serializers.ReadOnlyField(source='reportado.username')

    class Meta:
        model = Reporte
        fields = [
            'id',
            'reportante',
            'reportante_nombre',
            'reportado',
            'reportado_nombre',
            'actividad',
            'motivo',
            'descripcion',
            'estado',
            'creado_en',
        ]
        read_only_fields = ['reportante', 'estado', 'creado_en']


class BloqueoSerializer(serializers.ModelSerializer):
    bloqueador_nombre = serializers.ReadOnlyField(source='bloqueador.username')
    bloqueado_nombre = serializers.ReadOnlyField(source='bloqueado.username')

    class Meta:
        model = Bloqueo
        fields = ['id', 'bloqueador', 'bloqueador_nombre', 'bloqueado', 'bloqueado_nombre', 'creado_en']
        read_only_fields = ['bloqueador', 'creado_en']


# ==========================================
# 7. PASARELA FLOW Y SUSCRIPCIONES (RF-22, RF-23, RF-24)
# ==========================================
class SuscripcionSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.ReadOnlyField(source='usuario.username')
    estado_display = serializers.ReadOnlyField(source='get_estado_display')

    class Meta:
        model = Suscripcion
        fields = [
            'id',
            'usuario',
            'usuario_nombre',
            'flow_token',
            'flow_customer_id',
            'flow_subscription_id',
            'estado',
            'estado_display',
            'fecha_inicio',
            'fecha_vencimiento',
            'creado_en',
            'actualizado_en',
        ]
        read_only_fields = (
            'id',
            'usuario',
            'flow_token',
            'flow_customer_id',
            'flow_subscription_id',
            'creado_en',
            'actualizado_en',
        )


# ==========================================
# 8. AUDITORÍA DEL SISTEMA (RF-25)
# ==========================================
class RegistroActividadSerializer(serializers.ModelSerializer):
    usuario_username = serializers.ReadOnlyField(source='usuario.username')
    modulo_display = serializers.CharField(source='get_modulo_display', read_only=True)
    nivel_display = serializers.CharField(source='get_nivel_display', read_only=True)

    class Meta:
        model = RegistroActividad
        fields = [
            'id',
            'usuario',
            'usuario_username',
            'modulo',
            'modulo_display',
            'nivel',
            'nivel_display',
            'accion',
            'descripcion',
            'ip_origen',
            'fecha_hora',
        ]
        read_only_fields = [
            'id',
            'usuario',
            'modulo',
            'nivel',
            'accion',
            'descripcion',
            'ip_origen',
            'fecha_hora',
        ]