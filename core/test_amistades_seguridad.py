"""Hotfix C4: las rutas genéricas de /api/amistades/ son solo lectura.

Antes era un ModelViewSet completo: un POST/PATCH permitía crear una amistad ya
ACEPTADA o aceptar una solicitud ajena, saltándose el tope de 5 amigos y la
confirmación del receptor.
"""
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Amistad, Usuario


def usuario(nombre, **kw):
    return Usuario.objects.create_user(
        username=nombre, email=f'{nombre}@t.cl', password='Clave12345', **kw)


class AmistadesRutasGenericasTests(TestCase):
    def setUp(self):
        self.ana = usuario('ana')
        self.beto = usuario('beto')
        self.carla = usuario('carla')
        self.client = APIClient()
        self.client.force_authenticate(self.ana)
        self.pendiente = Amistad.objects.create(
            solicitante=self.beto, receptor=self.ana, estado='PENDIENTE')

    def test_post_generico_ya_no_crea_amistades(self):
        r = self.client.post('/api/amistades/', {
            'receptor': self.carla.id, 'estado': 'ACEPTADA'}, format='json')
        self.assertEqual(r.status_code, 405)
        self.assertFalse(Amistad.objects.filter(solicitante=self.ana, receptor=self.carla).exists())

    def test_patch_no_permite_aceptar_una_solicitud(self):
        r = self.client.patch(f'/api/amistades/{self.pendiente.id}/', {'estado': 'ACEPTADA'}, format='json')
        self.assertEqual(r.status_code, 405)
        self.pendiente.refresh_from_db()
        self.assertEqual(self.pendiente.estado, 'PENDIENTE')

    def test_el_solicitante_no_puede_autoaceptar_su_solicitud(self):
        propia = Amistad.objects.create(solicitante=self.ana, receptor=self.carla, estado='PENDIENTE')
        r = self.client.patch(f'/api/amistades/{propia.id}/', {'estado': 'ACEPTADA'}, format='json')
        self.assertEqual(r.status_code, 405)
        propia.refresh_from_db()
        self.assertEqual(propia.estado, 'PENDIENTE')

    def test_put_y_delete_genericos_no_estan_permitidos(self):
        url = f'/api/amistades/{self.pendiente.id}/'
        self.assertEqual(self.client.put(url, {'estado': 'ACEPTADA'}, format='json').status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)
        self.assertTrue(Amistad.objects.filter(pk=self.pendiente.pk).exists())

    def test_lectura_sigue_funcionando_y_solo_muestra_las_propias(self):
        ajena = Amistad.objects.create(solicitante=self.beto, receptor=self.carla)
        r = self.client.get('/api/amistades/')
        self.assertEqual(r.status_code, 200)
        ids = [a['id'] for a in (r.data['results'] if isinstance(r.data, dict) else r.data)]
        self.assertIn(self.pendiente.id, ids)
        self.assertNotIn(ajena.id, ids)
        self.assertEqual(self.client.get(f'/api/amistades/{self.pendiente.id}/').status_code, 200)
        self.assertEqual(self.client.get(f'/api/amistades/{ajena.id}/').status_code, 404)

    def test_requiere_autenticacion(self):
        anon = APIClient()
        self.assertEqual(anon.get('/api/amistades/').status_code, 401)
        self.assertEqual(anon.post('/api/amistades/', {}, format='json').status_code, 401)


class AmistadesFlujoOficialTests(TestCase):
    """Los endpoints que usa la app deben seguir funcionando igual."""

    def setUp(self):
        self.ana = usuario('ana')
        self.beto = usuario('beto')
        self.client = APIClient()
        self.client.force_authenticate(self.ana)

    def test_enviar_solicitud_y_que_el_receptor_acepte(self):
        r = self.client.post('/api/amistades/enviar-solicitud/', {'username': 'beto'}, format='json')
        self.assertEqual(r.status_code, 201)
        solicitud = Amistad.objects.get(solicitante=self.ana, receptor=self.beto)
        self.assertEqual(solicitud.estado, 'PENDIENTE')

        # el solicitante no puede aceptar su propia solicitud
        r = self.client.post('/api/amistades/responder-solicitud/',
                             {'solicitud_id': solicitud.id, 'accion': 'ACEPTAR'}, format='json')
        self.assertEqual(r.status_code, 404)

        beto = APIClient()
        beto.force_authenticate(self.beto)
        r = beto.post('/api/amistades/responder-solicitud/',
                      {'solicitud_id': solicitud.id, 'accion': 'ACEPTAR'}, format='json')
        self.assertEqual(r.status_code, 200)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'ACEPTADA')

        r = self.client.get('/api/amistades/mis-amigos/')
        self.assertEqual([a['username'] for a in r.data], ['beto'])

    def test_eliminar_amigo(self):
        Amistad.objects.create(solicitante=self.ana, receptor=self.beto, estado='ACEPTADA')
        r = self.client.post('/api/amistades/eliminar-amigo/', {'amigo_id': self.beto.id}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Amistad.objects.exists())

    def test_tope_de_5_amigos_sigue_vigente_para_plan_gratuito(self):
        for i in range(5):
            otro = usuario(f'amigo{i}')
            Amistad.objects.create(solicitante=self.ana, receptor=otro, estado='ACEPTADA')
        r = self.client.post('/api/amistades/enviar-solicitud/', {'username': 'beto'}, format='json')
        self.assertEqual(r.status_code, 403)
