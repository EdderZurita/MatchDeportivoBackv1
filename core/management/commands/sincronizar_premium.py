from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from core.models import Suscripcion
from core.premium_sync import sincronizar_con_flow


class Command(BaseCommand):
    help = ('Sincroniza con Flow las suscripciones Premium activas cuyo período guardado ya terminó '
            '(renueva a quien pagó, quita Premium a quien dejó de pagar). Se puede ejecutar a mano '
            'o desde un programador de tareas; no depende de PREMIUM_SYNC_ENABLED.')

    def add_arguments(self, parser):
        parser.add_argument('--todas', action='store_true',
                            help='Incluye también las activas cuyo período aún no termina.')

    def handle(self, *args, **opciones):
        ahora = timezone.now()
        subs = Suscripcion.objects.filter(estado='ACTIVA', usuario__es_premium=True).select_related('usuario')
        if not opciones['todas']:
            subs = subs.filter(Q(fecha_vencimiento__lte=ahora) | Q(fecha_vencimiento__isnull=True))
        resumen = {}
        for sub in subs:
            codigo = sincronizar_con_flow(sub.usuario, sub, ahora)
            resumen[codigo] = resumen.get(codigo, 0) + 1
        self.stdout.write(f'Suscripciones revisadas: {sum(resumen.values())} -> {resumen or "nada que hacer"}')
