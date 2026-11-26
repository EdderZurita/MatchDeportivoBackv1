"""Hotfix C6: datos privados de usuarios (email, telefono, is_staff, is_superuser)."""
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Usuario

PRIVADOS = ('email', 'telefono', 'is_staff', 'is_superuser')


def usuario(nombre, **kw):
    return Usuario.objects.create_user(
        username=nombre, email=f'{nombre}@t.cl', password='Clave12345', telefono='+56911111111', **kw)


class PrivacidadUsuariosTests(TestCase):
    def setUp(self):
        self.ana = usuario('ana')
        self.beto = usuario('beto')
        self.admin = usuario('admin', is_staff=True)
        self.client = APIClient()

    def test_listado_no_expone_campos_privados_a_otros_usuarios(self):
        self.client.force_authenticate(self.ana)
        r = self.client.get('/api/usuarios/')
        self.assertEqual(r.status_code, 200)
        for u in r.data['results']:
            if u['username'] != 'ana':
                for campo in PRIVADOS:
                    self.assertNotIn(campo, u, f"{u['username']} expone {campo}")

    def test_detalle_de_otro_usuario_no_expone_campos_privados(self):
        self.client.force_authenticate(self.ana)
        r = self.client.get(f'/api/usuarios/{self.beto.id}/')
        self.assertEqual(r.status_code, 200)
        for campo in PRIVADOS:
            self.assertNotIn(campo, r.data)
        self.assertEqual(r.data['username'], 'beto')

    def test_busqueda_no_expone_campos_privados(self):
        self.client.force_authenticate(self.ana)
        r = self.client.get('/api/usuarios/', {'search': 'beto'})
        self.assertEqual(len(r.data['results']), 1)
        self.assertNotIn('email', r.data['results'][0])

    def test_el_usuario_ve_sus_propios_datos_completos(self):
        self.client.force_authenticate(self.ana)
        r = self.client.get('/api/usuarios/mi_perfil/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['email'], 'ana@t.cl')
        self.assertEqual(r.data['telefono'], '+56911111111')
        self.assertIn('is_staff', r.data)

    def test_el_admin_ve_todo_de_todos(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get('/api/usuarios/')
        for u in r.data['results']:
            for campo in PRIVADOS:
                self.assertIn(campo, u)

    def test_datos_publicos_siguen_disponibles(self):
        self.client.force_authenticate(self.ana)
        r = self.client.get(f'/api/usuarios/{self.beto.id}/')
        for campo in ('id', 'username', 'comuna', 'deporte_favorito', 'nivel_habilidad',
                      'es_premium', 'reputacion_promedio'):
            self.assertIn(campo, r.data)

    def test_no_se_puede_autoasignar_staff_ni_premium_por_perfil(self):
        self.client.force_authenticate(self.ana)
        self.client.patch('/api/usuarios/mi_perfil/',
                          {'is_staff': True, 'is_superuser': True, 'es_premium': True}, format='json')
        self.ana.refresh_from_db()
        self.assertFalse(self.ana.is_staff or self.ana.is_superuser or self.ana.es_premium)

    def test_sin_login_no_hay_acceso(self):
        self.assertEqual(self.client.get('/api/usuarios/').status_code, 401)
