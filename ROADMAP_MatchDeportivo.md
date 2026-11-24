# Roadmap de reordenamiento – MatchDeportivo (Back Django + Front Flutter)
Fecha: 2026-11-24 · Base: AUDITORIA_MatchDeportivo.md · Objetivo: modularizar, ordenar y limpiar **sin romper producción**, con logs y tests desde el inicio.

## 0. Principios para no romper nada
1. **Primero la red de seguridad, después el refactor.** Ningún archivo se mueve sin tests de caracterización que lo cubran.
2. **Refactor ≠ cambio de comportamiento.** Un commit mueve código *o* cambia lógica, nunca ambos. Los bugs encontrados en la auditoría se anotan (tests `xfail`) y se arreglan en su propia fase.
3. **Contrato de API congelado**: mismas URLs, mismos JSON, mismos códigos HTTP. Hay APKs ya instalados que no se actualizan solos. Verificación: snapshot del esquema OpenAPI (drf-spectacular) antes/después debe ser idéntico.
4. **Migraciones solo aditivas** y nunca renombrar tablas/columnas en el mismo paso que un refactor. `makemigrations --check` debe dar "No changes" durante toda la Fase 2.
5. **Una rama por paso, PRs pequeños (<400 líneas)**, merge solo con tests verdes, deploy a **staging** antes de producción, rollback = revertir el commit/redeploy anterior.
6. **Backup de BD (Supabase) antes de cualquier migración** y antes de cada deploy de fase.
7. **Hotfixes de seguridad críticos van en carril aparte** (ver Fase 1B): son explotables hoy y no deben esperar al refactor.

## 1. Vista general
| Fase | Nombre | Cambia comportamiento | Riesgo | Duración aprox.* |
|---|---|---|---|---|
| 0 | Línea base y entornos | No | Muy bajo | 2–3 días |
| 1A | Logging + tests de caracterización | No | Bajo | 1–1,5 semanas |
| 1B | Hotfixes de seguridad mínimos (carril paralelo) | Sí (acotado) | Medio | 2–4 días |
| 2 | Modularización y limpieza del backend | No | Medio | 1,5–2 semanas |
| 3 | Modularización del frontend Flutter | No | Medio | 1,5–2 semanas |
| 4 | Corrección de bugs y seguridad (auditoría completa) | Sí | Medio | 2–3 semanas |
| 5 | Calidad continua: CI, cobertura, observabilidad, escalado | Sí/No | Bajo | continuo |
*Estimación para 1 desarrollador a medio tiempo/tiempo completo mezclado; ajustar.

---
## FASE 0 – Línea base y entornos (sin tocar lógica)
**Objetivo:** poder volver atrás siempre y trabajar en un entorno reproducible.
- [ ] `git init` en ambos proyectos, primer commit = **código tal cual está** (tag `baseline-2026-11`). Repos remotos privados (GitHub). Verificar que `.env`, `firebase_credentials.json` y `google-services.json` NO entran.
- [ ] Confirmar qué commit/versión corre hoy en Render y qué APK está publicado; etiquetarlos.
- [ ] Backup de la BD de producción (dump Supabase) y guardar restauración probada en una BD local/staging.
- [ ] **Staging**: segundo servicio en Render + BD Postgres separada (o proyecto Supabase aparte) + proyecto Flow sandbox + Firebase de pruebas. Nunca probar contra datos reales.
- [ ] `requirements.txt` → UTF-8; separar `requirements/base.txt`, `dev.txt`, `prod.txt` (mismas versiones).
- [ ] `.env.example` con todas las variables (SECRET_KEY, DEBUG, DATABASE_URL, FLOW_*, BREVO_API_KEY, EMAIL_*, BASE_URL_BACKEND, FIREBASE_CREDENTIALS_JSON…). README con pasos de arranque (venv, migrate, runserver, tests).
- [ ] Entorno local reproducible: `docker-compose` con Postgres (misma versión que Supabase) o al menos SQLite + instrucciones.
- [ ] Verificar en Render: `DEBUG=False`, `SECRET_KEY` propia, `ALLOWED_HOSTS` (**solo comprobar y fijar variables**, sin cambiar código todavía).
- [ ] Flutter: `flutter doctor`, `flutter analyze` y `flutter build apk` de la línea base para saber qué errores/warnings ya existen (no arreglarlos aún).
**Salida/Gate:** repo con baseline, backup restaurable, staging operativo, ambos proyectos compilan/arrancan localmente.

---
## FASE 1A – Logs y tests de caracterización (la red de seguridad)
### 1A.1 Logging backend (aditivo, no cambia respuestas)
- `LOGGING` en settings: formato **JSON** en prod (texto legible en dev), niveles por variable `LOG_LEVEL`.
- Loggers por dominio: `matchdeportivo.auth`, `.actividades`, `.pagos`, `.social`, `.moderacion`, `.notificaciones`, `.http`.
- Middleware **request-id + access log**: método, ruta, status, duración (ms), user_id, request-id (también en header de respuesta `X-Request-ID`). Sin cuerpos de request ni headers `Authorization`.
- Reemplazar los `print()` (10 en back) por `logger.*` con contexto (`extra={user_id, actividad_id,…}`). **Nunca** loguear: contraseñas, tokens JWT, códigos OTP, números de tarjeta, contenido completo de mensajes privados, `flow_token` completo (solo primeros 6 caracteres).
- `registrar_log()` (auditoría en BD) se mantiene tal cual; se le añade también un `logger.info` espejo. Corregir luego `XFF` spoofing (Fase 4).
- Integraciones externas (Flow, Brevo, FCM): log de intento, status y latencia; errores con `logger.exception`.
- Opcional: Sentry (plan gratuito) con scrubbing de PII.
### 1A.2 Logging frontend
- Wrapper `AppLogger` (nivel debug/info/warn/error; solo debug en `kDebugMode`); reemplaza los 17 `print/debugPrint`.
- Logging central de red en un único punto: `[HTTP] GET /actividades/ → 200 (143 ms)`; sin tokens ni bodies sensibles.
- Captura global de errores: `FlutterError.onError` + `PlatformDispatcher.instance.onError` (+ Crashlytics opcional, ya hay Firebase).
### 1A.3 Tests backend (pytest + pytest-django + factory_boy + APIClient)
Orden de prioridad (por riesgo de negocio):
1. **Auth/registro/recuperación OTP** (registro, login JWT, OTP válido/expirado/usado).
2. **Actividades**: CRUD, permisos de organizador, límite diario freemium, finalización automática, inscribirse/abandonar/gestionar_participante (cupos, estado LLENA↔ABIERTA, expulsados).
3. **Pagos/Premium con Flow mockeado** (`responses`/`unittest.mock`): iniciar, retorno OK/fallo, cancelar, activar. Documentar el comportamiento actual (incluidos los fallos C1–C3 como `xfail`/tests "así es hoy").
4. **Social**: amistades (límite 5), bloqueos, mensajes (incl. palabras de riesgo → Reporte), reportes.
5. **Reputación**: Calificación/Valoración/Reseña (hoy dan 500: test `xfail` que documenta el bug).
6. **Permisos transversales**: matriz endpoint × rol (anónimo, usuario, otro usuario, staff) → status esperado. Es el test más valioso para detectar regresiones y los IDOR.
7. **Contrato**: snapshot del OpenAPI (`manage.py spectacular`) guardado en repo; test que falla si cambia sin querer.
8. Pruebas de consultas: `assertNumQueries` en listados clave (documenta N+1 actuales).
- Meta de la fase: **≥70 % cobertura en `views.py`/`models.py`**, 100 % de endpoints con al menos un test de humo.
### 1A.4 Tests frontend
- Corregir/borrar el `widget_test.dart` de plantilla (falla hoy).
- Tests unitarios de `ApiService` con `http` mockeado (`MockClient`): login, listas, errores, 401.
- Widget tests mínimos: login/registro (validaciones), tarjeta de actividad, chat con lista de mensajes.
- **Checklist de humo manual** (documentado): registro → login → crear partido → inscribirse → aprobar → chat → calificar → premium (sandbox) → cancelar.
**Salida/Gate:** suite verde sobre el código *sin refactorizar*, esquema OpenAPI snapshot, logs visibles en staging, `makemigrations --check` limpio.

---
## FASE 1B – Hotfixes de seguridad mínimos (carril paralelo, ramas separadas)
Solo lo explotable hoy, con el menor cambio posible y su test:
- C7: settings fail-fast (si `SECRET_KEY` falta en prod, la app no arranca; `DEBUG` por defecto False; `ALLOWED_HOSTS`/CORS desde variables).
- C1: deshabilitar `SuscripcionViewSet.activar` y create/update/delete públicos de Suscripcion (solo lectura para el usuario).
- C2: retorno de Flow: eliminar el fallback "última PENDIENTE", validar `customerId` ↔ usuario, no activar si falla la suscripción.
- C8: throttling DRF en login/registro/recuperación + límite de intentos del OTP.
- Cada hotfix: PR propio, test que reproduce el exploit (rojo → verde), deploy a staging → prod, nota en CHANGELOG. Verificar con la app actual que el flujo legítimo sigue funcionando.
**Gate:** los exploits C1/C2/C7/C8 no son reproducibles; flujo de pago sandbox sigue OK.

---
## FASE 2 – Modularización y limpieza del backend (refactor puro)
### 2.1 Arquitectura objetivo (monolito modular, una sola app Django por ahora)
```
config/
  settings/ base.py · dev.py · prod.py · test.py     # leen variables de entorno
core/
  models/      usuarios.py · actividades.py · social.py · reputacion.py · moderacion.py · pagos.py · notificaciones.py  (+ __init__ re-exporta todo)
  serializers/ (mismo split)
  views/       auth.py · usuarios.py · actividades.py · inscripciones.py · amistades.py · mensajes.py · reputacion.py · moderacion.py · pagos.py · admin.py
  services/    actividades.py (reglas de inscripción/cupos/límites) · pagos.py · matchmaking.py · notificaciones.py · moderacion.py · auditoria.py
  integrations/ flow.py · firebase.py · brevo.py
  selectors/   consultas reutilizables (evitan N+1 más adelante)
  constants.py / enums.py   # TextChoices: Estado, Deporte, Nivel, etc.
  permissions.py            # IsOwner, IsOrganizador, IsParticipante (se usan en Fase 4)
  urls.py                   # idéntico; importa de core.views
  tests/                    # espejo de la estructura
```
**Decisión clave – NO separar en varias apps Django todavía.** Mover modelos entre apps cambia `app_label`/nombres de tabla y obliga a migraciones delicadas (`SeparateDatabaseAndState`). Se deja como opción de largo plazo, cuando haya tests y staging estables. Dentro de `core`, los paquetes dan el mismo orden sin riesgo de migraciones.
### 2.2 Orden de ejecución (cada ítem = 1 PR, tests verdes + OpenAPI idéntico)
1. `settings` → paquete `settings/` (base/dev/prod/test). `manage.py`, `wsgi`, `asgi` apuntan a `config.settings.prod` vía `DJANGO_SETTINGS_MODULE`. Probar en staging primero.
2. Extraer `integrations/` (flow, firebase, brevo): mover `flow_service.py` y `firebase_service.py`; la vista de OTP deja de llamar a `urllib` directo. Firebase: inicialización perezosa (no al importar) y credenciales desde variable de entorno.
3. Extraer `constants/enums` (reemplazar strings literales `'CONFIRMADA'`, `'ABIERTA'`… por `TextChoices` con **los mismos valores** → sin migración de datos; verificar `makemigrations --check`).
4. Partir `serializers.py` y `models.py` en paquetes con `__init__` que re-exporta (los imports existentes siguen funcionando). Las migraciones no cambian (el `app_label` sigue siendo `core`).
5. Partir `views.py` (1.912 líneas) en `views/` por dominio. `core/views/__init__.py` re-exporta, `urls.py` sin cambios.
6. Mover reglas de negocio de las vistas a `services/` (inscribirse, abandonar, gestionar_participante, límites freemium, matchmaking, activación/cancelación de Premium) como funciones puras con la **misma lógica**; las vistas solo validan entrada y devuelven respuesta.
7. Reemplazar notificaciones y logs inline por llamadas a `services.notificaciones` / `services.auditoria` (misma salida).
8. Eliminar duplicados **solo si hay test que lo respalde**: acción `cambiar_estado_cuenta` repetida bajo `AdminLogsListView` (código muerto), imports duplicados (`unicodedata`, `models`, `settings`), `SuscripcionViewSet` vs vistas de Flow (se marca deprecado, se retira en Fase 4).
9. Herramientas: `ruff` (lint+format) o black+isort+flake8, `pre-commit`, `mypy` opcional, `bandit`, `pip-audit`. Primera pasada **solo formato** (un commit grande aparte, sin cambios de lógica) para que los diffs posteriores sean legibles.
10. Documentación: docstrings en servicios, `docs/arquitectura.md` (diagrama de módulos, flujo de pago, flujo de inscripción), `docs/api.md` generado desde OpenAPI, `CHANGELOG.md`.
### 2.3 Qué NO se toca en esta fase
Modelos/campos/tablas, nombres de URL, formato de respuestas, validaciones, lógica de pagos, versiones de dependencias.
**Gate:** `views.py` ya no existe como monolito (ningún archivo >400 líneas), suite verde, OpenAPI idéntico, `makemigrations --check` limpio, deploy a staging 48 h sin errores en logs → producción.

---
## FASE 3 – Modularización del frontend (Flutter)
### 3.1 Estructura objetivo
```
lib/
  main.dart                      # solo bootstrap
  app/        app.dart · routes.dart · theme.dart
  core/       config/ (env, dart-define) · network/ (api_client, interceptor, errores) · logging/ · utils/ (geo_utils) · widgets compartidos
  data/       models/ (Usuario, Actividad, Inscripcion, Mensaje… con fromJson/toJson) · repositories/ (auth, actividades, social, chat, pagos, admin)
  features/   auth/ · actividades/ · mapa/ · chat/ · amigos/ · perfil/ · premium/ · admin/   → cada una con screens/ widgets/ state/
```
### 3.2 Orden (siempre con la app compilando y el checklist de humo OK)
1. Corregir `pubspec.yaml`: mover `flutter_map`, `latlong2`, `geolocator` a `dependencies`. Verificar build release.
2. `AppConfig`: `baseUrl`, claves de mapa vía `--dart-define` (dev/staging/prod). Sacar la clave MapTiler del código (y restringirla en el panel de MapTiler).
3. `ApiClient` único (envuelve `http`): headers, **timeouts**, logging central, manejo de errores tipado (`NetworkError`, `Unauthorized`, `ServerError`). Mismo comportamiento externo de `ApiService` (fachada que delega) → no se toca ninguna pantalla todavía.
4. Partir `ApiService` (1.000 líneas) en repositorios por dominio; `ApiService` queda como fachada deprecada hasta migrar todas las pantallas.
5. Modelos tipados con `fromJson` (empezar por Actividad y Usuario), manteniendo tolerancia a campos faltantes.
6. Mover pantallas a `features/` (solo mover archivos + imports, sin cambios de lógica). Partir `main.dart` (1.399 líneas: Home, login, registro, perfil) en pantallas separadas.
7. Extraer widgets repetidos (tarjeta de actividad, diálogos, estados vacíos/errores) a `core/widgets`.
8. Estado: introducir Riverpod o Bloc **feature por feature** (primero chat, luego actividades), sin reescritura total.
9. `analysis_options.yaml` más estricto, `flutter analyze` sin warnings, `dart format`.
**Gate:** app equivalente funcionalmente, ningún archivo >400 líneas, `flutter analyze` limpio, tests y checklist de humo OK, APK probado contra staging.

---
## FASE 4 – Correcciones funcionales y de seguridad (ya con red de seguridad)
Se ejecuta el resto de la auditoría, cada ítem con test rojo→verde:
- **Permisos**: reemplazar `ModelViewSet` por mixins explícitos; `fields` explícitos; `UsuarioPublicoSerializer` (sin email/teléfono/is_staff); permisos de objeto (`IsOrganizador`, `IsParticipante`); chat solo para participantes y respetando bloqueos; `estado` de Amistad/Actividad solo vía acciones.
- **Pagos**: webhook Flow (`urlConfirmation`) como fuente de verdad, tarea de expiración de Premium, una sola fuente de verdad (`Suscripcion`), idempotencia.
- **Reputación**: persistir `reputacion_promedio` (campo + signal/servicio) o `annotate`; unificar Valoración/Calificación (migración de datos con script reversible).
- **Chat**: orden descendente + paginación por cursor / `after_id`; luego WebSocket (Channels) como mejora.
- **Auth**: refresh token en Flutter + interceptor 401, rotación/blacklist JWT, access más corto (15–60 min), verificación de email, OTP con `secrets` y hash.
- **Datos**: constraints (`CheckConstraint`, `UniqueConstraint`), índices, validación lat/long y cupos, `UsuarioPublico`.
- **Rendimiento**: `annotate` para cupos/reputación (fin del N+1), mover el `UPDATE` de finalización a tarea periódica, push/email asíncronos (cola) y fuera de `transaction.atomic`.
- **Android/iOS**: quitar `usesCleartextTraffic`, firma release real + `applicationId` definitivo, permisos de ubicación en `Info.plist`.
**Gate por ítem:** test que prueba el fallo original, deploy a staging, aprobación, producción, nota en CHANGELOG. Los cambios que alteran el contrato de la API se publican como `/api/v2/` o con compatibilidad hacia atrás hasta que el APK antiguo deje de usarse (forzar actualización con un endpoint de versión mínima).

---
## FASE 5 – Calidad continua y escalado
- **CI (GitHub Actions)**: back → ruff, tests con Postgres, `check --deploy`, bandit, pip-audit, snapshot OpenAPI; front → `flutter analyze`, `flutter test`, build apk debug. Bloquear merge si falla.
- **Observabilidad**: Sentry/Crashlytics, endpoint `/health` y `/ready`, métricas básicas (latencia p95, errores 5xx), alertas por email/Slack.
- **Operación**: backups automáticos, runbook de rollback, gestión de secretos (Render env + secret files), separación de entornos, rotación periódica de claves.
- **Escalado**: gunicorn workers/threads ajustados, caché (Redis) para listados/mapa, búsqueda geoespacial (PostGIS o bounding box con índice), CDN para estáticos, plan de Render sin hibernación, cola de tareas (Celery/RQ).
- **Opcional largo plazo**: separar `core` en apps (`usuarios`, `actividades`, `social`, `pagos`) con `SeparateDatabaseAndState`; versionado `/api/v1`.

---
## Definición de "hecho" por PR
- [ ] Tests nuevos/existentes verdes (back y/o front) · [ ] `makemigrations --check` limpio (salvo Fase 4) · [ ] OpenAPI snapshot sin cambios (salvo Fase 4) · [ ] Lint/format OK · [ ] Logs revisados en staging · [ ] CHANGELOG actualizado · [ ] Plan de rollback anotado.

## Riesgos principales y mitigación
| Riesgo | Mitigación |
|---|---|
| Romper la app instalada en celulares | Contrato de API congelado en Fases 1–3; versión mínima soportada; `/v2` para cambios incompatibles |
| Migración que daña datos | Backup previo, migraciones aditivas, probar en copia de prod |
| Refactor grande sin cobertura | Fase 1A obligatoria antes de la 2; PRs pequeños |
| Cambiar pagos y afectar cobros | Flow sandbox en staging, tests con mocks, hotfix mínimo primero |
| Falta de tiempo | Orden por valor: 0 → 1A → 1B → 2 → 3 → 4; cada fase entrega valor por sí sola |
| Fuga de secretos al versionar | `.gitignore` verificado, escaneo (gitleaks) en el primer commit |

## Decisiones abiertas (necesito tu respuesta antes de ejecutar)
1. ¿Existe ya un entorno de **staging** (Render/BD aparte) o lo creamos en la Fase 0?
2. ¿Dónde alojarás los repos (GitHub privado)? ¿Hay algún CI hoy?
3. ¿Hay usuarios reales en producción? (define cuán estricto es el congelado de contrato y los backups).
4. Estado del front: ¿prefieres Riverpod o Bloc?
5. ¿Los hotfixes de la Fase 1B (pagos/Premium) pueden ir antes que el refactor? (recomendado: sí).
