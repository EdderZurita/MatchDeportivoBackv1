"""
Tests de caracterizacion: fijan el comportamiento ACTUAL de la API (contrato)
antes de refactorizar. Si un refactor rompe uno, el cambio de comportamiento
debe ser deliberado y documentado.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Actividad, CodigoRecuperacion, Inscripcion, Usuario


def crear_usuario(username, premium=False):
    return Usuario.objects.create_user(
        username=username, email=f'{username}@test.cl', password='Clave12345', es_premium=premium
    )


def datos_actividad(**extra):
    d = {
        'titulo': 'Pichanga', 'deporte': 'FUTBOL', 'direccion': 'Cancha 1', 'comuna': 'La Pintana',
        'latitud': '-33.5800', 'longitud': '-70.6300', 'cupos_totales': 4,
        'fecha_hora': (timezone.now() + timedelta(days=2)).isoformat(),
    }
    d.update(extra)
    return d


def crear_actividad(organizador, **extra):
    d = dict(titulo='Partido', deporte='FUTBOL', direccion='Cancha', comuna='Santiago',
             latitud='-33.4', longitud='-70.6', cupos_totales=4,
             fecha_hora=timezone.now() + timedelta(days=2))
    d.update(extra)
    return Actividad.objects.create(organizador=organizador, **d)


class RegistroYLoginTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_registro_crea_usuario_sin_premium_y_con_password_hasheada(self):
        r = self.client.post('/api/auth/registro/', {
            'username': 'nuevo', 'email': 'nuevo@test.cl', 'password': 'Clave12345'}, format='json')
        self.assertEqual(r.status_code, 201)
        u = Usuario.objects.get(username='nuevo')
        self.assertFalse(u.es_premium)
        self.assertFalse(u.is_staff)
        self.assertNotEqual(u.password, 'Clave12345')
        self.assertTrue(u.check_password('Clave12345'))
        self.assertNotIn('password', r.data)

    def test_registro_no_permite_autoasignarse_premium_ni_staff(self):
        self.client.post('/api/auth/registro/', {
            'username': 'listo', 'email': 'l@test.cl', 'password': 'Clave12345',
            'es_premium': True, 'is_staff': True, 'is_superuser': True}, format='json')
        u = Usuario.objects.get(username='listo')
        self.assertFalse(u.es_premium or u.is_staff or u.is_superuser)

    def test_registro_rechaza_password_corta(self):
        r = self.client.post('/api/auth/registro/', {
            'username': 'x', 'email': 'x@test.cl', 'password': '123'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_registro_rechaza_username_duplicado(self):
        crear_usuario('repetido')
        r = self.client.post('/api/auth/registro/', {
            'username': 'repetido', 'email': 'otro@test.cl', 'password': 'Clave12345'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_login_devuelve_access_y_refresh(self):
        crear_usuario('ana')
        r = self.client.post('/api/auth/token/', {'username': 'ana', 'password': 'Clave12345'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertIn('access', r.data)
        self.assertIn('refresh', r.data)

    def test_login_con_password_incorrecta_da_401(self):
        crear_usuario('ana')
        r = self.client.post('/api/auth/token/', {'username': 'ana', 'password': 'mala'}, format='json')
        self.assertEqual(r.status_code, 401)

    def test_refresh_entrega_nuevo_access(self):
        crear_usuario('ana')
        tok = self.client.post('/api/auth/token/', {'username': 'ana', 'password': 'Clave12345'}, format='json').data
        r = self.client.post('/api/auth/token/refresh/', {'refresh': tok['refresh']}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertIn('access', r.data)

    def test_token_permite_acceder_a_endpoint_protegido(self):
        crear_usuario('ana')
        tok = self.client.post('/api/auth/token/', {'username': 'ana', 'password': 'Clave12345'}, format='json').data
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tok['access']}")
        self.assertEqual(self.client.get('/api/usuarios/mi_perfil/').status_code, 200)

    def test_endpoints_protegidos_exigen_token(self):
        for url in ('/api/usuarios/', '/api/inscripciones/', '/api/amistades/', '/api/mensajes/',
                    '/api/pagos/mi-suscripcion/'):
            self.assertEqual(self.client.get(url).status_code, 401, url)


class ActividadesTests(TestCase):
    def setUp(self):
        self.org = crear_usuario('org')
        self.otro = crear_usuario('otro')
        self.client = APIClient()
        self.client.force_authenticate(self.org)

    def test_crear_actividad_asigna_organizador(self):
        r = self.client.post('/api/actividades/', datos_actividad(), format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Actividad.objects.get().organizador, self.org)
        self.assertEqual(r.data['estado'], 'ABIERTA')

    def test_no_se_puede_elegir_organizador_en_el_body(self):
        self.client.post('/api/actividades/', datos_actividad(organizador=self.otro.id), format='json')
        self.assertEqual(Actividad.objects.get().organizador, self.org)

    def test_fecha_pasada_se_rechaza(self):
        pasada = (timezone.now() - timedelta(days=1)).isoformat()
        r = self.client.post('/api/actividades/', datos_actividad(fecha_hora=pasada), format='json')
        self.assertEqual(r.status_code, 400)

    def test_plan_gratis_limita_a_una_actividad_por_dia(self):
        self.assertEqual(self.client.post('/api/actividades/', datos_actividad(), format='json').status_code, 201)
        r = self.client.post('/api/actividades/', datos_actividad(titulo='Otra'), format='json')
        self.assertEqual(r.status_code, 403)

    def test_premium_puede_crear_varias_por_dia(self):
        prem = crear_usuario('prem', premium=True)
        self.client.force_authenticate(prem)
        for i in range(3):
            r = self.client.post('/api/actividades/', datos_actividad(titulo=f'P{i}'), format='json')
            self.assertEqual(r.status_code, 201)

    def test_listado_es_publico_y_filtra_por_deporte(self):
        crear_actividad(self.org, titulo='F', deporte='FUTBOL')
        crear_actividad(self.org, titulo='T', deporte='TENIS')
        anon = APIClient()
        r = anon.get('/api/actividades/', {'deporte': 'TENIS'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual([a['titulo'] for a in r.data['results']], ['T'])

    def test_crear_sin_login_da_401(self):
        r = APIClient().post('/api/actividades/', datos_actividad(), format='json')
        self.assertEqual(r.status_code, 401)

    def test_otro_usuario_no_puede_editar(self):
        a = crear_actividad(self.org)
        self.client.force_authenticate(self.otro)
        r = self.client.patch(f'/api/actividades/{a.id}/', {'titulo': 'Hack'}, format='json')
        self.assertEqual(r.status_code, 403)
        a.refresh_from_db()
        self.assertEqual(a.titulo, 'Partido')

    def test_organizador_si_puede_editar(self):
        a = crear_actividad(self.org)
        r = self.client.patch(f'/api/actividades/{a.id}/', {'titulo': 'Nuevo'}, format='json')
        self.assertEqual(r.status_code, 200)
        a.refresh_from_db()
        self.assertEqual(a.titulo, 'Nuevo')

    def test_actividad_pasada_se_finaliza_sola_al_listar(self):
        a = crear_actividad(self.org, fecha_hora=timezone.now() - timedelta(hours=1))
        self.client.get('/api/actividades/')
        a.refresh_from_db()
        self.assertEqual(a.estado, 'FINALIZADA')

    def test_cupos_disponibles_descuenta_solo_confirmados(self):
        a = crear_actividad(self.org, cupos_totales=3)
        Inscripcion.objects.create(actividad=a, usuario=self.otro, estado='CONFIRMADA')
        Inscripcion.objects.create(actividad=a, usuario=crear_usuario('p'), estado='PENDIENTE')
        self.assertEqual(a.cupos_disponibles, 2)


class InscripcionesTests(TestCase):
    def setUp(self):
        self.org = crear_usuario('org')
        self.user = crear_usuario('user')
        self.act = crear_actividad(self.org, cupos_totales=1)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def url(self, accion, pk=None):
        return f'/api/actividades/{pk or self.act.id}/{accion}/'

    def test_inscribirse_crea_solicitud_pendiente(self):
        r = self.client.post(self.url('inscribirse'))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['estado'], 'PENDIENTE')
        self.assertEqual(Inscripcion.objects.get().estado, 'PENDIENTE')

    def test_inscribirse_dos_veces_se_rechaza(self):
        self.client.post(self.url('inscribirse'))
        self.assertEqual(self.client.post(self.url('inscribirse')).status_code, 400)
        self.assertEqual(Inscripcion.objects.count(), 1)

    def test_organizador_no_puede_inscribirse_en_su_partido(self):
        self.client.force_authenticate(self.org)
        self.assertEqual(self.client.post(self.url('inscribirse')).status_code, 400)

    def test_sin_cupos_se_rechaza(self):
        Inscripcion.objects.create(actividad=self.act, usuario=crear_usuario('c'), estado='CONFIRMADA')
        self.assertEqual(self.client.post(self.url('inscribirse')).status_code, 400)

    def test_actividad_cerrada_no_acepta_inscripciones(self):
        self.act.estado = 'CANCELADA'
        self.act.save()
        self.assertEqual(self.client.post(self.url('inscribirse')).status_code, 400)

    def test_expulsado_no_puede_volver(self):
        Inscripcion.objects.create(actividad=self.act, usuario=self.user, estado='EXPULSADA')
        self.assertEqual(self.client.post(self.url('inscribirse')).status_code, 400)

    def test_actividad_inexistente_da_404(self):
        self.assertEqual(self.client.post(self.url('inscribirse', pk=99999)).status_code, 404)

    def test_abandonar_cancela_la_inscripcion(self):
        self.client.post(self.url('inscribirse'))
        r = self.client.post(self.url('abandonar'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Inscripcion.objects.get().estado, 'CANCELADA')

    def test_abandonar_sin_inscripcion_da_400(self):
        self.assertEqual(self.client.post(self.url('abandonar')).status_code, 400)

    def test_abandonar_confirmada_reabre_actividad_llena(self):
        Inscripcion.objects.create(actividad=self.act, usuario=self.user, estado='CONFIRMADA')
        self.act.estado = 'LLENA'
        self.act.save()
        self.client.post(self.url('abandonar'))
        self.act.refresh_from_db()
        self.assertEqual(self.act.estado, 'ABIERTA')

    def test_reinscribirse_tras_abandonar_vuelve_a_pendiente(self):
        self.client.post(self.url('inscribirse'))
        self.client.post(self.url('abandonar'))
        r = self.client.post(self.url('inscribirse'))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Inscripcion.objects.get().estado, 'PENDIENTE')


@patch('core.views.urllib.request.urlopen')
class RecuperacionPasswordTests(TestCase):
    """OTP por correo (Brevo mockeado)."""

    def setUp(self):
        cache.clear()
        self.user = crear_usuario('ana')
        self.client = APIClient()

    def _solicitar(self, email='ana@test.cl'):
        return self.client.post('/api/auth/solicitar-recuperacion/', {'email': email}, format='json')

    def _restablecer(self, codigo, pw='NuevaClave99', email='ana@test.cl'):
        return self.client.post('/api/auth/restablecer-password/',
                                {'email': email, 'codigo': codigo, 'nueva_password': pw}, format='json')

    def test_solicitar_responde_igual_exista_o_no_el_correo(self, urlopen):
        a = self._solicitar('ana@test.cl')
        b = self._solicitar('noexiste@test.cl')
        self.assertEqual(a.status_code, 200)
        self.assertEqual(a.data, b.data)

    def test_solicitar_genera_codigo_de_6_digitos(self, urlopen):
        self._solicitar()
        cod = CodigoRecuperacion.objects.get(usuario=self.user)
        self.assertRegex(cod.codigo, r'^\d{6}$')
        self.assertFalse(cod.usado)

    def test_nuevo_codigo_invalida_los_anteriores(self, urlopen):
        self._solicitar()
        self._solicitar()
        self.assertEqual(CodigoRecuperacion.objects.filter(usuario=self.user, usado=False).count(), 1)
        self.assertEqual(CodigoRecuperacion.objects.filter(usuario=self.user, usado=True).count(), 1)

    def test_codigo_valido_cambia_la_password_y_no_se_reutiliza(self, urlopen):
        self._solicitar()
        cod = CodigoRecuperacion.objects.get(usuario=self.user).codigo
        self.assertEqual(self._restablecer(cod).status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NuevaClave99'))
        self.assertEqual(self._restablecer(cod, pw='OtraClave123').status_code, 400)

    def test_codigo_incorrecto_se_rechaza(self, urlopen):
        self._solicitar()
        self.assertEqual(self._restablecer('000000').status_code, 400)

    def test_codigo_expirado_se_rechaza(self, urlopen):
        self._solicitar()
        cod = CodigoRecuperacion.objects.get(usuario=self.user)
        CodigoRecuperacion.objects.filter(pk=cod.pk).update(creado_en=timezone.now() - timedelta(minutes=16))
        self.assertEqual(self._restablecer(cod.codigo).status_code, 400)

    def test_password_corta_se_rechaza(self, urlopen):
        self._solicitar()
        cod = CodigoRecuperacion.objects.get(usuario=self.user).codigo
        self.assertEqual(self._restablecer(cod, pw='123').status_code, 400)

    def test_tras_5_intentos_fallidos_se_bloquea_aun_con_codigo_correcto(self, urlopen):
        self._solicitar()
        cod = CodigoRecuperacion.objects.get(usuario=self.user).codigo
        for _ in range(5):
            self.assertEqual(self._restablecer('000000').status_code, 400)
        self.assertEqual(self._restablecer(cod).status_code, 429)
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('NuevaClave99'))

    def test_solicitar_tiene_limite_por_hora(self, urlopen):
        codigos = [self._solicitar().status_code for _ in range(6)]
        self.assertEqual(codigos[:5], [200] * 5)
        self.assertEqual(codigos[5], 429)
