from rest_framework_simplejwt.authentication import JWTAuthentication

from .utils import expirar_premium_si_corresponde


class JWTAuthenticationConPremiumVigente(JWTAuthentication):
    """JWT normal, más: si el usuario canceló Premium y su período pagado ya terminó,
    se le quita Premium en ese momento (RF-24). Solo consulta la suscripción de
    quienes aún figuran como Premium."""

    def authenticate(self, request):
        resultado = super().authenticate(request)
        if resultado is not None:
            user, _token = resultado
            if user.es_premium:
                expirar_premium_si_corresponde(user)
        return resultado
