# Pruebas de pagos con Flow (sandbox)

Entorno de pruebas: `FLOW_API_URL=https://sandbox.flow.cl/api` con las claves del **sandbox**
(nunca las de producción) y `FLOW_PLAN_ID=premium_mensual`. Nunca se cobra dinero real.

## Tarjetas de prueba (ambiente de integración de Transbank Oneclick)

Flow sandbox registra la tarjeta mediante Webpay Oneclick de Transbank. Datos de prueba
publicados por Transbank (fuentes de terceros que los citan):

| Resultado | Tipo | Número | CVV | Vencimiento |
|---|---|---|---|---|
| Aprobada | Visa crédito | 4051 8856 0044 6623 | 123 | cualquier fecha futura |
| Aprobada | Redcompra (débito) | 4051 8842 3993 7763 | 123 | cualquier fecha futura |
| Rechazada | Mastercard crédito | 5186 0595 5959 0568 | 123 | cualquier fecha futura |

Si aparece el formulario del banco: RUT `11.111.111-1`, clave `123`.
En el paso "Es mi correo" hay que marcar la casilla; el cobro de validación de $50 es simulado.

> Si algún número no funciona, avisar: los datos no vienen de la documentación oficial de Flow.

## Cómo probar la suscripción desde la app (staging)

1. Entrar con un usuario de prueba y pulsar "Suscribirse a Premium".
2. En Flow sandbox: Continuar → Tarjetas → ingresar la tarjeta aprobada → marcar "Es mi correo".
3. Debe aparecer "¡Suscripción Activada!" y la app debe mostrar Premium.
4. Para probar rechazo, repetir con la tarjeta rechazada: el usuario NO debe quedar Premium.

## Respuesta real observada de Flow (28-nov-2026, staging)

- `getRegisterStatus`: `status: '1'` (tarjeta registrada).
- `subscription/get`: `status: 1` (activa), `morose: 0`, `period_start: 2026-11-28 00:00:00`,
  `period_end: 2026-12-27 00:00:00`, `next_invoice_date: 2026-12-28 00:00:00`,
  `trial_period_days: 0`, `cancel_at_period_end: 0`.
- `period_end` es 29 días después de `period_start`; con 1 día de gracia coincide con
  `next_invoice_date`.

Con esto queda confirmado que `status == 1` significa suscripción activa. Pendiente de confirmar
con el comando `sincronizar_premium --diagnostico`: comportamiento con cobro rechazado (`morose`)
y tras cancelar (`cancel_at_period_end`).
