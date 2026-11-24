from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (
    Usuario,
    Actividad,
    Inscripcion,
    Amistad,
    Mensaje,
    ValoracionJugador,
    ResenaActividad,
    Reporte,
    Suscripcion,
)


@admin.register(Usuario)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Datos Deportivos (MatchDeportivo)', {
            'fields': (
                'telefono',
                'comuna',
                'deporte_favorito',
                'nivel_habilidad',
                'biografia',
                'foto_perfil',
                'es_premium',
                'reputacion_promedio',
            )
        }),
    )
    list_display = (
        'username',
        'email',
        'comuna',
        'deporte_favorito',
        'nivel_habilidad',
        'es_premium',
        'reputacion_promedio',
        'is_active',
    )
    list_filter = ('es_premium', 'nivel_habilidad', 'comuna', 'is_active')
    search_fields = ('username', 'email', 'comuna')


class InscripcionInline(admin.TabularInline):
    model = Inscripcion
    extra = 0
    fields = ('usuario', 'estado', 'fecha_inscripcion')
    readonly_fields = ('fecha_inscripcion',)


@admin.register(Actividad)
class ActividadAdmin(admin.ModelAdmin):
    list_display = (
        'titulo',
        'deporte',
        'organizador',
        'comuna',
        'fecha_hora',
        'cupos_totales',
        'cupos_disponibles',
        'estado',
    )
    list_filter = ('deporte', 'estado', 'nivel_esperado', 'comuna')
    search_fields = ('titulo', 'direccion', 'comuna', 'organizador__username')
    inlines = [InscripcionInline]


@admin.register(Inscripcion)
class InscripcionAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'actividad', 'estado', 'fecha_inscripcion')
    list_filter = ('estado', 'fecha_inscripcion')
    search_fields = ('usuario__username', 'actividad__titulo')


@admin.register(Amistad)
class AmistadAdmin(admin.ModelAdmin):
    list_display = ('solicitante', 'receptor', 'estado', 'creado_en')
    list_filter = ('estado', 'creado_en')
    search_fields = ('solicitante__username', 'receptor__username')


@admin.register(Mensaje)
class MensajeAdmin(admin.ModelAdmin):
    list_display = ('actividad', 'usuario', 'contenido_corto', 'fecha_envio')
    list_filter = ('fecha_envio',)
    search_fields = ('usuario__username', 'actividad__titulo', 'contenido')

    def contenido_corto(self, obj):
        return obj.contenido[:40]
    contenido_corto.short_description = 'Contenido'


@admin.register(ValoracionJugador)
class ValoracionJugadorAdmin(admin.ModelAdmin):
    list_display = ('evaluador', 'evaluado', 'actividad', 'puntuacion', 'creado_en')
    list_filter = ('puntuacion', 'creado_en')
    search_fields = ('evaluador__username', 'evaluado__username', 'actividad__titulo')


@admin.register(ResenaActividad)
class ResenaActividadAdmin(admin.ModelAdmin):
    list_display = ('actividad', 'usuario', 'puntuacion', 'creado_en')
    list_filter = ('puntuacion', 'creado_en')
    search_fields = ('actividad__titulo', 'usuario__username')


@admin.register(Reporte)
class ReporteAdmin(admin.ModelAdmin):
    list_display = ('reportante', 'reportado', 'motivo', 'estado', 'creado_en')
    list_filter = ('estado', 'motivo', 'creado_en')
    search_fields = ('reportante__username', 'reportado__username', 'descripcion')


@admin.register(Suscripcion)
class SuscripcionAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'usuario',
        'estado',
        'flow_token',
        'flow_customer_id',
        'flow_subscription_id',
        'fecha_inicio',
        'fecha_vencimiento',
        'creado_en',
    )
    list_filter = ('estado', 'fecha_inicio', 'fecha_vencimiento', 'creado_en')
    search_fields = (
        'usuario__username',
        'usuario__email',
        'flow_token',
        'flow_customer_id',
        'flow_subscription_id',
    )
    readonly_fields = (
        'flow_token',
        'flow_customer_id',
        'flow_subscription_id',
        'creado_en',
        'actualizado_en',
    )

    fieldsets = (
        ('Información de Usuario', {
            'fields': ('usuario', 'estado')
        }),
        ('Identificadores Flow PSP (PCI-DSS)', {
            'description': 'Tokens técnicos generados por Flow para seguimiento de cobros y conciliación.',
            'fields': ('flow_token', 'flow_customer_id', 'flow_subscription_id'),
        }),
        ('Vigencia de Membresía', {
            'fields': ('fecha_inicio', 'fecha_vencimiento')
        }),
        ('Marcas de Tiempo', {
            'classes': ('collapse',),
            'fields': ('creado_en', 'actualizado_en')
        }),
    )

from django.contrib import admin
from .models import RegistroActividad

@admin.register(RegistroActividad)
class RegistroActividadAdmin(admin.ModelAdmin):
    list_display = ('fecha_hora', 'nivel_coloreado', 'modulo', 'usuario', 'accion', 'ip_origen')
    list_filter = ('nivel', 'modulo', 'fecha_hora')
    search_fields = ('accion', 'descripcion', 'usuario__username', 'ip_origen')
    date_hierarchy = 'fecha_hora'
    readonly_fields = [f.name for f in RegistroActividad._meta.fields]

    def has_add_permission(self, request):
        return False  # Los logs se generan automáticamente, no manualmente

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser  # Solo superadmin puede limpiar logs

    @admin.display(description='Nivel')
    def nivel_coloreado(self, obj):
        from django.utils.html import format_html
        colores = {
            'INFO': '#2196F3',
            'SUCCESS': '#4CAF50',
            'WARNING': '#FF9800',
            'ERROR': '#F44336',
        }
        color = colores.get(obj.nivel, '#666')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 11px;">{}</span>',
            color,
            obj.nivel
        )