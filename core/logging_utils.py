"""
Utilidades de logging: request-id por peticion y formato opcional JSON.

El request-id viaja en un ContextVar, asi cualquier logger (views, servicios,
Flow, Firebase) puede incluirlo sin recibir el request como parametro.
"""
import json
import logging
from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar('request_id', default='-')


def get_request_id() -> str:
    return request_id_ctx.get()


class RequestIdFilter(logging.Filter):
    """Agrega `record.request_id` a todos los registros."""

    def filter(self, record):
        record.request_id = request_id_ctx.get()
        return True


class JsonFormatter(logging.Formatter):
    """Una linea JSON por registro (util para Render/Datadog/Loki)."""

    def format(self, record):
        data = {
            'ts': self.formatTime(record, '%Y-%m-%dT%H:%M:%S%z'),
            'level': record.levelname,
            'logger': record.name,
            'request_id': getattr(record, 'request_id', '-'),
            'msg': record.getMessage(),
        }
        if record.exc_info:
            data['exc'] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)
