from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from core.flow_service import FlowService
from core.models import Suscripcion
from core.premium_sync import sincronizar_con_flow

# Campos de subscription/get que importan para decidir; no incluye datos del cliente ni claves.
CAMPOS_DIAGNOSTICO = ('status', 'morose', 'cancel_at_period_end', 'cancel_at', 'period_start',
                      'period_end', 'next_invoice_date', 'periods_number', 'days_until_due')


class Command(BaseCommand):
    help = ('Sincroniza con Flow las suscripciones Premium activas cuyo período guardado ya terminó '
            '(renueva a quien pagó, quita Premium a quien dejó de pagar). Se puede ejecutar a mano '
            'o desde un programador de tareas; no depende de PREMIUM_SYNC_ENABLED.')

    def add_arguments(self, parser):
        parser.add_argument('--todas', action='store_true',
                            help='Incluye también las activas cuyo período aún no termina.')
        parser.add_argument('--diagnostico', action='store_true',
                            help='Solo muestra lo que Flow responde para cada suscripción activa '
                                 '(status, morose, period_end...). No modifica nada.')

    def handle(self, *args, **opciones):
        if opciones['diagnostico']:
            return self._diagnostico()
        ahora = timezone.now()
        subs = Suscripcion.objects.filter(estado='ACTIVA', usuario__es_premium=True).select_related('usuario')
        if not opciones['todas']:
            subs = subs.filter(Q(fecha_vencimiento__lte=ahora) | Q(fecha_vencimiento__isnull=True))
        resumen = {}
        for sub in subs:
            codigo = sincronizar_con_flow(sub.usuario, sub, ahora)
            resumen[codigo] = resumen.get(codigo, 0) + 1
        self.stdout.write(f'Suscripciones revisadas: {sum(resumen.values())} -> {resumen or "nada que hacer"}')

    def _diagnostico(self):
        subs = Suscripcion.objects.exclude(flow_subscription_id__isnull=True).exclude(flow_subscription_id='')
        if not subs.exists():
            self.stdout.write('No hay suscripciones con id de Flow.')
        for sub in subs.select_related('usuario'):
            datos = FlowService.obtener_suscripcion(sub.flow_subscription_id)
            self.stdout.write(f'usuario={sub.usuario_id} estado_local={sub.estado} '
                              f'vence_local={sub.fecha_vencimiento}')
            if isinstance(datos, dict) and 'code' not in datos:
                self.stdout.write('  Flow: ' + ', '.join(f'{c}={datos.get(c)!r}' for c in CAMPOS_DIAGNOSTICO))
            else:
                self.stdout.write(f'  Flow respondió con error: {datos!r}')
