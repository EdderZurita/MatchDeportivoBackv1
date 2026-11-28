import hmac
import hashlib
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT_SEGUNDOS = 15


class FlowService:
    @staticmethod
    def _firmar(params: dict) -> str:
        """
        Genera la firma HMAC-SHA256 requerida por Flow:
        1. Ordena los parámetros alfabéticamente por su clave.
        2. Concatena los pares clave-valor (clave1valor1clave2valor2...).
        3. Aplica HMAC-SHA256 usando el secret key.
        """
        secret = settings.FLOW_SECRET_KEY.strip()
        claves_ordenadas = sorted(k for k in params.keys() if k != 's')
        cadena_a_firmar = "".join(f"{k}{params[k]}" for k in claves_ordenadas)
        return hmac.new(
            secret.encode('utf-8'),
            cadena_a_firmar.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

    @classmethod
    def _enviar_peticion(cls, endpoint: str, params: dict, metodo: str = "POST") -> dict:
        """Firma automáticamente los parámetros y envía la petición a Flow."""
        params['apiKey'] = settings.FLOW_API_KEY.strip()
        params['s'] = cls._firmar(params)
        url = f"{settings.FLOW_API_URL.rstrip('/')}/{endpoint.lstrip('/')}"

        try:
            if metodo.upper() == "POST":
                response = requests.post(url, data=params, timeout=TIMEOUT_SEGUNDOS)
            else:
                response = requests.get(url, params=params, timeout=TIMEOUT_SEGUNDOS)
        except requests.RequestException as exc:
            # Sin timeout, un Flow lento dejaba colgado un worker de gunicorn.
            logger.warning("flow %s sin respuesta: %s", endpoint, exc.__class__.__name__)
            return {"code": -1, "message": "Flow no respondió a tiempo.", "error_conexion": True}

        try:
            return response.json()
        except Exception:
            return {"status": response.status_code, "text": response.text}

    @classmethod
    def crear_cliente(cls, usuario) -> dict:
        """Registra al usuario como cliente dentro de Flow con externalId."""
        # Asegurar email y nombre válidos para Flow Sandbox
        email = usuario.email.strip() if usuario.email else f"user_{usuario.id}@matchdeportivo.cl"
        nombre = usuario.get_full_name().strip() if usuario.get_full_name() else usuario.username

        params = {
            'name': nombre,
            'email': email,
            'externalId': str(usuario.id),
        }
        return cls._enviar_peticion('customer/create', params, metodo='POST')

    @classmethod
    def iniciar_registro_tarjeta(cls, customer_id: str, url_retorno: str) -> dict:
        """
        Genera el link de Flow donde el usuario ingresa los datos de su tarjeta
        para tokenizarla (RF-22).
        """
        params = {
            'customerId': customer_id,
            'url_return': url_retorno,
        }
        return cls._enviar_peticion('customer/register', params, metodo='POST')

    @classmethod
    def consultar_registro_tarjeta(cls, token: str) -> dict:
        """Consulta el estado del registro de la tarjeta tras el retorno del usuario."""
        params = {'token': token}
        return cls._enviar_peticion('customer/getRegisterStatus', params, metodo='GET')

    @classmethod
    def crear_suscripcion(cls, customer_id: str, plan_id: str) -> dict:
        """Asocia el cliente con tarjeta registrada al plan recurrente."""
        params = {
            'customerId': customer_id,
            'planId': plan_id,
        }
        return cls._enviar_peticion('subscription/create', params, metodo='POST')

    @classmethod
    def cancelar_suscripcion(cls, subscription_id: str, al_final_del_periodo: bool = True) -> dict:
        """Cancela la suscripción en Flow para detener cobros futuros (RF-24).

        Por defecto la cancelación rige al final del ciclo ya pagado (at_period_end=1),
        como en cualquier app con suscripción; con False se cancela de inmediato.
        """
        params = {
            'subscriptionId': subscription_id,
            'at_period_end': 1 if al_final_del_periodo else 0,
        }
        return cls._enviar_peticion('subscription/cancel', params, metodo='POST')

    @classmethod
    def obtener_suscripcion(cls, subscription_id: str) -> dict:
        """Consulta el estado real de la suscripción en Flow (status, morose, period_end...)."""
        params = {'subscriptionId': subscription_id}
        return cls._enviar_peticion('subscription/get', params, metodo='GET')
