"""
Tests de seguridad de suscripciones Premium (hotfix C1 y C2).

Cada test documenta una regla: nadie obtiene Premium sin un pago verificado por Flow.
"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from core.models import Suscripcion, Usuario


def crear_usuario(username, **extra):
    return Usuario.objects.create_user(
        username=username, email=f'{username}@test.cl', password='Clave12345', **extra
    )


class PremiumGratisCerradoTests(TestCase):
    """C1: no debe existir forma de activar Premium sin pagar."""

    def setUp(self):
        self.user = crear_usuario('ana')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_endpoint_activar_ya_no_existe(self):
        r = self.client.post('/api/suscripciones/activar/', {'id_transaccion_psp': 'x'}, format='json')
        self.assertIn(r.status_code, (404, 405))
        self.user.refresh_from_db()
        self.assertFalse(self.user.es_premium)

    def test_usuario_no_puede_crear_suscripcion_por_api(self):
        r = self.client.post('/api/suscripciones/', {'estado': 'ACTIVA'}, format='json')
        self.assertEqual(r.status_code, 405)
        self.assertFalse(Suscripcion.objects.filter(usuario=self.user).exists())

    def test_usuario_no_puede_editar_su_suscripcion_por_api(self):
        sub = Suscripcion.objects.create(usuario=self.user, estado='PENDIENTE')
        r = self.client.patch(f'/api/suscripciones/{sub.id}/', {'estado': 'ACTIVA'}, format='json')
        self.assertEqual(r.status_code, 405)
        sub.refresh_from_db()
        self.assertEqual(sub.estado, 'PENDIENTE')

    def test_usuario_ve_solo_su_suscripcion(self):
        otro = crear_usuario('beto')
        Suscripcion.objects.create(usuario=self.user, estado='PENDIENTE')
        Suscripcion.objects.create(usuario=otro, estado='PENDIENTE')
        r = self.client.get('/api/suscripciones/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data['results']), 1)

    def test_perfil_no_permite_marcarse_premium(self):
        r = self.client.patch('/api/usuarios/mi_perfil/', {'es_premium': True}, format='json')
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.es_premium)


@patch('core.views.time.sleep', lambda s: None)
class RetornoFlowTests(TestCase):
    """C2: el retorno de Flow solo activa la suscripcion que corresponde a SU token."""

    URL = '/api/pagos/retorno-tarjeta/'

    def setUp(self):
        self.victima = crear_usuario('victima')
        self.atacante = crear_usuario('atacante')
        self.sub_victima = Suscripcion.objects.create(
            usuario=self.victima, estado='PENDIENTE',
            flow_token='TOKEN_VICTIMA', flow_customer_id='cus_victima')
        self.client = APIClient()

    @patch('core.views.FlowService')
    def test_token_desconocido_no_activa_a_nadie(self, flow):
        """Antes se activaba 'la ultima PENDIENTE' de cualquier usuario."""
        flow.consultar_registro_tarjeta.return_value = {'status': 1, 'customerId': 'cus_otro'}
        flow.crear_suscripcion.return_value = {'subscriptionId': 'sub_1'}
        r = self.client.get(self.URL, {'token': 'TOKEN_INVENTADO'})
        self.assertEqual(r.status_code, 404)
        self.victima.refresh_from_db()
        self.sub_victima.refresh_from_db()
        self.assertFalse(self.victima.es_premium)
        self.assertEqual(self.sub_victima.estado, 'PENDIENTE')
        flow.crear_suscripcion.assert_not_called()

    @patch('core.views.FlowService')
    def test_sin_token_responde_400(self, flow):
        r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 400)

    @patch('core.views.FlowService')
    def test_pago_valido_activa_premium(self, flow):
        flow.consultar_registro_tarjeta.return_value = {'status': 1, 'customerId': 'cus_victima'}
        flow.crear_suscripcion.return_value = {'subscriptionId': 'sub_real'}
        r = self.client.get(self.URL, {'token': 'TOKEN_VICTIMA'})
        self.assertEqual(r.status_code, 200)
        self.victima.refresh_from_db()
        self.sub_victima.refresh_from_db()
        self.assertTrue(self.victima.es_premium)
        self.assertEqual(self.sub_victima.estado, 'ACTIVA')
        self.assertEqual(self.sub_victima.flow_subscription_id, 'sub_real')
        self.assertIsNotNone(self.sub_victima.fecha_vencimiento)

    @patch('core.views.FlowService')
    def test_si_flow_no_crea_suscripcion_no_se_activa(self, flow):
        """Antes se inventaba 'sub_<id>' y se activaba igual."""
        flow.consultar_registro_tarjeta.return_value = {'status': 1, 'customerId': 'cus_victima'}
        flow.crear_suscripcion.return_value = {'code': 100, 'message': 'error'}
        r = self.client.get(self.URL, {'token': 'TOKEN_VICTIMA'})
        self.assertEqual(r.status_code, 502)
        self.victima.refresh_from_db()
        self.sub_victima.refresh_from_db()
        self.assertFalse(self.victima.es_premium)
        self.assertNotEqual(self.sub_victima.estado, 'ACTIVA')

    @patch('core.views.FlowService')
    def test_si_flow_lanza_excepcion_no_se_activa(self, flow):
        flow.consultar_registro_tarjeta.return_value = {'status': 1, 'customerId': 'cus_victima'}
        flow.crear_suscripcion.side_effect = RuntimeError('timeout')
        r = self.client.get(self.URL, {'token': 'TOKEN_VICTIMA'})
        self.assertEqual(r.status_code, 502)
        self.victima.refresh_from_db()
        self.assertFalse(self.victima.es_premium)

    @patch('core.views.FlowService')
    def test_tarjeta_rechazada_no_activa(self, flow):
        flow.consultar_registro_tarjeta.return_value = {'status': 2, 'customerId': 'cus_victima'}
        r = self.client.get(self.URL, {'token': 'TOKEN_VICTIMA'})
        self.assertEqual(r.status_code, 400)
        self.victima.refresh_from_db()
        self.assertFalse(self.victima.es_premium)
        flow.crear_suscripcion.assert_not_called()

    @patch('core.views.FlowService')
    def test_customer_id_distinto_se_rechaza(self, flow):
        flow.consultar_registro_tarjeta.return_value = {'status': 1, 'customerId': 'cus_ajeno'}
        r = self.client.get(self.URL, {'token': 'TOKEN_VICTIMA'})
        self.assertEqual(r.status_code, 400)
        self.victima.refresh_from_db()
        self.assertFalse(self.victima.es_premium)
        flow.crear_suscripcion.assert_not_called()

    @patch('core.views.FlowService')
    def test_retorno_repetido_es_idempotente(self, flow):
        flow.consultar_registro_tarjeta.return_value = {'status': 1, 'customerId': 'cus_victima'}
        flow.crear_suscripcion.return_value = {'subscriptionId': 'sub_real'}
        self.client.get(self.URL, {'token': 'TOKEN_VICTIMA'})
        r = self.client.get(self.URL, {'token': 'TOKEN_VICTIMA'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(flow.crear_suscripcion.call_count, 1)

    @patch('core.views.FlowService')
    def test_retorno_fallido_no_degrada_suscripcion_activa(self, flow):
        self.sub_victima.estado = 'ACTIVA'
        self.sub_victima.save()
        self.victima.es_premium = True
        self.victima.save()
        flow.consultar_registro_tarjeta.return_value = {'status': 2, 'customerId': 'cus_victima'}
        self.client.get(self.URL, {'token': 'TOKEN_VICTIMA'})
        self.sub_victima.refresh_from_db()
        self.assertEqual(self.sub_victima.estado, 'ACTIVA')
