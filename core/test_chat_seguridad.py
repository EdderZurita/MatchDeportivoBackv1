"""Hotfix C5: control de acceso del chat (actividades y mensajes privados)."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Actividad, Amistad, Bloqueo, Inscripcion, Mensaje, Usuario


def usuario(nombre, **kw):
    return Usuario.objects.create_user(username=nombre, email=f'{nombre}@t.cl', password='Clave12345', **kw)


class ChatActividadTests(TestCase):
    def setUp(self):
        self.org = usuario('org')
        self.miembro = usuario('miembro')
        self.pendiente = usuario('pendiente')
        self.intruso = usuario('intruso')
        self.act = Actividad.objects.create(
            organizador=self.org, titulo='P', deporte='FUTBOL', direccion='d', comuna='c',
            latitud='-33.4', longitud='-70.6', cupos_totales=5,
            fecha_hora=timezone.now() + timedelta(days=2))
        Inscripcion.objects.create(actividad=self.act, usuario=self.miembro, estado='CONFIRMADA')
        Inscripcion.objects.create(actividad=self.act, usuario=self.pendiente, estado='PENDIENTE')
        self.msg = Mensaje.objects.create(actividad=self.act, usuario=self.org, contenido='Hola equipo')
        self.client = APIClient()

    def _leer(self, user):
        self.client.force_authenticate(user)
        return self.client.get('/api/mensajes/', {'actividad_id': self.act.id})

    def _escribir(self, user, texto='mensaje'):
        self.client.force_authenticate(user)
        return self.client.post('/api/mensajes/', {'actividad': self.act.id, 'contenido': texto}, format='json')

    def test_organizador_y_confirmados_leen_el_chat(self):
        for u in (self.org, self.miembro):
            r = self._leer(u)
            self.assertEqual(r.status_code, 200, u.username)
            self.assertEqual(len(r.data['results']), 1)

    def test_intruso_y_pendiente_no_pueden_leer_el_chat(self):
        for u in (self.intruso, self.pendiente):
            self.assertEqual(self._leer(u).status_code, 403, u.username)

    def test_staff_puede_leer_el_chat(self):
        admin = usuario('admin', is_staff=True)
        self.assertEqual(self._leer(admin).status_code, 200)

    def test_actividad_inexistente_devuelve_lista_vacia(self):
        self.client.force_authenticate(self.intruso)
        r = self.client.get('/api/mensajes/', {'actividad_id': 99999})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['results'], [])

    def test_actividad_id_no_numerico_no_rompe(self):
        self.client.force_authenticate(self.intruso)
        r = self.client.get('/api/mensajes/', {'actividad_id': 'abc'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['results'], [])

    def test_confirmado_puede_escribir_y_queda_como_autor(self):
        r = self._escribir(self.miembro)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Mensaje.objects.latest('id').usuario, self.miembro)

    def test_intruso_y_pendiente_no_pueden_escribir(self):
        antes = Mensaje.objects.count()
        for u in (self.intruso, self.pendiente):
            self.assertEqual(self._escribir(u).status_code, 403, u.username)
        self.assertEqual(Mensaje.objects.count(), antes)

    def test_no_se_puede_enviar_sin_destino_ni_con_dos_destinos(self):
        self.client.force_authenticate(self.org)
        r = self.client.post('/api/mensajes/', {'contenido': 'x'}, format='json')
        self.assertEqual(r.status_code, 400)
        r = self.client.post('/api/mensajes/', {
            'contenido': 'x', 'actividad': self.act.id, 'receptor': self.miembro.id}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_mensajes_no_se_editan(self):
        self.client.force_authenticate(self.org)
        for metodo in (self.client.patch, self.client.put):
            r = metodo(f'/api/mensajes/{self.msg.id}/', {'contenido': 'cambiado'}, format='json')
            self.assertEqual(r.status_code, 405)
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.contenido, 'Hola equipo')

    def test_solo_el_autor_borra_su_mensaje(self):
        # el receptor de un mensaje privado NO puede borrarlo
        privado = Mensaje.objects.create(usuario=self.org, receptor=self.miembro, contenido='privado')
        self.client.force_authenticate(self.miembro)
        self.assertEqual(self.client.delete(f'/api/mensajes/{privado.id}/').status_code, 403)
        self.assertTrue(Mensaje.objects.filter(pk=privado.id).exists())
        self.client.force_authenticate(self.org)
        self.assertEqual(self.client.delete(f'/api/mensajes/{privado.id}/').status_code, 204)

    def test_intruso_no_ve_ni_borra_mensajes_ajenos_por_id(self):
        self.client.force_authenticate(self.intruso)
        self.assertEqual(self.client.get(f'/api/mensajes/{self.msg.id}/').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/mensajes/{self.msg.id}/').status_code, 404)


class ChatPrivadoTests(TestCase):
    def setUp(self):
        self.a = usuario('ana')
        self.b = usuario('beto')
        self.c = usuario('carla')
        self.client = APIClient()
        self.client.force_authenticate(self.a)

    def _enviar(self, receptor, texto='hola'):
        return self.client.post('/api/mensajes/', {'receptor': receptor.id, 'contenido': texto}, format='json')

    def test_no_se_puede_escribir_a_quien_no_es_amigo(self):
        self.assertEqual(self._enviar(self.b).status_code, 403)
        self.assertEqual(Mensaje.objects.count(), 0)

    def test_amistad_pendiente_no_basta(self):
        Amistad.objects.create(solicitante=self.a, receptor=self.b, estado='PENDIENTE')
        self.assertEqual(self._enviar(self.b).status_code, 403)

    def test_amigos_pueden_escribirse_en_ambos_sentidos(self):
        Amistad.objects.create(solicitante=self.b, receptor=self.a, estado='ACEPTADA')
        self.assertEqual(self._enviar(self.b).status_code, 201)
        self.client.force_authenticate(self.b)
        self.assertEqual(self._enviar(self.a, 'respuesta').status_code, 201)

    def test_bloqueo_impide_escribir(self):
        Amistad.objects.create(solicitante=self.a, receptor=self.b, estado='ACEPTADA')
        Bloqueo.objects.create(bloqueador=self.b, bloqueado=self.a)
        self.assertEqual(self._enviar(self.b).status_code, 403)

    def test_no_se_puede_escribir_a_si_mismo(self):
        self.assertEqual(self._enviar(self.a).status_code, 400)

    def test_conversacion_privada_solo_la_ven_sus_dos_participantes(self):
        Amistad.objects.create(solicitante=self.a, receptor=self.b, estado='ACEPTADA')
        self._enviar(self.b, 'secreto')
        self.client.force_authenticate(self.c)
        r = self.client.get('/api/mensajes/', {'amigo_id': self.a.id})
        self.assertEqual(r.data['results'], [])
        self.client.force_authenticate(self.b)
        r = self.client.get('/api/mensajes/', {'amigo_id': self.a.id})
        self.assertEqual(len(r.data['results']), 1)

    def test_mensaje_sospechoso_se_marca_y_genera_reporte(self):
        """Comportamiento existente que no debe perderse con el hotfix."""
        from core.views import PALABRAS_RIESGO
        Amistad.objects.create(solicitante=self.a, receptor=self.b, estado='ACEPTADA')
        palabra = next(iter(PALABRAS_RIESGO))
        self.assertEqual(self._enviar(self.b, f'esto es {palabra}').status_code, 201)
        self.assertTrue(Mensaje.objects.get().sospechoso)
