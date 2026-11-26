"""Tests del middleware de request-id y del logging de auditoria."""
from unittest.mock import patch

from django.test import TestCase

from core.models import RegistroActividad
from core.utils import registrar_log


class RequestIdTests(TestCase):
    def test_genera_request_id_en_la_respuesta(self):
        r = self.client.get('/api/actividades/')
        self.assertEqual(r.status_code, 200)
        self.assertRegex(r['X-Request-ID'], r'^[0-9a-f]{16}$')

    def test_reutiliza_request_id_entrante_valido(self):
        r = self.client.get('/api/actividades/', HTTP_X_REQUEST_ID='abc-123_XYZ.9')
        self.assertEqual(r['X-Request-ID'], 'abc-123_XYZ.9')

    def test_descarta_request_id_peligroso_o_largo(self):
        for malo in ('x' * 65, 'a b', 'a\nb', 'a;b'):
            r = self.client.get('/api/actividades/', HTTP_X_REQUEST_ID=malo)
            self.assertNotEqual(r['X-Request-ID'], malo)
            self.assertRegex(r['X-Request-ID'], r'^[0-9a-f]{16}$')

    def test_request_ids_distintos_por_peticion(self):
        a = self.client.get('/api/actividades/')['X-Request-ID']
        b = self.client.get('/api/actividades/')['X-Request-ID']
        self.assertNotEqual(a, b)

    def test_registra_una_linea_por_peticion(self):
        with self.assertLogs('core.request', level='INFO') as cm:
            self.client.get('/api/actividades/')
        self.assertEqual(len(cm.output), 1)
        self.assertIn('GET /api/actividades/ -> 200', cm.output[0])

    @patch('core.views.FlowService')
    def test_no_registra_query_string(self, flow):
        flow.consultar_registro_tarjeta.return_value = {'status': 2}
        with self.assertLogs('core.request', level='INFO') as cm:
            self.client.get('/api/pagos/retorno-tarjeta/', {'token': 'SECRETO123'})
        self.assertNotIn('SECRETO123', cm.output[0])

    def test_health_no_se_registra(self):
        with self.assertNoLogs('core.request', level='INFO'):
            r = self.client.get('/health/')
        self.assertEqual(r.status_code, 200)

    def test_errores_4xx_se_registran_como_warning(self):
        with self.assertLogs('core.request', level='WARNING') as cm:
            self.client.get('/api/inscripciones/')  # sin token -> 401
        self.assertIn('401', cm.output[0])


class AuditoriaTests(TestCase):
    def test_registrar_log_guarda_en_bd_y_en_consola(self):
        with self.assertLogs('core.audit', level='INFO') as cm:
            registrar_log('PRUEBA', 'descripcion de prueba', modulo='SISTEMA')
        self.assertEqual(RegistroActividad.objects.filter(accion='PRUEBA').count(), 1)
        self.assertIn('SISTEMA/PRUEBA', cm.output[0])

    def test_fallo_al_guardar_auditoria_no_rompe_la_operacion(self):
        with patch('core.utils.RegistroActividad.objects.create', side_effect=RuntimeError('bd caida')):
            with self.assertLogs('core.audit', level='ERROR'):
                registrar_log('PRUEBA', 'no debe lanzar')  # no lanza excepcion
