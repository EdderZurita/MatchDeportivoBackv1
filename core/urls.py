from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    RegistroUsuarioView,
    UsuarioViewSet,
    SolicitarRecuperacionView,
    RestablecerPasswordView,
    ActividadViewSet,
    InscripcionViewSet,
    AmistadViewSet,
    MensajeViewSet,
    ValoracionJugadorViewSet,
    ResenaActividadViewSet,
    ReporteViewSet,
    SuscripcionViewSet,
    CalificacionViewSet, 
    BloqueoViewSet,
    IniciarSuscripcionView,
    RetornoRegistroTarjetaView,
    EstadoSuscripcionView,
    CancelarSuscripcionView,
    AdminLogsListView
)

# Enrutador automático de Django REST Framework
router = DefaultRouter()
router.register(r'usuarios', UsuarioViewSet, basename='usuario')
router.register(r'actividades', ActividadViewSet, basename='actividad')
router.register(r'inscripciones', InscripcionViewSet, basename='inscripcion')
router.register(r'amistades', AmistadViewSet, basename='amistad')
router.register(r'mensajes', MensajeViewSet, basename='mensaje')
router.register(r'valoraciones', ValoracionJugadorViewSet, basename='valoracion')
router.register(r'resenas', ResenaActividadViewSet, basename='resena')
router.register(r'reportes', ReporteViewSet, basename='reporte')
router.register(r'suscripciones', SuscripcionViewSet, basename='suscripcion')
router.register(r'calificaciones', CalificacionViewSet, basename='calificacion')
router.register(r'bloqueos', BloqueoViewSet, basename='bloqueo')

urlpatterns = [
    # Rutas del CRUD y acciones personalizadas
    path('', include(router.urls)),
    
    # Endpoint público de registro de nuevos usuarios (RF-1)
    path('auth/registro/', RegistroUsuarioView.as_view(), name='registro_usuario'),

    # Recuperación de contraseña mediante OTP al correo
    path('auth/solicitar-recuperacion/', SolicitarRecuperacionView.as_view(), name='solicitar-recuperacion'),
    path('auth/restablecer-password/', RestablecerPasswordView.as_view(), name='restablecer-password'),

    # Pasarela de pagos Flow y Suscripciones Premium (RF-22, RF-23, RF-24)
    path('pagos/iniciar-suscripcion/', IniciarSuscripcionView.as_view(), name='iniciar-suscripcion'),
    path('pagos/retorno-tarjeta/', RetornoRegistroTarjetaView.as_view(), name='retorno-tarjeta'),
    path('pagos/mi-suscripcion/', EstadoSuscripcionView.as_view(), name='mi-suscripcion'),
    path('pagos/cancelar-suscripcion/', CancelarSuscripcionView.as_view(), name='cancelar-suscripcion'),
    path('admin/logs/', AdminLogsListView.as_view(), name='admin-logs'),
]