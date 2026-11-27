"""
RF-24: cancelar Premium rige al final del período ya pagado.
Antes, cancelar quitaba Premium al instante aunque el usuario ya hubiera pagado el mes.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Suscripcion, Usuario
from core.utils import expirar_premium_si_corresponde, fin_del_periodo_pagado


def premium_con_sub(username, dias_desde_inicio=5, **extra):
    u = Usuario.objects.create_user(username=username, email=f'{username}@test.cl',
                                    password='Clave12345', es_premium=True)
    inicio = timezone.now() - timedelta(days=dias_desde_inicio)
    sub = Suscripcion.objects.create(usuario=u, estado='ACTIVA', flow_subscription_id=f'sub_{username}',
                                     fecha_inicio=inicio, fecha_vencimiento=inicio + timedelta(days=30), **extra)
    return u, sub


class CancelarPremiumTests(TestCase):
    URL = '/api/pagos/cancelar-suscripcion/'

    def setUp(self):
        self.client = APIClient()

    @patch('core.views.FlowService')
    def test_cancelar_mantiene_premium_hasta_el_fin_del_periodo(self, flow):
        u, sub = premium_con_sub('ana', dias_desde_inicio=5)
        self.client.force_authenticate(u)
        r = self.client.post(self.URL)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['es_premium'])
        u.refresh_from_db(); sub.refresh_from_db()
        self.assertTrue(u.es_premium)
        self.assertEqual(sub.estado, 'CANCELADA')
        self.assertAlmostEqual((sub.fecha_vencimiento - timezone.now()).days, 24, delta=1)
        flow.cancelar_suscripcion.assert_called_once_with('sub_ana', al_final_del_periodo=True)

    @patch('core.views.FlowService')
    def test_periodo_ya_renovado_usa_el_ciclo_vigente(self, flow):
        """A los 45 días Flow ya cobró el 2.º mes: Premium debe durar hasta el día 60, no cortarse."""
        u, sub = premium_con_sub('beto', dias_desde_inicio=45)
        self.client.force_authenticate(u)
        r = self.client.post(self.URL)
        self.assertEqual(r.status_code, 200)
        sub.refresh_from_db()
        self.assertAlmostEqual((sub.fecha_vencimiento - timezone.now()).days, 14, delta=1)
        u.refresh_from_db()
        self.assertTrue(u.es_premium)

    @patch('core.views.FlowService')
    def test_no_se_puede_cancelar_dos_veces(self, flow):
        u, sub = premium_con_sub('cami')
        self.client.force_authenticate(u)
        self.client.post(self.URL)
        self.assertEqual(self.client.post(self.URL).status_code, 400)

    @patch('core.views.FlowService')
    def test_no_puede_volver_a_suscribirse_mientras_dure_el_periodo(self, flow):
        u, sub = premium_con_sub('dani')
        self.client.force_authenticate(u)
        self.client.post(self.URL)
        r = self.client.post('/api/pagos/iniciar-suscripcion/')
        self.assertEqual(r.status_code, 400)
        flow.crear_cliente.assert_not_called()


class ExpiracionPremiumTests(TestCase):
    def test_pierde_premium_al_vencer_el_periodo_tras_cancelar(self):
        u, sub = premium_con_sub('eli', dias_desde_inicio=40)
        sub.estado = 'CANCELADA'
        sub.fecha_vencimiento = timezone.now() - timedelta(days=1)
        sub.save()
        self.assertTrue(expirar_premium_si_corresponde(u))
        u.refresh_from_db()
        self.assertFalse(u.es_premium)

    def test_no_pierde_premium_si_el_periodo_sigue_vigente(self):
        u, sub = premium_con_sub('fabi')
        sub.estado = 'CANCELADA'
        sub.fecha_vencimiento = timezone.now() + timedelta(days=3)
        sub.save()
        self.assertFalse(expirar_premium_si_corresponde(u))
        u.refresh_from_db()
        self.assertTrue(u.es_premium)

    def test_suscripcion_activa_nunca_se_expira_localmente(self):
        u, sub = premium_con_sub('gabo', dias_desde_inicio=100)
        self.assertFalse(expirar_premium_si_corresponde(u))
        u.refresh_from_db()
        self.assertTrue(u.es_premium)

    def test_la_autenticacion_quita_premium_vencido_en_la_siguiente_peticion(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        u, sub = premium_con_sub('hugo', dias_desde_inicio=40)
        sub.estado = 'CANCELADA'
        sub.fecha_vencimiento = timezone.now() - timedelta(hours=1)
        sub.save()
        token = str(RefreshToken.for_user(u).access_token)
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        r = c.get('/api/pagos/mi-suscripcion/')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data['es_premium'])
        u.refresh_from_db()
        self.assertFalse(u.es_premium)

    def test_fin_del_periodo_siempre_es_futuro(self):
        for dias in (0, 1, 29, 30, 31, 59, 60, 61, 200):
            _u, sub = premium_con_sub(f'u{dias}', dias_desde_inicio=dias)
            self.assertGreater(fin_del_periodo_pagado(sub), timezone.now(), dias)
