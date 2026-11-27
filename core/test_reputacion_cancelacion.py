"""
Pruebas de los hotfix de reputación (RF-18, RF-19) y de cancelación tardía (CU-05, RF-11).

Antes del arreglo, calificar a un jugador devolvía error 500 porque el código intentaba
asignar un valor a Usuario.reputacion_promedio, que es una propiedad de solo lectura.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Calificacion, Inscripcion, RegistroActividad, Usuario
from core.test_caracterizacion import crear_actividad, crear_usuario


class CalificarJugadorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = crear_usuario('org')
        self.a = crear_usuario('a')
        self.b = crear_usuario('b')
        # Partido ya jugado, con tres participantes confirmados
        self.act = crear_actividad(self.org, fecha_hora=timezone.now() - timedelta(hours=3))
        for u in (self.a, self.b):
            Inscripcion.objects.create(actividad=self.act, usuario=u, estado='CONFIRMADA')

    def calificar(self, evaluador, evaluado, puntos):
        self.client.force_authenticate(evaluador)
        return self.client.post('/api/calificaciones/', {
            'actividad': self.act.id, 'evaluado': evaluado.id, 'puntuacion': puntos}, format='json')

    def test_calificar_devuelve_201_y_actualiza_reputacion(self):
        r = self.calificar(self.a, self.b, 4)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Calificacion.objects.count(), 1)
        self.assertEqual(Usuario.objects.get(pk=self.b.pk).reputacion_promedio, 4.0)

    def test_reputacion_es_el_promedio_de_las_calificaciones(self):
        self.calificar(self.a, self.b, 5)
        self.calificar(self.org, self.b, 2)
        self.assertEqual(Usuario.objects.get(pk=self.b.pk).reputacion_promedio, 3.5)

    def test_sin_calificaciones_la_reputacion_es_5(self):
        self.assertEqual(Usuario.objects.get(pk=self.b.pk).reputacion_promedio, 5.0)

    def test_no_se_puede_calificar_a_si_mismo(self):
        self.assertEqual(self.calificar(self.a, self.a, 5).status_code, 400)

    def test_no_se_puede_calificar_dos_veces_al_mismo_jugador(self):
        self.calificar(self.a, self.b, 5)
        self.assertEqual(self.calificar(self.a, self.b, 1).status_code, 400)

    def test_valoracion_legacy_no_devuelve_500(self):
        self.client.force_authenticate(self.a)
        r = self.client.post('/api/valoraciones/', {
            'actividad': self.act.id, 'evaluado': self.b.id, 'puntuacion': 4}, format='json')
        self.assertLess(r.status_code, 500, getattr(r, 'data', None))


class CancelacionTardiaTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = crear_usuario('org')
        self.user = crear_usuario('jugador')
        self.client.force_authenticate(self.user)

    def abandonar(self, horas):
        act = crear_actividad(self.org, fecha_hora=timezone.now() + timedelta(hours=horas))
        Inscripcion.objects.create(actividad=act, usuario=self.user, estado='CONFIRMADA')
        r = self.client.post(f'/api/actividades/{act.id}/abandonar/')
        return act, r

    def tardias(self):
        return RegistroActividad.objects.filter(accion='CANCELACION_TARDIA').count()

    def test_con_mas_de_2_horas_no_queda_registro_y_libera_cupo(self):
        act, r = self.abandonar(5)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data['cancelacion_tardia'])
        self.assertEqual(Inscripcion.objects.get(actividad=act).estado, 'CANCELADA')
        self.assertEqual(self.tardias(), 0)

    def test_con_2_horas_o_menos_queda_como_cancelacion_tardia_y_libera_cupo(self):
        act, r = self.abandonar(1.5)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['cancelacion_tardia'])
        self.assertEqual(Inscripcion.objects.get(actividad=act).estado, 'CANCELADA')
        self.assertEqual(self.tardias(), 1)

    def test_no_se_puede_abandonar_una_actividad_que_ya_comenzo(self):
        act, r = self.abandonar(-1)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Inscripcion.objects.get(actividad=act).estado, 'CONFIRMADA')
        self.assertEqual(self.tardias(), 0)
