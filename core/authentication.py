from rest_framework_simplejwt.authentication import JWTAuthentication

from .premium_sync import verificar_premium


class JWTAuthenticationConPremiumVigente(JWTAuthentication):
    """JWT normal, más: mantiene el Premium al día en cada request autenticado (RF-24).

    - Cancelada y con el período pagado terminado -> se quita Premium.
    - Activa con el período guardado terminado    -> se consulta Flow (si la sincronización
      está habilitada) para extender, o quitar Premium si dejó de pagar.
    Solo actúa sobre quienes aún figuran como Premium.
    """

    def authenticate(self, request):
        resultado = super().authenticate(request)
        if resultado is not None:
            user, _token = resultado
            verificar_premium(user)
        return resultado
