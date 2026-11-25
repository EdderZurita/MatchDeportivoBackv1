import json
import os
from django.core.cache import cache
from rest_framework.throttling import ScopedRateThrottle
import re
import time
import unicodedata
import urllib.request
from datetime import timedelta
import unicodedata

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Avg, Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import exceptions, filters, generics, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .firebase_service import enviar_notificacion_push
from .flow_service import FlowService
from .models import (
    Actividad,
    Amistad,
    Bloqueo,
    Calificacion,
    CodigoRecuperacion,
    DispositivoFCM,
    Inscripcion,
    Mensaje,
    RegistroActividad,
    Reporte,
    ResenaActividad,
    Suscripcion,
    Usuario,
    ValoracionJugador,
)
from .serializers import (
    ActividadSerializer,
    AmistadSerializer,
    BloqueoSerializer,
    CalificacionSerializer,
    InscripcionSerializer,
    MensajeSerializer,
    RegistroActividadSerializer,
    RegistroUsuarioSerializer,
    ReporteSerializer,
    ResenaActividadSerializer,
    SuscripcionSerializer,
    UsuarioSerializer,
    ValoracionJugadorSerializer,
)
from .utils import registrar_log


# ==========================================
# 1. AUTENTICACIÓN Y USUARIOS (RF-1, RF-3, RF-5, RF-26)
# ==========================================
class RegistroUsuarioView(generics.CreateAPIView):
    """
    Endpoint público para el registro de nuevos usuarios (RF-1).
    """
    queryset = Usuario.objects.all()
    serializer_class = RegistroUsuarioSerializer
    permission_classes = [permissions.AllowAny]

    def perform_create(self, serializer):
        usuario = serializer.save()
        registrar_log(
            accion='USUARIO_REGISTRADO',
            descripcion=f'Nueva cuenta creada para {usuario.username} ({usuario.email}).',
            modulo='USUARIOS',
            nivel='SUCCESS',
            usuario=usuario,
            request=self.request
        )


class UsuarioViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Búsqueda de usuarios (RF-5) y gestión del perfil deportivo propio (RF-3).
    """
    queryset = Usuario.objects.all()
    serializer_class = UsuarioSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['username', 'first_name', 'last_name', 'comuna', 'deporte_favorito']

    @action(detail=False, methods=['get', 'patch', 'put'], permission_classes=[permissions.IsAuthenticated])
    def mi_perfil(self, request):
        usuario = request.user
        if request.method == 'GET':
            serializer = self.get_serializer(usuario)
            return Response(serializer.data)

        serializer = self.get_serializer(usuario, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        registrar_log(
            accion='PERFIL_ACTUALIZADO',
            descripcion=f'El usuario {usuario.username} actualizó sus datos de perfil deportivo.',
            modulo='USUARIOS',
            nivel='INFO',
            usuario=usuario,
            request=request
        )
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='registrar-fcm', permission_classes=[permissions.IsAuthenticated])
    def registrar_fcm(self, request):
        token = request.data.get('token')
        if not token:
            return Response({'detail': 'Token FCM requerido.'}, status=status.HTTP_400_BAD_REQUEST)

        DispositivoFCM.objects.update_or_create(
            token=token,
            defaults={'usuario': request.user}
        )
        return Response({'detail': 'Token registrado exitosamente.'}, status=status.HTTP_200_OK)

    # ==========================================
    # RF-7: HISTORIAL DE PARTICIPACIÓN DEPORTIVA
    # ==========================================
    @action(detail=True, methods=['get'])
    def historial(self, request, pk=None):
        """RF-7: Retorna las actividades donde el usuario participó o fue organizador."""
        usuario = self.get_object()

        # 1. Partidos organizados por el usuario
        organizados = Actividad.objects.filter(organizador=usuario).order_by('-fecha_hora')

        # 2. Partidos donde se inscribió y confirmó asistencia
        inscripciones = Inscripcion.objects.filter(
            usuario=usuario,
            estado='CONFIRMADA'
        ).select_related('actividad').order_by('-actividad__fecha_hora')

        historial = []
        ids_vistos = set()

        for act in organizados:
            ids_vistos.add(act.id)
            historial.append({
                'id': act.id,
                'titulo': act.titulo,
                'deporte': act.get_deporte_display() if hasattr(act, 'get_deporte_display') else act.deporte,
                'fecha_hora': act.fecha_hora.isoformat(),
                'comuna': act.comuna,
                'direccion': act.direccion,
                'rol': 'ORGANIZADOR',
                'estado': act.estado,
            })

        for insc in inscripciones:
            act = insc.actividad
            if act.id not in ids_vistos:
                ids_vistos.add(act.id)
                historial.append({
                    'id': act.id,
                    'titulo': act.titulo,
                    'deporte': act.get_deporte_display() if hasattr(act, 'get_deporte_display') else act.deporte,
                    'fecha_hora': act.fecha_hora.isoformat(),
                    'comuna': act.comuna,
                    'direccion': act.direccion,
                    'rol': 'PARTICIPANTE',
                    'estado': act.estado,
                })

        historial.sort(key=lambda x: x['fecha_hora'], reverse=True)
        return Response(historial)

    @action(detail=True, methods=['post'], url_path='cambiar-estado-cuenta', permission_classes=[permissions.IsAdminUser])
    def cambiar_estado_cuenta(self, request, pk=None):
        """RF-26: Permite a un administrador suspender o reactivar una cuenta."""
        usuario = self.get_object()

        if usuario == request.user:
            return Response(
                {'detail': 'No puedes suspender tu propia cuenta de administrador.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if usuario.is_superuser:
            return Response(
                {'detail': 'No está permitido suspender a un superusuario del sistema.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        motivo = request.data.get('motivo', '').strip()
        if not motivo:
            motivo = 'Decisión administrativa / Infracción a normas comunitarias.'

        usuario.is_active = not usuario.is_active
        usuario.save()

        nuevo_estado_str = "ACTIVA" if usuario.is_active else "SUSPENDIDA"
        accion_log = "USUARIO_REACTIVADO" if usuario.is_active else "USUARIO_SUSPENDIDO"
        nivel_log = "INFO" if usuario.is_active else "WARNING"

        registrar_log(
            accion=accion_log,
            descripcion=(
                f"El administrador {request.user.username} cambió el estado de {usuario.username} "
                f"a {nuevo_estado_str}. Motivo: '{motivo}'."
            ),
            modulo='SEGURIDAD',
            nivel=nivel_log,
            usuario=request.user,
            request=request
        )

        return Response({
            'detail': f'Cuenta de {usuario.username} {nuevo_estado_str.lower()} exitosamente.',
            'usuario_id': usuario.id,
            'username': usuario.username,
            'is_active': usuario.is_active,
        }, status=status.HTTP_200_OK)


# ==========================================
# 2. ACTIVIDADES Y PARTIDOS (RF-8 a RF-15, RF-17, RF-22)
# ==========================================

import unicodedata

def _normalizar_texto(texto):
    """Convierte a minúsculas, quita tildes y homologa basquet/basket."""
    if not texto:
        return ''
    texto = str(texto).lower().strip()
    # Elimina tildes (Básquetbol -> Basquetbol)
    texto = ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')
    # Homologa ortografía (basketbol -> basquetbol)
    return texto.replace('basket', 'basquet')


def _calcular_afinidad_partido(act, deporte_user, comuna_user, val_nivel_user, escala_nivel):
    # 1. Deporte (35 pts) - Cruza 'Básquetbol' con 'BASKETBOL'
    dep_act = _normalizar_texto(act.deporte)
    dep_usr = _normalizar_texto(deporte_user)

    if dep_usr and dep_act:
        score_deporte = 35 if (dep_act in dep_usr or dep_usr in dep_act) else 5
    else:
        score_deporte = 20

    # 2. Comuna (25 pts) - Cruza 'La Pintana' con 'La Pintana'
    com_act = _normalizar_texto(act.comuna)
    com_usr = _normalizar_texto(comuna_user)

    if com_usr and com_act:
        score_ubicacion = 25 if com_usr == com_act else 10
    else:
        score_ubicacion = 15

    # 3. Nivel (25 pts) - Lee 'nivel_esperado' de core_actividad
    nivel_act_raw = getattr(act, 'nivel_esperado', 'INTERMEDIO') or 'INTERMEDIO'
    val_nivel_act = escala_nivel.get(str(nivel_act_raw).strip().upper(), 2)
    diferencia = abs(val_nivel_user - val_nivel_act)
    score_nivel = 25 if diferencia == 0 else (15 if diferencia == 1 else 5)

    # 4. Reputación Organizador (15 pts)
    rep_org = getattr(act.organizador, 'reputacion_promedio', 5.0) or 5.0
    score_reputacion = (float(rep_org) / 5.0) * 15

    score_total = int(round(score_deporte + score_ubicacion + score_nivel + score_reputacion))
    return max(10, min(score_total, 100))


class ActividadViewSet(viewsets.ModelViewSet):
    """
    Gestión de partidos, filtros de búsqueda, coordenadas para el mapa,
    inscripciones, límite de convocatorias según membresía, motor de matchmaking y notificaciones push.
    """
    serializer_class = ActividadSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    filter_backends = [filters.SearchFilter]
    search_fields = ['titulo', 'descripcion', 'direccion', 'comuna']

    def get_queryset(self):
        # Cierre automático de actividades cuya fecha/hora ya transcurrió
        Actividad.objects.filter(
            fecha_hora__lte=timezone.now(),
            estado__in=['ABIERTA', 'LLENA']
        ).update(estado='FINALIZADA')

        queryset = Actividad.objects.all().select_related('organizador')
        deporte = self.request.query_params.get('deporte')
        comuna = self.request.query_params.get('comuna')
        estado = self.request.query_params.get('estado')
        organizador = self.request.query_params.get('organizador')

        if deporte:
            queryset = queryset.filter(deporte=deporte)
        if comuna:
            queryset = queryset.filter(comuna__icontains=comuna)
        if estado:
            queryset = queryset.filter(estado=estado)

        if organizador is not None:
            organizador_limpio = organizador.strip()
            if not organizador_limpio:
                return queryset.none()
            if organizador_limpio.isdigit():
                queryset = queryset.filter(organizador_id=int(organizador_limpio))
            else:
                queryset = queryset.filter(organizador__username__iexact=organizador_limpio)

        return queryset

    def perform_create(self, serializer):
        """
        RF-8 / RF-22: Creación de partidos con validación de membresía y auditoría.
        """
        usuario = self.request.user

        if not getattr(usuario, 'es_premium', False):
            hoy = timezone.now().date()
            actividades_hoy = Actividad.objects.filter(
                organizador=usuario,
                creado_en__date=hoy
            ).exclude(estado='CANCELADA').count()

            if actividades_hoy >= 1:
                registrar_log(
                    accion='LIMITE_DIARIO_BLOQUEADO',
                    descripcion=f'El usuario {usuario.username} intentó publicar un segundo partido sin suscripción Premium.',
                    modulo='ACTIVIDADES',
                    nivel='WARNING',
                    usuario=usuario,
                    request=self.request
                )
                raise exceptions.PermissionDenied(
                    'Has alcanzado el límite diario de 1 partido para el Plan Gratuito. '
                    'Pásate a MatchDeportivo Premium para organizar actividades ilimitadas.'
                )

        actividad = serializer.save(organizador=usuario)

        registrar_log(
            accion='PARTIDO_CREADO',
            descripcion=f'{usuario.username} publicó el partido "{actividad.titulo}" ({actividad.deporte}) en {actividad.comuna}.',
            modulo='ACTIVIDADES',
            nivel='SUCCESS',
            usuario=usuario,
            request=self.request
        )

    def perform_update(self, serializer):
        """
        Bloquea la edición si no es el creador o si la actividad ya ocurrió.
        RF-13 / RF-17: Notifica vía push a todos los inscritos ante cualquier modificación.
        """
        actividad_anterior = self.get_object()

        if actividad_anterior.organizador != self.request.user and not self.request.user.is_staff:
            raise exceptions.PermissionDenied('Solo el organizador puede editar esta actividad.')

        if actividad_anterior.fecha_hora <= timezone.now() or actividad_anterior.estado == 'FINALIZADA':
            raise serializers.ValidationError(
                {'detail': 'Esta actividad ya finalizó y forma parte del histórico; no se puede editar.'}
            )

        # Capturar valores previos para detectar qué modificó el organizador
        titulo_anterior = actividad_anterior.titulo
        fecha_antigua = actividad_anterior.fecha_hora
        direccion_anterior = actividad_anterior.direccion
        comuna_anterior = actividad_anterior.comuna
        cupos_anteriores = actividad_anterior.cupos_totales
        nivel_anterior = actividad_anterior.nivel_esperado
        descripcion_anterior = actividad_anterior.descripcion

        actividad_guardada = serializer.save()

        # Identificar cambios realizados
        cambios = []
        if fecha_antigua != actividad_guardada.fecha_hora:
            fecha_local = timezone.localtime(actividad_guardada.fecha_hora)
            cambios.append(f"horario ({fecha_local.strftime('%d/%m a las %H:%M')} hrs)")

        if direccion_anterior != actividad_guardada.direccion or comuna_anterior != actividad_guardada.comuna:
            cambios.append(f"ubicación ({actividad_guardada.direccion}, {actividad_guardada.comuna})")

        if titulo_anterior != actividad_guardada.titulo:
            cambios.append("título")

        if (
            cupos_anteriores != actividad_guardada.cupos_totales
            or nivel_anterior != actividad_guardada.nivel_esperado
            or descripcion_anterior != actividad_guardada.descripcion
        ):
            cambios.append("detalles del encuentro")

        # Enviar notificación push a inscritos confirmados y pendientes
        if cambios:
            resumen_cambios = ", ".join(cambios)
            inscritos = Inscripcion.objects.filter(
                actividad=actividad_guardada,
                estado__in=['CONFIRMADA', 'PENDIENTE']
            ).exclude(usuario=self.request.user).select_related('usuario')

            for insc in inscritos:
                enviar_notificacion_push(
                    usuario=insc.usuario,
                    titulo=f"Partido actualizado: {actividad_guardada.titulo}",
                    cuerpo=f"El organizador modificó: {resumen_cambios}.",
                    data={
                        'actividad_id': str(actividad_guardada.id),
                        'tipo': 'ACTIVIDAD_EDITADA'
                    }
                )

            registrar_log(
                accion='PARTIDO_EDITADO',
                descripcion=(
                    f'El organizador {self.request.user.username} editó el partido '
                    f'"{actividad_guardada.titulo}" (cambios: {resumen_cambios}).'
                ),
                modulo='ACTIVIDADES',
                nivel='INFO',
                usuario=self.request.user,
                request=self.request
            )

    def perform_destroy(self, instance):
        """RF-13 / RF-17: Cancelación lógica por parte del organizador con notificación push y auditoría."""
        if instance.organizador != self.request.user and not self.request.user.is_staff:
            registrar_log(
                accion='CANCELACION_NO_AUTORIZADA',
                descripcion=f'{self.request.user.username} intentó cancelar la actividad #{instance.id} de otro organizador.',
                modulo='SEGURIDAD',
                nivel='ERROR',
                usuario=self.request.user,
                request=self.request
            )
            raise exceptions.PermissionDenied('Solo el organizador puede cancelar o eliminar la actividad.')

        if instance.fecha_hora <= timezone.now() or instance.estado == 'FINALIZADA':
            raise serializers.ValidationError(
                {'detail': 'No se puede cancelar una actividad que ya finalizó.'}
            )

        instance.estado = 'CANCELADA'
        instance.save()

        inscritos = Inscripcion.objects.filter(
            actividad=instance,
            estado__in=['CONFIRMADA', 'PENDIENTE']
        ).exclude(usuario=self.request.user).select_related('usuario')

        for insc in inscritos:
            enviar_notificacion_push(
                usuario=insc.usuario,
                titulo=f"Partido cancelado: {instance.titulo}",
                cuerpo="El organizador ha cancelado esta convocatoria deportiva.",
                data={
                    'actividad_id': str(instance.id),
                    'tipo': 'ACTIVIDAD_CANCELADA'
                }
            )

        registrar_log(
            accion='PARTIDO_CANCELADO',
            descripcion=f'{self.request.user.username} canceló la convocatoria "{instance.titulo}" (ID #{instance.id}).',
            modulo='ACTIVIDADES',
            nivel='INFO',
            usuario=self.request.user,
            request=self.request
        )

    # ==========================================
    # MOTOR DE MATCHMAKING (AFINIDAD DEPORTIVA)
    # ==========================================
    @action(detail=False, methods=['get'], url_path='recomendadas', permission_classes=[permissions.IsAuthenticated])
    def recomendadas(self, request):
        usuario = request.user
        ahora = timezone.now()
        if not getattr(usuario, 'es_premium', False):
            return Response(
                {
                    'detail': 'El algoritmo de Matchmaking por afinidad es exclusivo para miembros Premium.',
                    'codigo': 403
                },
                status=status.HTTP_403_FORBIDDEN
            )

        ahora = timezone.now()
        # 1. Filtro en BD sin 'cupos_disponibles' (solo campos reales)
        candidatas = Actividad.objects.filter(
            estado='ABIERTA',
            fecha_hora__gt=ahora,
        ).exclude(organizador=usuario).select_related('organizador')

        # Excluir partidos donde ya está inscrito o en espera
        inscrito_ids = Inscripcion.objects.filter(
            usuario=usuario,
            estado__in=['CONFIRMADA', 'PENDIENTE', 'EXPULSADA']
        ).values_list('actividad_id', flat=True)
        candidatas = candidatas.exclude(id__in=inscrito_ids)

        # Excluir usuarios bloqueados
        bloqueos = set(Bloqueo.objects.filter(bloqueador=usuario).values_list('bloqueado_id', flat=True))
        bloqueos.update(Bloqueo.objects.filter(bloqueado=usuario).values_list('bloqueador_id', flat=True))
        if bloqueos:
            candidatas = candidatas.exclude(organizador_id__in=bloqueos)

        # 2. Filtrar cupos disponibles en Python (usando la property del modelo)
        candidatas_con_cupo = [act for act in candidatas if getattr(act, 'cupos_disponibles', 1) > 0]

        # 3. Datos del usuario
        deporte_user = usuario.deporte_favorito
        comuna_user = usuario.comuna
        nivel_user = usuario.nivel_habilidad

        escala_nivel = {'PRINCIPIANTE': 1, 'INTERMEDIO': 2, 'AVANZADO': 3}
        val_nivel_user = escala_nivel.get(str(nivel_user).strip().upper(), 2)

        # 4. Cálculo de afinidad
        partidos_puntuados = [
            {
                'actividad': act,
                'afinidad': _calcular_afinidad_partido(act, deporte_user, comuna_user, val_nivel_user, escala_nivel)
            }
            for act in candidatas_con_cupo
        ]

        partidos_puntuados.sort(key=lambda x: x['afinidad'], reverse=True)

        resultados = []
        for item in partidos_puntuados[:25]:
            data = self.get_serializer(item['actividad']).data
            data['porcentaje_match'] = item['afinidad']
            resultados.append(data)

        return Response(resultados, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='mis-actividades', permission_classes=[permissions.IsAuthenticated])
    def mis_actividades(self, request):
        propias = Actividad.objects.filter(organizador=request.user).order_by('-fecha_hora')
        serializer = self.get_serializer(propias, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='mis-inscripciones', permission_classes=[permissions.IsAuthenticated])
    def mis_inscripciones(self, request):
        inscripciones = Inscripcion.objects.filter(
            usuario=request.user,
            estado__in=['CONFIRMADA', 'PENDIENTE']
        ).select_related('actividad', 'actividad__organizador').order_by('-actividad__fecha_hora')

        data = []
        for insc in inscripciones:
            item = ActividadSerializer(insc.actividad).data
            item['mi_estado_inscripcion'] = insc.estado
            data.append(item)

        return Response(data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def inscribirse(self, request, pk=None):
        try:
            with transaction.atomic():
                actividad = Actividad.objects.select_for_update().get(pk=pk)

                if actividad.estado != 'ABIERTA':
                    return Response(
                        {'detail': f'La actividad no está abierta a postulaciones (Estado: {actividad.estado}).'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                if actividad.organizador == request.user:
                    return Response(
                        {'detail': 'Eres el organizador de este partido.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                if actividad.cupos_disponibles <= 0:
                    return Response(
                        {'detail': 'No quedan cupos disponibles en este partido.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                inscripcion_existente = Inscripcion.objects.filter(
                    actividad=actividad,
                    usuario=request.user
                ).first()

                if inscripcion_existente:
                    if inscripcion_existente.estado == 'EXPULSADA':
                        return Response(
                            {'detail': 'Fuiste retirado de esta convocatoria y no puedes volver a solicitar cupo.'},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    if inscripcion_existente.estado == 'CONFIRMADA':
                        return Response(
                            {'detail': 'Ya estás confirmado en este partido.'},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    if inscripcion_existente.estado == 'PENDIENTE':
                        return Response(
                            {'detail': 'Ya tienes una solicitud pendiente de aprobación para este partido.'},
                            status=status.HTTP_400_BAD_REQUEST
                        )

                    inscripcion_existente.estado = 'PENDIENTE'
                    inscripcion_existente.save()
                else:
                    Inscripcion.objects.create(
                        actividad=actividad,
                        usuario=request.user,
                        estado='PENDIENTE'
                    )

                registrar_log(
                    accion='SOLICITUD_INSCRIPCION',
                    descripcion=f'{request.user.username} solicitó unirse al partido "{actividad.titulo}".',
                    modulo='ACTIVIDADES',
                    nivel='INFO',
                    usuario=request.user,
                    request=request
                )

                return Response(
                    {
                        'detail': 'Solicitud enviada con éxito. Esperando aprobación del organizador.',
                        'estado': 'PENDIENTE'
                    },
                    status=status.HTTP_201_CREATED
                )
        except Actividad.DoesNotExist:
            return Response({'detail': 'Partido no encontrado.'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def abandonar(self, request, pk=None):
        try:
            with transaction.atomic():
                actividad = Actividad.objects.select_for_update().get(pk=pk)
                inscripcion = Inscripcion.objects.filter(
                    actividad=actividad,
                    usuario=request.user
                ).first()

                if not inscripcion or inscripcion.estado not in ['CONFIRMADA', 'PENDIENTE']:
                    return Response(
                        {'detail': 'No tienes una inscripción o solicitud activa en este partido.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                estaba_confirmada = (inscripcion.estado == 'CONFIRMADA')
                inscripcion.estado = 'CANCELADA'
                inscripcion.save()

                if estaba_confirmada and actividad.estado == 'LLENA':
                    actividad.estado = 'ABIERTA'
                    actividad.save()

                registrar_log(
                    accion='PARTICIPANTE_ABANDONO',
                    descripcion=f'{request.user.username} canceló su inscripción en "{actividad.titulo}".',
                    modulo='ACTIVIDADES',
                    nivel='INFO',
                    usuario=request.user,
                    request=request
                )

                return Response(
                    {'detail': 'Has cancelado tu participación en este partido.'},
                    status=status.HTTP_200_OK
                )
        except Actividad.DoesNotExist:
            return Response({'detail': 'Partido no encontrado.'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def gestionar_participante(self, request, pk=None):
        try:
            with transaction.atomic():
                actividad = Actividad.objects.select_for_update().get(pk=pk)

                if actividad.organizador != request.user and not request.user.is_staff:
                    return Response(
                        {'detail': 'Solo el organizador puede gestionar participantes.'},
                        status=status.HTTP_403_FORBIDDEN
                    )

                usuario_id = request.data.get('usuario_id')
                nuevo_estado = request.data.get('estado')

                if not usuario_id or not nuevo_estado:
                    return Response(
                        {'detail': 'Debe proveer usuario_id y estado.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                estados_validos = ['CONFIRMADA', 'RECHAZADA', 'EXPULSADA', 'CANCELADA']
                if nuevo_estado not in estados_validos:
                    return Response(
                        {'detail': f'Estado {nuevo_estado} no es válido. Opciones: {estados_validos}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                inscripcion = Inscripcion.objects.filter(
                    actividad=actividad,
                    usuario_id=usuario_id
                ).select_related('usuario').first()

                if not inscripcion:
                    return Response(
                        {'detail': 'Inscripción no encontrada.'},
                        status=status.HTTP_404_NOT_FOUND
                    )

                if nuevo_estado == 'CONFIRMADA' and inscripcion.estado != 'CONFIRMADA':
                    if actividad.cupos_disponibles <= 0:
                        return Response(
                            {'detail': 'No quedan cupos libres para confirmar a este jugador.'},
                            status=status.HTTP_400_BAD_REQUEST
                        )

                inscripcion.estado = nuevo_estado
                inscripcion.save()

                if actividad.cupos_disponibles <= 0:
                    actividad.estado = 'LLENA'
                    actividad.save()
                elif actividad.estado == 'LLENA' and actividad.cupos_disponibles > 0:
                    actividad.estado = 'ABIERTA'
                    actividad.save()

                if nuevo_estado == 'CONFIRMADA':
                    enviar_notificacion_push(
                        usuario=inscripcion.usuario,
                        titulo="¡Solicitud Aceptada!",
                        cuerpo=f"Fuiste confirmado en el partido '{actividad.titulo}'.",
                        data={'actividad_id': str(actividad.id), 'tipo': 'SOLICITUD_ACEPTADA'}
                    )
                elif nuevo_estado == 'RECHAZADA':
                    enviar_notificacion_push(
                        usuario=inscripcion.usuario,
                        titulo="Solicitud no admitida",
                        cuerpo=f"Tu solicitud para el partido '{actividad.titulo}' no fue aceptada.",
                        data={'actividad_id': str(actividad.id), 'tipo': 'SOLICITUD_RECHAZADA'}
                    )
                elif nuevo_estado == 'EXPULSADA':
                    enviar_notificacion_push(
                        usuario=inscripcion.usuario,
                        titulo="Convocatoria actualizada",
                        cuerpo=f"Has sido retirado del partido '{actividad.titulo}'.",
                        data={'actividad_id': str(actividad.id), 'tipo': 'EXPULSION'}
                    )

                registrar_log(
                    accion=f'PARTICIPANTE_{nuevo_estado}',
                    descripcion=f'{request.user.username} cambió el estado de {inscripcion.usuario.username} a {nuevo_estado} en "{actividad.titulo}".',
                    modulo='ACTIVIDADES',
                    nivel='INFO',
                    usuario=request.user,
                    request=request
                )

                mensajes = {
                    'CONFIRMADA': 'Participante aceptado con éxito en el partido.',
                    'RECHAZADA': 'Solicitud de inscripción rechazada.',
                    'EXPULSADA': 'Participante expulsado de la convocatoria.',
                    'CANCELADA': 'Inscripción cancelada.',
                }

                return Response(
                    {
                        'detail': mensajes.get(nuevo_estado, f'Estado actualizado a {nuevo_estado}.'),
                        'estado': nuevo_estado
                    },
                    status=status.HTTP_200_OK
                )
        except Actividad.DoesNotExist:
            return Response({'detail': 'Partido no encontrado.'}, status=status.HTTP_404_NOT_FOUND)


# ==========================================
# 3. INSCRIPCIONES
# ==========================================
class InscripcionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Consulta de las inscripciones del usuario autenticado o por actividad.
    """
    serializer_class = InscripcionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = Inscripcion.objects.all().select_related('actividad', 'usuario')
        actividad_id = self.request.query_params.get('actividad')
        if actividad_id:
            return queryset.filter(actividad_id=actividad_id)
        return queryset.filter(usuario=self.request.user)


# ==========================================
# 4. RED SOCIAL Y AMISTADES (RF-6, RF-7, RF-22)
# ==========================================
class AmistadViewSet(viewsets.ModelViewSet):
    """
    Gestión de solicitudes de contacto entre deportistas (RF-6, RF-7).
    Regla Premium: usuarios gratuitos limitados a un máximo de 5 amigos confirmados.
    """
    serializer_class = AmistadSerializer
    permission_classes = [permissions.IsAuthenticated]

    def _total_amigos(self, usuario):
        return Amistad.objects.filter(
            Q(solicitante=usuario) | Q(receptor=usuario),
            estado='ACEPTADA'
        ).count()

    def get_queryset(self):
        user = self.request.user
        return Amistad.objects.filter(Q(solicitante=user) | Q(receptor=user))

    def perform_create(self, serializer):
        user = self.request.user
        if not getattr(user, 'es_premium', False) and self._total_amigos(user) >= 5:
            registrar_log(
                accion='LIMITE_AMIGOS_BLOQUEADO',
                descripcion=f'{user.username} intentó agregar una amistad superando el tope de 5.',
                modulo='AMISTADES',
                nivel='WARNING',
                usuario=user,
                request=self.request
            )
            raise exceptions.PermissionDenied(
                'Has alcanzado el límite de 5 amigos para el Plan Gratuito. '
                'Pásate a MatchDeportivo Premium para conectar con amigos ilimitados.'
            )
        serializer.save(solicitante=user)

    @action(detail=False, methods=['get'], url_path='mis-amigos')
    def mis_amigos(self, request):
        relaciones = Amistad.objects.filter(
            Q(solicitante=request.user) | Q(receptor=request.user),
            estado='ACEPTADA'
        ).select_related('solicitante', 'receptor')

        amigos = []
        for rel in relaciones:
            otro_usuario = rel.receptor if rel.solicitante == request.user else rel.solicitante
            amigos.append({
                'id': otro_usuario.id,
                'username': otro_usuario.username,
                'email': otro_usuario.email,
                'es_premium': getattr(otro_usuario, 'es_premium', False),
            })

        return Response(amigos, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='enviar-solicitud')
    def enviar_solicitud(self, request):
        if not getattr(request.user, 'es_premium', False) and self._total_amigos(request.user) >= 5:
            registrar_log(
                accion='LIMITE_AMIGOS_BLOQUEADO',
                descripcion=f'{request.user.username} intentó enviar solicitud superando el límite de 5 amigos.',
                modulo='AMISTADES',
                nivel='WARNING',
                usuario=request.user,
                request=request
            )
            return Response(
                {
                    'detail': 'Has alcanzado el límite de 5 amigos del Plan Gratuito. '
                              'Actualiza a MatchDeportivo Premium para agregar amigos ilimitados.'
                },
                status=status.HTTP_403_FORBIDDEN
            )

        username_destino = request.data.get('username', '').strip()
        if not username_destino:
            return Response({'detail': 'Debes ingresar un nombre de usuario.'}, status=status.HTTP_400_BAD_REQUEST)

        if username_destino.lower() == request.user.username.lower():
            return Response({'detail': 'No puedes enviarte una solicitud a ti mismo.'}, status=status.HTTP_400_BAD_REQUEST)

        destinatario = Usuario.objects.filter(username__iexact=username_destino).first()
        if not destinatario:
            return Response({'detail': 'Usuario no encontrado.'}, status=status.HTTP_404_NOT_FOUND)

        relacion = Amistad.objects.filter(
            Q(solicitante=request.user, receptor=destinatario) |
            Q(solicitante=destinatario, receptor=request.user)
        ).first()

        if relacion:
            if relacion.estado == 'ACEPTADA':
                return Response({'detail': 'Ya son amigos.'}, status=status.HTTP_400_BAD_REQUEST)
            return Response({'detail': 'Ya existe una solicitud pendiente entre ustedes.'}, status=status.HTTP_400_BAD_REQUEST)

        Amistad.objects.create(solicitante=request.user, receptor=destinatario, estado='PENDIENTE')

        registrar_log(
            accion='SOLICITUD_AMISTAD_ENVIADA',
            descripcion=f'{request.user.username} envió solicitud de amistad a {destinatario.username}.',
            modulo='AMISTADES',
            nivel='INFO',
            usuario=request.user,
            request=request
        )

        return Response({'detail': f'Solicitud enviada a {destinatario.username}. Debe aceptarla.'}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'], url_path='solicitudes-recibidas')
    def solicitudes_recibidas(self, request):
        pendientes = Amistad.objects.filter(
            receptor=request.user,
            estado='PENDIENTE'
        ).select_related('solicitante')

        data = [{
            'id': p.id,
            'solicitante_id': p.solicitante.id,
            'username': p.solicitante.username,
        } for p in pendientes]

        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='responder-solicitud')
    def responder_solicitud(self, request):
        solicitud_id = request.data.get('solicitud_id')
        accion = request.data.get('accion')

        solicitud = Amistad.objects.filter(
            id=solicitud_id,
            receptor=request.user,
            estado='PENDIENTE'
        ).select_related('solicitante').first()

        if not solicitud:
            return Response({'detail': 'Solicitud no encontrada o ya procesada.'}, status=status.HTTP_404_NOT_FOUND)

        if accion == 'ACEPTAR':
            if not getattr(request.user, 'es_premium', False) and self._total_amigos(request.user) >= 5:
                registrar_log(
                    accion='LIMITE_AMIGOS_BLOQUEADO',
                    descripcion=f'{request.user.username} intentó aceptar amistad pero tiene el límite de 5 amigos lleno.',
                    modulo='AMISTADES',
                    nivel='WARNING',
                    usuario=request.user,
                    request=request
                )
                return Response(
                    {
                        'detail': 'Has alcanzado el límite de 5 amigos del Plan Gratuito. '
                                  'Pásate a MatchDeportivo Premium para aceptar más amigos.'
                    },
                    status=status.HTTP_403_FORBIDDEN
                )

            if not getattr(solicitud.solicitante, 'es_premium', False) and self._total_amigos(solicitud.solicitante) >= 5:
                return Response(
                    {
                        'detail': f'{solicitud.solicitante.username} ya ha alcanzado el límite de 5 amigos de su plan estándar.'
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

            solicitud.estado = 'ACEPTADA'
            solicitud.save()

            registrar_log(
                accion='AMISTAD_CONFIRMADA',
                descripcion=f'{request.user.username} aceptó la solicitud de amistad de {solicitud.solicitante.username}.',
                modulo='AMISTADES',
                nivel='SUCCESS',
                usuario=request.user,
                request=request
            )
            return Response({'detail': f'¡Ahora eres amigo de {solicitud.solicitante.username}!'}, status=status.HTTP_200_OK)

        elif accion == 'RECHAZAR':
            nombre_solicitante = solicitud.solicitante.username
            solicitud.delete()

            registrar_log(
                accion='SOLICITUD_AMISTAD_RECHAZADA',
                descripcion=f'{request.user.username} rechazó la solicitud de amistad de {nombre_solicitante}.',
                modulo='AMISTADES',
                nivel='INFO',
                usuario=request.user,
                request=request
            )
            return Response({'detail': 'Solicitud rechazada.'}, status=status.HTTP_200_OK)

        return Response({'detail': 'Acción inválida. Usa ACEPTAR o RECHAZAR.'}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='eliminar-amigo')
    def eliminar_amigo(self, request):
        amigo_id = request.data.get('amigo_id')
        if not amigo_id:
            return Response({'detail': 'Debes especificar el ID del usuario.'}, status=status.HTTP_400_BAD_REQUEST)

        relacion = Amistad.objects.filter(
            Q(solicitante=request.user, receptor_id=amigo_id) |
            Q(solicitante_id=amigo_id, receptor=request.user)
        ).first()

        if not relacion:
            return Response({'detail': 'No se encontró relación de amistad con este usuario.'}, status=status.HTTP_404_NOT_FOUND)

        relacion.delete()

        registrar_log(
            accion='AMIGO_ELIMINADO',
            descripcion=f'{request.user.username} eliminó al amigo ID #{amigo_id}.',
            modulo='AMISTADES',
            nivel='INFO',
            usuario=request.user,
            request=request
        )
        return Response({'detail': 'Amistad eliminada correctamente.'}, status=status.HTTP_200_OK)


# ==========================================
# 5. MENSAJERÍA Y CHAT GRUPAL (RF-16)
# ==========================================
PALABRAS_RIESGO = [
    'droga', 'marihuana', 'coca', 'cocaina', 'tussi', 'keta', 'pasta base',
    'pitos', 'cogollos', 'clona', 'clonazepam', 'jarabe', 'codeina',
    'pistola', 'fierro', 'arma', 'balas', 'municion', 'revólver',
    'transferencia trucha', 'estafa', 'dealer', 'vender puntos'
]


class MensajeViewSet(viewsets.ModelViewSet):
    serializer_class = MensajeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Mensaje.objects.all().select_related('usuario', 'receptor', 'actividad')

        actividad_id = self.request.query_params.get('actividad_id')
        amigo_id = self.request.query_params.get('amigo_id')

        if actividad_id:
            return qs.filter(actividad_id=actividad_id)

        if amigo_id:
            return qs.filter(
                (Q(usuario=user, receptor_id=amigo_id) | Q(usuario_id=amigo_id, receptor=user)),
                actividad__isnull=True
            )

        return qs.filter(Q(usuario=user) | Q(receptor=user))

    def perform_create(self, serializer):
        user = self.request.user
        contenido = serializer.validated_data.get('contenido', '')
        texto_lower = contenido.lower()

        coincidencias = []
        for palabra in PALABRAS_RIESGO:
            patron = rf"\b{re.escape(palabra)}\b"
            if re.search(patron, texto_lower):
                coincidencias.append(palabra)

        es_sospechoso = len(coincidencias) > 0
        mensaje = serializer.save(usuario=user, sospechoso=es_sospechoso)

        if es_sospechoso:
            if mensaje.actividad:
                contexto = f"Actividad #{mensaje.actividad_id}"
            elif mensaje.receptor:
                contexto = f"Privado a {mensaje.receptor.username}"
            else:
                contexto = "Mensaje Privado"

            detalle_alerta = (
                f"[SEGURIDAD] Coincidencias: {', '.join(coincidencias)} | "
                f"Contexto: {contexto} | Mensaje: '{contenido}'"
            )

            reportante_usuario = mensaje.receptor if mensaje.receptor else user

            Reporte.objects.create(
                reportante=reportante_usuario,
                reportado=user,
                actividad=mensaje.actividad,
                motivo='OTRO',
                descripcion=detalle_alerta[:500],
                estado='PENDIENTE',
            )

            registrar_log(
                accion='MENSAJE_SOSPECHOSO_DETECTADO',
                descripcion=f'Usuario {user.username} envió mensaje con términos sensibles: {", ".join(coincidencias)}.',
                modulo='SEGURIDAD',
                nivel='WARNING',
                usuario=user,
                request=self.request
            )


# ==========================================
# 6. REPUTACIÓN Y EVALUACIONES (RF-18, RF-19, RF-27)
# ==========================================
class ValoracionJugadorViewSet(viewsets.ModelViewSet):
    """
    Calificación entre participantes tras finalizar el encuentro (RF-18, RF-19).
    """
    serializer_class = ValoracionJugadorSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ValoracionJugador.objects.all().select_related('evaluador', 'evaluado', 'actividad')

    def perform_create(self, serializer):
        valoracion = serializer.save(evaluador=self.request.user)

        promedio = ValoracionJugador.objects.filter(
            evaluado=valoracion.evaluado
        ).aggregate(Avg('puntuacion'))['puntuacion__avg']

        if promedio is not None:
            valoracion.evaluado.reputacion_promedio = round(promedio, 2)
            valoracion.evaluado.save()

        registrar_log(
            accion='VALORACION_EMITIDA',
            descripcion=f'{self.request.user.username} calificó a {valoracion.evaluado.username} con {valoracion.puntuacion} estrellas.',
            modulo='USUARIOS',
            nivel='INFO',
            usuario=self.request.user,
            request=self.request
        )


class ResenaActividadViewSet(viewsets.ModelViewSet):
    """
    RF-27: Evaluación general del encuentro, recinto y organización
    tras finalizar la actividad, independiente de las notas a jugadores.
    """
    serializer_class = ResenaActividadSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        actividad_id = self.request.query_params.get('actividad')
        if actividad_id:
            return ResenaActividad.objects.filter(
                actividad_id=actividad_id
            ).select_related('usuario').order_by('-creado_en')
        return ResenaActividad.objects.all().select_related('usuario').order_by('-creado_en')

    def perform_create(self, serializer):
        usuario = self.request.user
        actividad = serializer.validated_data.get('actividad')

        # 1. Validar que la actividad ya haya concluido
        es_finalizada = actividad.fecha_hora <= timezone.now() or actividad.estado == 'FINALIZADA'
        if not es_finalizada:
            raise exceptions.ValidationError(
                {'detail': 'Solo puedes evaluar la actividad una vez que el partido haya finalizado.'}
            )

        # 2. Validar que el usuario haya participado (organizador o confirmado)
        es_organizador = (actividad.organizador == usuario)
        es_participante = Inscripcion.objects.filter(
            actividad=actividad,
            usuario=usuario,
            estado='CONFIRMADA'
        ).exists()

        if not (es_organizador or es_participante):
            raise exceptions.PermissionDenied(
                'Solo los jugadores que participaron en la convocatoria pueden dejar una reseña.'
            )

        # 3. Validar que no exista reseña previa del mismo usuario en este partido
        if ResenaActividad.objects.filter(actividad=actividad, usuario=usuario).exists():
            raise exceptions.ValidationError(
                {'detail': 'Ya has enviado una reseña para esta actividad.'}
            )

        resena = serializer.save(usuario=usuario)

        # 4. Auditoría RF-25
        puntuacion = getattr(resena, 'puntuacion', getattr(resena, 'calificacion', 5))
        registrar_log(
            accion='RESENA_ACTIVIDAD_CREADA',
            descripcion=f'{usuario.username} calificó el partido "{actividad.titulo}" con {puntuacion} estrellas.',
            modulo='ACTIVIDADES',
            nivel='INFO',
            usuario=usuario,
            request=self.request
        )

    @action(detail=False, methods=['get'], url_path=r'mi-resena/(?P<actividad_id>\d+)')
    def mi_resena(self, request, actividad_id=None):
        """Devuelve si el usuario en sesión ya emitió una opinión sobre este partido."""
        resena = ResenaActividad.objects.filter(
            actividad_id=actividad_id,
            usuario=request.user
        ).first()

        if not resena:
            return Response({'existe': False}, status=status.HTTP_200_OK)

        return Response({
            'existe': True,
            'resena': self.get_serializer(resena).data
        }, status=status.HTTP_200_OK)


class CalificacionViewSet(viewsets.ModelViewSet):
    """
    RF-18, RF-19: Valoración post-partido entre compañeros confirmados.
    """
    serializer_class = CalificacionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Calificacion.objects.filter(evaluador=self.request.user)

    def perform_create(self, serializer):
        actividad = serializer.validated_data['actividad']
        evaluado = serializer.validated_data['evaluado']
        evaluador = self.request.user

        if evaluador == evaluado:
            raise exceptions.ValidationError({'detail': 'No puedes calificarte a ti mismo.'})

        es_pasada = actividad.fecha_hora <= timezone.now() or actividad.estado == 'FINALIZADA'
        if not es_pasada:
            raise exceptions.ValidationError({'detail': 'Solo puedes calificar cuando el partido haya finalizado.'})

        # Prevenir IntegrityError: validar si ya calificó previamente a este compañero en este partido
        if Calificacion.objects.filter(actividad=actividad, evaluador=evaluador, evaluado=evaluado).exists():
            raise exceptions.ValidationError({'detail': 'Ya calificaste a este jugador en este partido.'})

        participantes_ids = set(
            Inscripcion.objects.filter(actividad=actividad, estado='CONFIRMADA')
            .values_list('usuario_id', flat=True)
        )
        participantes_ids.add(actividad.organizador_id)

        if evaluador.id not in participantes_ids:
            raise exceptions.PermissionDenied('No participaste en este partido.')
        if evaluado.id not in participantes_ids:
            raise exceptions.ValidationError({'detail': 'El usuario a calificar no participó en este partido.'})

        calificacion = serializer.save(evaluador=evaluador)

        # Recálculo de reputación promedio para el evaluado (RF-19)
        promedio = Calificacion.objects.filter(
            evaluado=evaluado
        ).aggregate(Avg('puntuacion'))['puntuacion__avg']

        if promedio is not None:
            evaluado.reputacion_promedio = round(promedio, 2)
            evaluado.save()

        # Registro de auditoría (RF-25)
        registrar_log(
            accion='COMPANERO_CALIFICADO',
            descripcion=f'{evaluador.username} calificó a {evaluado.username} con {calificacion.puntuacion} estrellas en "{actividad.titulo}".',
            modulo='USUARIOS',
            nivel='INFO',
            usuario=evaluador,
            request=self.request
        )

    @action(detail=False, methods=['get'], url_path=r'pendientes/(?P<actividad_id>\d+)')
    def pendientes_por_partido(self, request, actividad_id=None):
        try:
            actividad = Actividad.objects.get(pk=actividad_id)
        except Actividad.DoesNotExist:
            return Response({'detail': 'Partido no encontrado.'}, status=status.HTTP_404_NOT_FOUND)

        ids = list(
            Inscripcion.objects.filter(actividad=actividad, estado='CONFIRMADA')
            .values_list('usuario_id', flat=True)
        )
        ids.append(actividad.organizador_id)

        ya_calificados = Calificacion.objects.filter(
            actividad=actividad, evaluador=request.user
        ).values_list('evaluado_id', flat=True)

        User = get_user_model()
        pendientes = User.objects.filter(id__in=ids).exclude(id=request.user.id).exclude(id__in=ya_calificados)

        data = [{'id': u.id, 'username': u.username} for u in pendientes]
        return Response(data, status=status.HTTP_200_OK)


# ==========================================
# 7. MODERACIÓN, REPORTES Y BLOQUEOS (RF-20, RF-21)
# ==========================================
class ReporteViewSet(viewsets.ModelViewSet):
    """
    Emisión y revisión de reportes por faltas al reglamento comunitario (RF-20).
    """
    serializer_class = ReporteSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'post']

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return Reporte.objects.all().select_related('reportante', 'reportado')
        return Reporte.objects.filter(reportante=user)

    def perform_create(self, serializer):
        if serializer.validated_data.get('reportado') == self.request.user:
            raise exceptions.ValidationError({'detail': 'No puedes reportarte a ti mismo.'})

        reporte = serializer.save(reportante=self.request.user)

        registrar_log(
            accion='REPORTE_CREADO',
            descripcion=f'{self.request.user.username} reportó a {reporte.reportado.username}. Motivo: {reporte.motivo}.',
            modulo='SEGURIDAD',
            nivel='WARNING',
            usuario=self.request.user,
            request=self.request
        )


class BloqueoViewSet(viewsets.ModelViewSet):
    """
    RF-21: Bloqueo de usuarios.
    """
    serializer_class = BloqueoSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'post', 'delete']

    def get_queryset(self):
        return Bloqueo.objects.filter(bloqueador=self.request.user)

    def perform_create(self, serializer):
        bloqueado = serializer.validated_data['bloqueado']
        if bloqueado == self.request.user:
            raise exceptions.ValidationError({'detail': 'No puedes bloquearte a ti mismo.'})

        serializer.save(bloqueador=self.request.user)

        registrar_log(
            accion='USUARIO_BLOQUEADO',
            descripcion=f'{self.request.user.username} bloqueó al usuario {bloqueado.username}.',
            modulo='SEGURIDAD',
            nivel='INFO',
            usuario=self.request.user,
            request=self.request
        )

    @action(detail=False, methods=['post'], url_path='desbloquear')
    def desbloquear(self, request):
        bloqueado_id = request.data.get('bloqueado_id')
        if not bloqueado_id:
            return Response({'detail': 'Falta bloqueado_id.'}, status=status.HTTP_400_BAD_REQUEST)

        eliminados, _ = Bloqueo.objects.filter(
            bloqueador=request.user, bloqueado_id=bloqueado_id
        ).delete()

        if eliminados > 0:
            registrar_log(
                accion='USUARIO_DESBLOQUEADO',
                descripcion=f'{request.user.username} desbloqueó al usuario ID #{bloqueado_id}.',
                modulo='SEGURIDAD',
                nivel='INFO',
                usuario=request.user,
                request=request
            )
            return Response({'detail': 'Usuario desbloqueado exitosamente.'}, status=status.HTTP_200_OK)

        return Response({'detail': 'No se encontró el bloqueo.'}, status=status.HTTP_404_NOT_FOUND)


# ==========================================
# 8. SUSCRIPCIONES PREMIUM (RF-22, RF-23, RF-24)
# ==========================================
class SuscripcionViewSet(viewsets.ModelViewSet):
    """
    Consulta administrativa y gestión directa de suscripciones.
    """
    serializer_class = SuscripcionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_staff:
            return Suscripcion.objects.all()
        return Suscripcion.objects.filter(usuario=user)

    @action(detail=False, methods=['post'])
    def activar(self, request):
        token_transaccion = request.data.get('id_transaccion_psp')
        if not token_transaccion:
            return Response({'detail': 'Falta el identificador de transacción del PSP.'}, status=status.HTTP_400_BAD_REQUEST)

        vencimiento = timezone.now() + timedelta(days=30)
        suscripcion, _ = Suscripcion.objects.update_or_create(
            usuario=request.user,
            defaults={
                'flow_token': token_transaccion,
                'estado': 'ACTIVA',
                'fecha_inicio': timezone.now(),
                'fecha_vencimiento': vencimiento,
            }
        )
        request.user.es_premium = True
        request.user.save()

        registrar_log(
            accion='SUSCRIPCION_MANUAL_ACTIVADA',
            descripcion=f'Suscripción activada directamente para {request.user.username}.',
            modulo='PAGOS',
            nivel='SUCCESS',
            usuario=request.user,
            request=request
        )

        return Response({
            'detail': 'Suscripción Premium activada exitosamente.',
            'fecha_vencimiento': vencimiento,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'])
    def cancelar(self, request):
        suscripcion = Suscripcion.objects.filter(usuario=request.user, estado='ACTIVA').first()
        if not suscripcion:
            return Response({'detail': 'No posees una suscripción activa.'}, status=status.HTTP_400_BAD_REQUEST)

        suscripcion.estado = 'CANCELADA'
        suscripcion.save()

        request.user.es_premium = False
        request.user.save()

        registrar_log(
            accion='SUSCRIPCION_CANCELADA',
            descripcion=f'El usuario {request.user.username} canceló su suscripción.',
            modulo='PAGOS',
            nivel='INFO',
            usuario=request.user,
            request=request
        )

        return Response({'detail': 'Suscripción cancelada.'}, status=status.HTTP_200_OK)


# ====================================================
# PASARELA FLOW (RF-22, RF-23, RF-24)
# ====================================================
class IniciarSuscripcionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        usuario = request.user

        suscripcion, _ = Suscripcion.objects.get_or_create(usuario=usuario)

        if suscripcion.estado == 'ACTIVA' and usuario.es_premium:
            return Response(
                {'detail': 'Ya posees una suscripción Premium activa.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        customer_id = suscripcion.flow_customer_id
        if not customer_id or customer_id.startswith('usr_'):
            res_cliente = FlowService.crear_cliente(usuario)
            customer_id = res_cliente.get('customerId')
            if not customer_id:
                error_msg = res_cliente.get('message', str(res_cliente))
                registrar_log(
                    accion='FLOW_ERROR_CREAR_CLIENTE',
                    descripcion=f'Fallo al registrar cliente Flow para {usuario.username}: {error_msg}',
                    modulo='PAGOS',
                    nivel='ERROR',
                    usuario=usuario,
                    request=request
                )
                return Response(
                    {
                        'detail': f'Error Flow al registrar cliente: {error_msg}',
                        'flow_error': res_cliente
                    },
                    status=status.HTTP_400_BAD_REQUEST
                )

            suscripcion.flow_customer_id = customer_id
            suscripcion.save()

        url_retorno = f"{settings.BASE_URL_BACKEND}/api/pagos/retorno-tarjeta/"
        res_tarjeta = FlowService.iniciar_registro_tarjeta(customer_id, url_retorno)

        url_pago = res_tarjeta.get('url')
        token = res_tarjeta.get('token')

        if not url_pago or not token:
            error_tarjeta = res_tarjeta.get('message', str(res_tarjeta))
            registrar_log(
                accion='FLOW_ERROR_REGISTRO_TARJETA',
                descripcion=f'Flow no generó pasarela para {usuario.username}: {error_tarjeta}',
                modulo='PAGOS',
                nivel='ERROR',
                usuario=usuario,
                request=request
            )
            return Response(
                {
                    'detail': f'Flow no generó la pasarela: {error_tarjeta}',
                    'flow_error': res_tarjeta
                },
                status=status.HTTP_502_BAD_GATEWAY
            )

        suscripcion.flow_token = token
        suscripcion.estado = 'PENDIENTE'
        suscripcion.save()

        registrar_log(
            accion='SUSCRIPCION_INICIADA',
            descripcion=f'Usuario {usuario.username} abrió la pasarela Webpay/Flow con token: {token[:12]}...',
            modulo='PAGOS',
            nivel='INFO',
            usuario=usuario,
            request=request
        )

        return Response({
            'url': f"{url_pago}?token={token}",
            'token': token
        }, status=status.HTTP_200_OK)


class RetornoRegistroTarjetaView(APIView):
    """
    Endpoint al que Flow redirige al usuario tras completar el registro en Webpay.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        return self._procesar_retorno(request)

    def get(self, request):
        return self._procesar_retorno(request)

    def _procesar_retorno(self, request):
        # 1. Extracción tolerante del token
        token = (
            request.data.get('token')
            or request.query_params.get('token')
            or (request.POST.get('token') if hasattr(request, 'POST') else None)
            or (request.GET.get('token') if hasattr(request, 'GET') else None)
        )

        print(f"--> [FLOW RETORNO] Metodo: {request.method} | Token: {token}")

        if not token:
            return HttpResponse("<h2>Token no proporcionado por la pasarela de pagos.</h2>", status=400)

        # 2. Consultar estado en Flow API
        estado_registro = FlowService.consultar_registro_tarjeta(token)
        print(f"--> [FLOW RETORNO] Respuesta Flow getRegisterStatus: {estado_registro}")

        status_code = estado_registro.get('status')
        customer_id = estado_registro.get('customerId')

        # Si responde con 0 (aún pendiente), esperamos 1.5s y reintentamos una vez por latencia
        if str(status_code) == '0':
            time.sleep(1.5)
            estado_registro = FlowService.consultar_registro_tarjeta(token)
            print(f"--> [FLOW RETORNO] Reintento Flow: {estado_registro}")
            status_code = estado_registro.get('status')
            customer_id = estado_registro.get('customerId') or customer_id

        # 3. Localizar suscripción local
        suscripcion = Suscripcion.objects.filter(flow_token=token).first()
        if not suscripcion and customer_id:
            suscripcion = Suscripcion.objects.filter(flow_customer_id=customer_id).first()
        if not suscripcion:
            suscripcion = Suscripcion.objects.filter(estado='PENDIENTE').order_by('-id').first()

        if not suscripcion:
            registrar_log(
                accion='RETORNO_PAGO_HUERFANO',
                descripcion=f'Confirmación recibida con token {str(token)[:12]}... pero sin suscripción local asociada.',
                modulo='PAGOS',
                nivel='WARNING',
                request=request
            )
            return HttpResponse("<h2>Error: No se encontró la suscripción asociada.</h2>", status=404)

        usuario = suscripcion.usuario

        # 4. Validar estado (1 o '1' significa tarjeta registrada con éxito)
        es_exitoso = (str(status_code) == '1' or status_code == 1)

        if es_exitoso:
            sub_id = None
            try:
                res_sub = FlowService.crear_suscripcion(customer_id, settings.FLOW_PLAN_ID)
                print(f"--> [FLOW RETORNO] Respuesta crear_suscripcion: {res_sub}")
                sub_id = res_sub.get('subscriptionId')
            except Exception as e:
                print(f"--> [FLOW RETORNO] Error al crear suscripcion en Flow: {e}")

            suscripcion.flow_subscription_id = sub_id or f"sub_{usuario.id}"
            suscripcion.estado = 'ACTIVA'
            suscripcion.fecha_inicio = timezone.now()
            suscripcion.fecha_vencimiento = timezone.now() + timedelta(days=30)
            suscripcion.save()

            usuario.es_premium = True
            usuario.save()

            registrar_log(
                accion='SUSCRIPCION_ACTIVADA',
                descripcion=f'Plan Premium activado para {usuario.username} (SubscriptionID: {suscripcion.flow_subscription_id}).',
                modulo='PAGOS',
                nivel='SUCCESS',
                usuario=usuario,
                request=request
            )

            html_exito = """
            <!DOCTYPE html>
            <html lang="es">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>MatchDeportivo Premium</title>
                <style>
                    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background-color: #f7f9fa; }
                    .card { background: white; padding: 32px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); text-align: center; max-width: 400px; margin: 16px; }
                    .icon { font-size: 56px; color: #2e7d32; margin-bottom: 16px; }
                    h1 { color: #1e293b; margin: 0 0 8px; font-size: 24px; }
                    p { color: #64748b; line-height: 1.5; margin-bottom: 24px; }
                    .badge { display: inline-block; background-color: #2e7d32; color: white; padding: 6px 14px; border-radius: 20px; font-weight: bold; font-size: 14px; }
                </style>
            </head>
            <body>
                <div class="card">
                    <div class="icon">✓</div>
                    <span class="badge">MATCHDEPORTIVO PREMIUM</span>
                    <h1>¡Suscripción Activada!</h1>
                    <p>Tu tarjeta ha sido vinculada correctamente. Tu plan ya se encuentra activo en tu cuenta.</p>
                    <p style="font-size: 13px; color: #94a3b8;">Puedes cerrar esta ventana y regresar a la aplicación móvil.</p>
                </div>
            </body>
            </html>
            """
            return HttpResponse(html_exito, content_type="text/html; charset=utf-8")
        else:
            suscripcion.estado = 'PENDIENTE'
            suscripcion.save()

            registrar_log(
                accion='PAGO_CANCELADO_O_RECHAZADO',
                descripcion=f'El usuario {usuario.username} canceló o la pasarela rechazó la tarjeta (Status: {status_code}). Detalle: {estado_registro}',
                modulo='PAGOS',
                nivel='WARNING',
                usuario=usuario,
                request=request
            )

            return HttpResponse(
                "<h2>No se pudo vincular la tarjeta o el proceso fue cancelado. Por favor, inténtalo nuevamente desde la app.</h2>",
                status=400,
                content_type="text/html; charset=utf-8"
            )


class EstadoSuscripcionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        usuario = request.user
        try:
            sub = usuario.suscripcion
            data = {
                'es_premium': usuario.es_premium,
                'estado': sub.estado,
                'fecha_inicio': sub.fecha_inicio,
                'fecha_vencimiento': sub.fecha_vencimiento,
                'plan': settings.FLOW_PLAN_ID,
                'flow_token': sub.flow_token,
            }
        except Suscripcion.DoesNotExist:
            data = {
                'es_premium': False,
                'estado': 'SIN_PLAN',
                'fecha_inicio': None,
                'fecha_vencimiento': None,
                'plan': None,
                'flow_token': None,
            }
        return Response(data, status=status.HTTP_200_OK)


class CancelarSuscripcionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        usuario = request.user
        try:
            sub = usuario.suscripcion
        except Suscripcion.DoesNotExist:
            return Response({'detail': 'No tienes una suscripción activa.'}, status=status.HTTP_404_NOT_FOUND)

        if sub.estado != 'ACTIVA':
            return Response({'detail': 'No tienes una suscripción activa para cancelar.'}, status=status.HTTP_400_BAD_REQUEST)

        if sub.flow_subscription_id:
            res_flow = FlowService.cancelar_suscripcion(sub.flow_subscription_id)
            print("Cancelación en Flow:", res_flow)

        sub.estado = 'CANCELADA'
        sub.save()

        usuario.es_premium = False
        usuario.save()

        registrar_log(
            accion='SUSCRIPCION_CANCELADA',
            descripcion=f'Usuario {usuario.username} canceló su suscripción Premium voluntariamente.',
            modulo='PAGOS',
            nivel='INFO',
            usuario=usuario,
            request=request
        )

        return Response({
            'detail': 'Tu suscripción Premium ha sido cancelada exitosamente.',
            'es_premium': False
        }, status=status.HTTP_200_OK)


# ==========================================
# 9. RECUPERACIÓN DE CONTRASEÑA (CÓDIGO OTP)
# ==========================================
class SolicitarRecuperacionView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'otp_solicitar'

    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        if not email:
            return Response({'detail': 'Debes ingresar un correo electrónico.'}, status=status.HTTP_400_BAD_REQUEST)

        usuario = Usuario.objects.filter(email__iexact=email).first()
        if usuario:
            registro_codigo = CodigoRecuperacion.generar_codigo(usuario)

            api_key = os.getenv('BREVO_API_KEY')
            remitente_email = os.getenv('EMAIL_HOST_USER', 'soporte.matchdeportivo@gmail.com')

            payload = {
                "sender": {"name": "Match Deportivo", "email": remitente_email},
                "to": [{"email": usuario.email, "name": usuario.username}],
                "subject": "Código de recuperación - Match Deportivo",
                "htmlContent": (
                    f"<h3>Hola {usuario.username},</h3>"
                    f"<p>Tu código para restablecer tu contraseña en Match Deportivo es:</p>"
                    f"<h2 style='color: #2E7D32; letter-spacing: 4px;'>{registro_codigo.codigo}</h2>"
                    f"<p>Este código es válido durante los próximos 15 minutos.</p>"
                    f"<p>Si no solicitaste este cambio, puedes ignorar este mensaje.</p>"
                )
            }

            try:
                req = urllib.request.Request(
                    "https://api.brevo.com/v3/smtp/email",
                    data=json.dumps(payload).encode('utf-8'),
                    headers={
                        "accept": "application/json",
                        "api-key": api_key,
                        "content-type": "application/json"
                    },
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as response:
                    pass

                registrar_log(
                    accion='SOLICITUD_CODIGO_RECUPERACION',
                    descripcion=f'Código de recuperación OTP generado y enviado a {usuario.email}.',
                    modulo='USUARIOS',
                    nivel='INFO',
                    usuario=usuario,
                    request=request
                )
            except Exception as e:
                print(f"Error al enviar correo por Brevo API: {e}")

        return Response({
            'detail': 'Si el correo está registrado en el sistema, recibirás un código de 6 dígitos en tu bandeja de entrada.'
        }, status=status.HTTP_200_OK)


class RestablecerPasswordView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'otp_restablecer'

    MAX_INTENTOS = 5
    VENTANA_SEGUNDOS = 15 * 60

    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        codigo = request.data.get('codigo', '').strip()
        nueva_password = request.data.get('nueva_password', '')

        if not email or not codigo or not nueva_password:
            return Response({'detail': 'Todos los campos son obligatorios.'}, status=status.HTTP_400_BAD_REQUEST)

        if len(nueva_password) < 8:
            return Response({'detail': 'La nueva contraseña debe tener al menos 8 caracteres.'}, status=status.HTTP_400_BAD_REQUEST)

        usuario = Usuario.objects.filter(email__iexact=email).first()
        if not usuario:
            return Response({'detail': 'Código o correo inválido.'}, status=status.HTTP_400_BAD_REQUEST)

        clave_intentos = f'otp_intentos:{usuario.pk}'
        if cache.get(clave_intentos, 0) >= self.MAX_INTENTOS:
            return Response({'detail': 'Demasiados intentos. Solicita un nuevo código.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        registro_codigo = CodigoRecuperacion.objects.filter(usuario=usuario, codigo=codigo).first()
        if not registro_codigo or not registro_codigo.es_valido():
            cache.set(clave_intentos, cache.get(clave_intentos, 0) + 1, self.VENTANA_SEGUNDOS)
            return Response({'detail': 'El código es inválido o ha expirado.'}, status=status.HTTP_400_BAD_REQUEST)

        cache.delete(clave_intentos)

        usuario.set_password(nueva_password)
        usuario.save()

        registro_codigo.usado = True
        registro_codigo.save()

        registrar_log(
            accion='PASSWORD_RESTABLECIDO',
            descripcion=f'El usuario {usuario.username} restableció exitosamente su contraseña con código OTP.',
            modulo='USUARIOS',
            nivel='SUCCESS',
            usuario=usuario,
            request=request
        )

        return Response({'detail': '¡Contraseña restablecida con éxito! Ya puedes iniciar sesión.'}, status=status.HTTP_200_OK)


# ==========================================
# 10. AUDITORÍA DEL SISTEMA (RF-25)
# ==========================================
class AdminLogsListView(generics.ListAPIView):
    """RF-25: Permite únicamente al staff consultar los logs del sistema."""
    serializer_class = RegistroActividadSerializer
    permission_classes = [permissions.IsAdminUser]

    def get_queryset(self):
        queryset = RegistroActividad.objects.all().select_related('usuario')
        modulo = self.request.query_params.get('modulo')
        nivel = self.request.query_params.get('nivel')

        if modulo:
            queryset = queryset.filter(modulo=modulo.upper())
        if nivel:
            queryset = queryset.filter(nivel=nivel.upper())

        return queryset[:200]


    # ==========================================
    # RF-26: SUSPENSIÓN Y REACTIVACIÓN DE CUENTAS
    # ==========================================
    @action(detail=True, methods=['post'], url_path='cambiar-estado-cuenta', permission_classes=[permissions.IsAdminUser])
    def cambiar_estado_cuenta(self, request, pk=None):
        """
        RF-26: Permite a un administrador suspender (is_active=False) 
        o reactivar (is_active=True) a un usuario, registrando la auditoría (RF-25).
        """
        usuario = self.get_object()

        # 1. Reglas de protección de administradores
        if usuario == request.user:
            return Response(
                {'detail': 'No puedes suspender tu propia cuenta de administrador.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if usuario.is_superuser:
            return Response(
                {'detail': 'No está permitido suspender a un superusuario del sistema.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 2. Alternar estado y capturar motivo
        motivo = request.data.get('motivo', '').strip()
        if not motivo:
            motivo = 'Decisión administrativa / Infracción a normas comunitarias.'

        usuario.is_active = not usuario.is_active
        usuario.save()

        nuevo_estado_str = "ACTIVA" if usuario.is_active else "SUSPENDIDA"
        accion_log = "USUARIO_REACTIVADO" if usuario.is_active else "USUARIO_SUSPENDIDO"
        nivel_log = "INFO" if usuario.is_active else "WARNING"

        # 3. Disparar auditoría RF-25
        registrar_log(
            accion=accion_log,
            descripcion=(
                f"El administrador {request.user.username} cambió el estado de {usuario.username} "
                f"a {nuevo_estado_str}. Motivo: '{motivo}'."
            ),
            modulo='SEGURIDAD',
            nivel=nivel_log,
            usuario=request.user,
            request=request
        )

        return Response({
            'detail': f'Cuenta de {usuario.username} {nuevo_estado_str.lower()} exitosamente.',
            'usuario_id': usuario.id,
            'username': usuario.username,
            'is_active': usuario.is_active,
        }, status=status.HTTP_200_OK)