"""C3 (2.ª parte): sincronizar el Premium con Flow para quien deja de pagar sin cancelar."""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from core import premium_sync
from core.models import Suscripcion, Usuario

FLOW = 'core.premium_sync.FlowService.obtener_suscripcion'


def flow_ok(**extra):
    """Respuesta de subscription/get de una suscripción al día, renovada hasta dentro de 29 días."""
    fin = timezone.now().astimezone(premium_sync.ZONA_FLOW) + timedelta(days=29)
    datos = {'subscriptionId': 'sub_x', 'status': 1, 'morose': 0, 'cancel_at_period_end': 0,
             'period_end': fin.strftime(premium_sync.FORMATO_FECHA_FLOW)}
    datos.update(extra)
    return datos


def premium_vencido(nombre, estado='ACTIVA'):
    """Premium cuyo período guardado terminó hace 2 días (le toca consultar a Flow)."""
    u = Usuario.objects.create_user(username=nombre, email=f'{nombre}@t.cl', password='Clave12345', es_premium=True)
    sub = Suscripcion.objects.create(
        usuario=u, estado=estado, flow_subscription_id=f'sub_{nombre}',
        fecha_inicio=timezone.now() - timedelta(days=32),
        fecha_vencimiento=timezone.now() - timedelta(days=2))
    return u, sub


@override_settings(PREMIUM_SYNC_ENABLED=True, PREMIUM_SYNC_INTERVALO_SEG=600, PREMIUM_GRACIA_DIAS=1)
class SincronizarConFlowTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch(FLOW)
    def test_renovacion_extiende_premium_con_el_periodo_de_flow(self, flow):
        flow.return_value = flow_ok()
        u, sub = premium_vencido('ana')
        self.assertEqual(premium_sync.sincronizar_con_flow(u, sub), premium_sync.RENOVADA)
        sub.refresh_from_db(); u.refresh_from_db()
        self.assertTrue(u.es_premium)
        self.assertEqual(sub.estado, 'ACTIVA')
        self.assertAlmostEqual((sub.fecha_vencimiento - timezone.now()).days, 29, delta=1)

    @patch(FLOW)
    def test_moroso_pierde_premium(self, flow):
        flow.return_value = flow_ok(morose=1)
        u, sub = premium_vencido('beto')
        self.assertEqual(premium_sync.sincronizar_con_flow(u, sub), premium_sync.MOROSA)
        sub.refresh_from_db(); u.refresh_from_db()
        self.assertFalse(u.es_premium)
        self.assertEqual(sub.estado, 'VENCIDA')

    @patch(FLOW)
    def test_pago_pendiente_aun_no_vencido_conserva_premium(self, flow):
        flow.return_value = flow_ok(morose=2)
        u, sub = premium_vencido('cami')
        premium_sync.sincronizar_con_flow(u, sub)
        u.refresh_from_db()
        self.assertTrue(u.es_premium)

    @patch(FLOW)
    def test_suscripcion_inactiva_en_flow_conserva_hasta_el_fin_del_periodo(self, flow):
        flow.return_value = flow_ok(status=4)
        u, sub = premium_vencido('dani')
        self.assertEqual(premium_sync.sincronizar_con_flow(u, sub), premium_sync.CANCELADA)
        sub.refresh_from_db(); u.refresh_from_db()
        self.assertEqual(sub.estado, 'CANCELADA')
        self.assertTrue(u.es_premium)  # lo pagado sigue vigente 29 días más

    @patch(FLOW)
    def test_inactiva_con_periodo_terminado_quita_premium(self, flow):
        ayer = (timezone.now().astimezone(premium_sync.ZONA_FLOW) - timedelta(days=5)).strftime(premium_sync.FORMATO_FECHA_FLOW)
        flow.return_value = flow_ok(status=4, period_end=ayer)
        u, sub = premium_vencido('eli')
        premium_sync.sincronizar_con_flow(u, sub)
        u.refresh_from_db()
        self.assertFalse(u.es_premium)

    @patch(FLOW)
    def test_cancelada_en_flow_para_fin_de_periodo(self, flow):
        flow.return_value = flow_ok(cancel_at_period_end=1)
        u, sub = premium_vencido('fede')
        self.assertEqual(premium_sync.sincronizar_con_flow(u, sub), premium_sync.CANCELADA)

    @patch(FLOW)
    def test_si_flow_falla_no_se_toca_nada(self, flow):
        for respuesta in ({'code': -1, 'message': 'timeout', 'error_conexion': True},
                          {'status': 500, 'text': '<html>'}, None, 'basura', {}):
            flow.return_value = respuesta
            u, sub = premium_vencido(f'gus{abs(hash(str(respuesta))) % 9999}')
            self.assertEqual(premium_sync.sincronizar_con_flow(u, sub), premium_sync.ERROR)
            u.refresh_from_db(); sub.refresh_from_db()
            self.assertTrue(u.es_premium)
            self.assertEqual(sub.estado, 'ACTIVA')

    @patch(FLOW)
    def test_sin_period_end_no_extiende_ni_quita(self, flow):
        flow.return_value = flow_ok(period_end=None)
        u, sub = premium_vencido('hana')
        self.assertEqual(premium_sync.sincronizar_con_flow(u, sub), premium_sync.ERROR)
        u.refresh_from_db()
        self.assertTrue(u.es_premium)


@override_settings(PREMIUM_SYNC_ENABLED=True, PREMIUM_SYNC_INTERVALO_SEG=600, PREMIUM_GRACIA_DIAS=1)
class SincronizacionEnCadaRequestTests(TestCase):
    """Se prueba por la API real: el JWT pasa por JWTAuthenticationConPremiumVigente."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def _login(self, u):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(u).access_token}')

    @patch(FLOW)
    def test_si_flow_falla_no_se_le_consulta_en_cada_request(self, flow):
        """Con Flow caído la fecha no avanza: sin límite, cada petición volvería a llamarlo."""
        flow.return_value = {'code': -1, 'message': 'timeout', 'error_conexion': True}
        u, _ = premium_vencido('ivan')
        self._login(u)
        for _ in range(5):
            r = self.client.get('/api/pagos/mi-suscripcion/')
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.data['es_premium'])
        self.assertEqual(flow.call_count, 1)

    @patch(FLOW)
    def test_tras_renovar_no_vuelve_a_consultar(self, flow):
        flow.return_value = flow_ok()
        u, _ = premium_vencido('ivana')
        self._login(u)
        for _ in range(5):
            self.client.get('/api/pagos/mi-suscripcion/')
        self.assertEqual(flow.call_count, 1)

    @patch(FLOW)
    def test_pasado_el_intervalo_vuelve_a_intentar(self, flow):
        flow.return_value = {'code': -1, 'message': 'timeout', 'error_conexion': True}
        u, _ = premium_vencido('ivo')
        self._login(u)
        self.client.get('/api/pagos/mi-suscripcion/')
        cache.clear()  # equivale a que pasaron PREMIUM_SYNC_INTERVALO_SEG segundos
        self.client.get('/api/pagos/mi-suscripcion/')
        self.assertEqual(flow.call_count, 2)

    @patch(FLOW)
    def test_moroso_pierde_premium_al_usar_la_app(self, flow):
        flow.return_value = flow_ok(morose=1)
        u, _ = premium_vencido('juana')
        self._login(u)
        r = self.client.get('/api/pagos/mi-suscripcion/')
        self.assertFalse(r.data['es_premium'])

    @patch(FLOW)
    def test_periodo_vigente_no_consulta_a_flow(self, flow):
        u, sub = premium_vencido('karen')
        sub.fecha_vencimiento = timezone.now() + timedelta(days=10)
        sub.save()
        self._login(u)
        self.client.get('/api/pagos/mi-suscripcion/')
        flow.assert_not_called()

    @patch(FLOW, side_effect=RuntimeError('Flow caído'))
    def test_una_excepcion_no_rompe_el_login_ni_quita_premium(self, flow):
        u, _ = premium_vencido('luis')
        self._login(u)
        r = self.client.get('/api/pagos/mi-suscripcion/')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['es_premium'])

    @patch(FLOW)
    def test_usuario_gratuito_nunca_consulta_a_flow(self, flow):
        u = Usuario.objects.create_user(username='maria', email='m@t.cl', password='Clave12345')
        self._login(u)
        self.client.get('/api/pagos/mi-suscripcion/')
        flow.assert_not_called()


class InterruptorApagadoTests(TestCase):
    """Con PREMIUM_SYNC_ENABLED=False (valor por defecto) la app no consulta Flow jamás."""

    def setUp(self):
        cache.clear()

    @override_settings(PREMIUM_SYNC_ENABLED=False)
    @patch(FLOW)
    def test_apagado_no_consulta_ni_quita_premium(self, flow):
        u, _ = premium_vencido('nico')
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {RefreshToken.for_user(u).access_token}')
        r = c.get('/api/pagos/mi-suscripcion/')
        self.assertTrue(r.data['es_premium'])
        flow.assert_not_called()

    def test_por_defecto_esta_apagado(self):
        from django.conf import settings
        self.assertFalse(settings.PREMIUM_SYNC_ENABLED)


@override_settings(PREMIUM_SYNC_ENABLED=False, PREMIUM_GRACIA_DIAS=1)
class ComandoSincronizarTests(TestCase):
    @patch(FLOW)
    def test_comando_procesa_solo_las_vencidas_y_funciona_con_interruptor_apagado(self, flow):
        flow.return_value = flow_ok(morose=1)
        vencido, _ = premium_vencido('olga')
        vigente, sub = premium_vencido('pablo')
        sub.fecha_vencimiento = timezone.now() + timedelta(days=10)
        sub.save()
        salida = StringIO()
        call_command('sincronizar_premium', stdout=salida)
        vencido.refresh_from_db(); vigente.refresh_from_db()
        self.assertFalse(vencido.es_premium)
        self.assertTrue(vigente.es_premium)
        self.assertEqual(flow.call_count, 1)
        self.assertIn('MOROSA', salida.getvalue())


class FlowServiceTimeoutTests(TestCase):
    def test_flow_lento_devuelve_error_en_vez_de_colgar(self):
        import requests
        from core.flow_service import FlowService, TIMEOUT_SEGUNDOS
        with patch('core.flow_service.requests.get', side_effect=requests.Timeout()) as get:
            res = FlowService.obtener_suscripcion('sub_1')
        self.assertTrue(res.get('error_conexion'))
        self.assertEqual(get.call_args.kwargs['timeout'], TIMEOUT_SEGUNDOS)

    def test_toda_llamada_post_lleva_timeout(self):
        from core.flow_service import FlowService
        with patch('core.flow_service.requests.post') as post:
            post.return_value.json.return_value = {}
            FlowService.cancelar_suscripcion('sub_1')
        self.assertIn('timeout', post.call_args.kwargs)


class ComandoDiagnosticoTests(TestCase):
    @patch(FLOW)
    def test_diagnostico_muestra_los_campos_de_flow_y_no_modifica_nada(self, flow):
        flow.return_value = flow_ok(morose=1, customerId='cus_secreto', email='x@y.cl')
        u, sub = premium_vencido('quique')
        salida = StringIO()
        call_command('sincronizar_premium', '--diagnostico', stdout=salida)
        texto = salida.getvalue()
        self.assertIn('morose=1', texto)
        self.assertIn('period_end=', texto)
        self.assertNotIn('cus_secreto', texto)   # datos del cliente no se imprimen
        self.assertNotIn('x@y.cl', texto)
        u.refresh_from_db(); sub.refresh_from_db()
        self.assertTrue(u.es_premium)
        self.assertEqual(sub.estado, 'ACTIVA')
