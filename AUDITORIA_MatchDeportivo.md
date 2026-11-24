# Auditoría técnica – MatchDeportivo (Back Django + Front Flutter)
Fecha: 2026-11-24 · Alcance: lectura completa de código (no se ejecutó nada). Los repos no tienen `.git`, así que no se pudo revisar historial ni secretos commiteados.

## 1. Qué tenemos
**Back** – Django 6.1 + DRF + SimpleJWT, una sola app `core` (~3.500 líneas; `views.py` = 1.912). BD Postgres (Supabase) vía `DATABASE_URL`, SQLite local. Deploy en Render (`build.sh`, WhiteNoise, gunicorn). Integraciones: Flow (suscripción Premium, tarjeta tokenizada), Firebase FCM (push), Brevo (OTP por email, vía urllib), correo SMTP Gmail configurado pero sin uso aparente.
Modelos: Usuario, Actividad, Inscripcion, Amistad, Mensaje, ValoracionJugador, Calificacion (duplica a ValoracionJugador), ResenaActividad, Reporte, Bloqueo, Suscripcion, CodigoRecuperacion, DispositivoFCM, RegistroActividad (auditoría).
**Front** – Flutter (Android/iOS/web), 8.100 líneas Dart, sin gestión de estado (setState), un único `ApiService` estático de 1.000 líneas. JWT en `flutter_secure_storage` (bien). Mapa con flutter_map + MapTiler + Nominatim. Chat por polling cada 3 s.
**Calidad general**: `core/tests.py` vacío, `widget_test.dart` es el de plantilla (y fallaría), sin CI, sin linters configurados en back, sin logging estructurado (`print` en 10 sitios back / 17 front), sin README real, sin `.env.example`, sin Dockerfile/render.yaml en repo.

## 2. Hallazgos CRÍTICOS (corregir primero)
| # | Dónde | Problema |
|---|---|---|
| C1 | `SuscripcionViewSet.activar` (`POST /api/suscripciones/activar/`) | Cualquier usuario autenticado envía un `id_transaccion_psp` inventado y queda **Premium gratis** 30 días. No verifica nada con Flow. Además el ViewSet es `ModelViewSet`: expone create/update/delete de Suscripcion (un usuario puede PATCH su `estado`). |
| C2 | `RetornoRegistroTarjetaView` | (a) Si no encuentra la suscripción por token/customer, **toma la última PENDIENTE de cualquier usuario** y la activa → premium asignado a la persona equivocada. (b) Si `crear_suscripcion` falla igual activa Premium con un `sub_<id>` falso. (c) No valida que el `customerId` devuelto coincida con el usuario. (d) `time.sleep(1.5)` bloquea un worker. |
| C3 | Premium nunca expira | `fecha_vencimiento` se guarda pero nada la usa: sin webhook de Flow, sin tarea/cron. Un Premium sigue siéndolo para siempre aunque deje de pagar. |
| C4 | `AmistadViewSet` (ModelViewSet) | El solicitante puede hacer `PATCH estado=ACEPTADA` sobre su propia solicitud: se salta la aceptación y el límite de 5 amigos; también puede crear con `estado`/`receptor` arbitrarios. |
| C5 | `MensajeViewSet` | `?actividad_id=` devuelve el chat de **cualquier** actividad a cualquier usuario (IDOR); no se valida que quien escribe sea participante; se pueden **editar/borrar** mensajes recibidos (PUT/DELETE sobre queryset donde `receptor=user`); no respeta Bloqueos. El auto-reporte usa como "reportante" al receptor (falsifica autoría). |
| C6 | `UsuarioViewSet` + `UsuarioSerializer` | Lista/busca **todos** los usuarios exponiendo email, teléfono, `is_staff`, `is_superuser` a cualquier usuario logueado. `mis_amigos` también devuelve email. Fuga de PII y mapa de admins. |
| C7 | Configuración | `DEBUG` por defecto **True**, `SECRET_KEY` con fallback `django-insecure-key`, `ALLOWED_HOSTS=['*']`, `CORS_ALLOW_ALL_ORIGINS=True`. Si falta una variable en Render, la app arranca insegura sin avisar. |
| C8 | OTP de recuperación | Código de 6 dígitos con `random.randint` (no criptográfico), **sin límite de intentos** ni throttling: fuerza bruta de 900k combinaciones en 15 min es viable. Tampoco se aplican los validadores de contraseña de Django (solo `len>=8`). |

## 3. Hallazgos ALTOS
- **Autorización por defecto laxa**: `ValoracionJugadorViewSet` y `ResenaActividadViewSet` son ModelViewSet con `queryset=all()`: cualquier usuario puede editar/borrar reseñas ajenas; Valoración permite calificar a cualquiera, sin haber jugado ni que el partido haya terminado.
- **InscripcionViewSet** `?actividad=ID` lista inscripciones (usuarios, estados) de cualquier actividad a cualquiera.
- **Bugs que dan 500**: `Usuario.reputacion_promedio` es `@property` sin setter, pero `CalificacionViewSet.perform_create`, `ValoracionJugadorViewSet.perform_create` y `signals.actualizar_reputacion_usuario` hacen `usuario.reputacion_promedio = ...` (+ `save(update_fields=['reputacion_promedio'])`). Resultado: calificar devuelve **500 después de guardar la fila** (reintentar dice "ya calificaste"). La señal sobre ValoracionJugador falla igual.
- **Sin rate limiting** (ni DRF throttling) en login, registro, recuperación, chat, reportes. Registro abierto sin verificación de email; el email se puede cambiar vía `mi_perfil` sin reverificar; `username` editable.
- **JWT**: access de 1 día, refresh 7 días, sin rotación ni blacklist; logout solo borra local. El front **nunca usa el refresh token** ni maneja 401: al día siguiente las llamadas fallan silenciosamente (devuelven listas vacías).
- **Android**: `usesCleartextTraffic="true"` (innecesario, API es https); release firmado con **debug keys**; `applicationId = com.example...`.
- **Clave MapTiler hardcodeada** en 2 archivos del front (visible en el APK). Restringirla por dominio/app en el panel de MapTiler o proxear.
- `ActividadSerializer(fields='__all__')`: el organizador puede escribir `estado` (p. ej. reabrir/FINALIZADA a mano) y bajar `cupos_totales` por debajo de los confirmados; sin validar rango de lat/long, `cupos_totales` máximo, ni `fecha_hora` en update (la validación bloquea fechas pasadas incluso al editar campos sin relación).
- **Race conditions**: `gestionar_participante` hace `enviar_notificacion_push` (red) dentro de `transaction.atomic` con `select_for_update`; límites freemium (1 partido/día, 5 amigos) se comprueban sin lock → se evaden con requests concurrentes. `creado_en__date=hoy` usa UTC/aware mal vs zona Chile.
- Admin de Django: `reputacion_promedio` (property) está en `fieldsets` sin estar en `readonly_fields` → probable `FieldError` al abrir un usuario en /admin.

## 4. Hallazgos MEDIOS / escalamiento
- **Rendimiento**: `cupos_disponibles` y `reputacion_promedio` hacen una query por fila (N+1) en cada listado (50 filas = ~100 queries); `ActividadViewSet.get_queryset` ejecuta un `UPDATE` masivo **en cada GET** (incluso anónimo). Mover a tarea periódica o anotar con `Count/Avg`. `recomendadas` carga todas las actividades en memoria y puntúa en Python.
- **Chat roto a escala**: `Mensaje.Meta.ordering=['fecha_envio']` + paginación de 50 + el front lee solo `results` de la página 1 ⇒ el chat solo muestra los **50 mensajes más antiguos**; nunca aparecen los nuevos. Además polling cada 3 s por cliente sobre Render (free tier duerme/limita). Migrar a WebSocket/Channels o al menos `?after_id=` + orden descendente.
- Push FCM y emails **síncronos dentro del request** (bucle por inscrito). Usar cola (Celery/RQ/Django-Q) o `transaction.on_commit`.
- `registrar_log` toma `X-Forwarded-For` sin validar (spoofable) y guarda IP/PII en texto; los logs incluyen contenido completo de mensajes sospechosos. Definir retención.
- Moderación por lista de palabras: frágil (falsos positivos, evadible) y crea Reportes automáticos con autoría falsa.
- Modelos duplicados: `ValoracionJugador` y `Calificacion` (misma función, dos tablas, dos endpoints). Elegir uno y migrar. `RegistroActividad.MODULOS` no incluye 'SEGURIDAD' (se usa en varios logs). `Usuario.es_premium` duplica el estado de `Suscripcion` (fuente de verdad doble). Faltan índices (Mensaje por actividad/fecha, Actividad por estado/fecha_hora, Inscripcion por estado) y `CheckConstraint` (puntuación, cupos>0, no auto-amistad/auto-bloqueo). `unique_together` → preferir `UniqueConstraint`; Amistad A→B y B→A pueden coexistir.
- Código muerto: acción `cambiar_estado_cuenta` duplicada dentro de `AdminLogsListView` al final de `views.py`; imports duplicados; `SuscripcionViewSet` + `*SuscripcionView` duplican lógica; `EMAIL_*` SMTP sin uso (se usa Brevo con `os.getenv` directo en la vista).
- `requirements.txt` está en **UTF-16** (pip suele tolerarlo por el BOM, pero rompe herramientas como pip-audit, Dependabot, grep). Reconvertir a UTF-8. Sin separar dev/prod; sin pin de hashes.
- `firebase_service.py` inicializa Firebase al importar y usa archivo JSON local (`firebase_credentials.json`) → en Render hay que montarlo como Secret File o cargar desde variable de entorno; hoy si falta, las push fallan silenciosamente (solo `print`).
- Sin `SECURE_SSL_REDIRECT`, `SESSION/CSRF_COOKIE_SECURE`, HSTS, `SECURE_PROXY_SSL_HEADER` (detrás de Render), ni `/health` endpoint. `/admin/` expuesto públicamente en la ruta por defecto. Sin `LOGGING`, sin Sentry.
- Paginación global de 50 pero varias `@action` devuelven listas completas sin paginar (`historial`, `mis_actividades`, `mis_inscripciones`).

## 5. Front (Flutter)
- **Bien**: tokens en `flutter_secure_storage`, HTTPS a la API, `Timer` del chat se cancela en `dispose`, `mounted` checks, permisos Android razonables.
- `flutter_map`, `latlong2` y `geolocator` están en **`dev_dependencies`** pero se usan en `lib/` → los builds release/CI pueden fallar o comportarse distinto. Moverlas a `dependencies`.
- `ApiService` captura todo y devuelve `[]`/`false`/`null`: la UI no distingue "sin datos" de "sesión expirada" o "sin red". 51 `catch` genéricos y 17 `print`. Falta interceptor (401 → refresh → reintento → logout), timeouts en `http` y reintentos.
- `baseUrl` hardcodeada a producción: no hay flavors dev/staging/prod (usar `--dart-define` / `String.fromEnvironment`). Las pruebas contra prod usan datos reales.
- La UI decide "admin" con `is_staff`/`is_superuser` devueltos por la API (OK porque el back vuelve a validar con `IsAdminUser`), pero esos campos no deberían ir a otros usuarios (ver C6).
- Validaciones de formulario mínimas (34 usos de `validator`/RegExp en 8.000 líneas): login/registro sin formato de email, fortaleza de contraseña ni límites; `crear_actividad` valida cupos>0 pero no máximo ni fecha futura en cliente.
- Archivos de 700–1.400 líneas con lógica de UI + red mezclada; sin capa de modelos (todo `Map<String,dynamic>`) → errores de tipo en runtime. Sugerido: modelos tipados, repositorios, Riverpod/Bloc, `go_router`.
- `firebase_options.dart` / `google-services.json` no están (correcto que no se versionen), pero `Firebase.initializeApp()` sin options en web/iOS fallará; README sin instrucciones.
- Nominatim (OSM) usado directo desde el cliente: su política de uso exige User-Agent propio y prohíbe uso intensivo; considerar geocodificación vía MapTiler o proxy en backend.
- iOS: `Info.plist` sin `NSLocationWhenInUseUsageDescription` (geolocator crashea/rechaza en iOS) – verificar.
- Tests: solo el de plantilla (falla: busca un contador).

## 6. Lo que SÍ está bien
Usuario custom con email único; `select_for_update` en inscribirse/gestionar; validadores de password configurados; respuestas genéricas en recuperación (no enumera emails); permisos de Reporte/Bloqueo/Calificacion filtrados por usuario; auditoría (`RegistroActividad`) bien pensada; `.gitignore` excluye `.env` y credenciales de Firebase; cancelación lógica de actividades; JWT guardado en storage seguro.

## 7. Roadmap sugerido
**Fase 0 – Urgente (1–2 días)**: C1, C2, C3 (pagos/premium), C4, C5, C6, C7; rotar cualquier secreto que haya estado en repos/Render si estos zips se compartieron; verificar que Render tenga `DEBUG=False` y `SECRET_KEY` propia.
**Fase 1 – Seguridad base (1 semana)**: throttling DRF (login/OTP/registro), OTP con `secrets` + intentos máx + hash, `validate_password` en registro/reset, ViewSets → `mixins` explícitos (no `ModelViewSet` por defecto), `serializers` con `fields` explícitos y un `UsuarioPublicoSerializer`, permisos de objeto (`IsOwner`, `IsParticipante`), CORS restringido, headers HTTPS, refresh/rotación/blacklist JWT, interceptor 401 en Flutter.
**Fase 2 – Correctitud**: arreglar reputación (campo persistido o annotate, eliminar duplicado Valoración/Calificacion), chat (orden/paginación), expiración de Premium + webhook Flow + tarea periódica, constraints e índices con migraciones.
**Fase 3 – Calidad y escalado**: tests (pytest-django + APIClient para permisos y pagos mockeados; widget/unit en Flutter), CI (ruff, black, mypy opcional, bandit, pip-audit, `manage.py check --deploy`), dividir `views.py` en módulo por dominio, cola de tareas para push/email, caché, logging/Sentry, entornos dev/staging/prod con `.env.example` y `--dart-define`, WebSockets para chat, estado/arquitectura en Flutter.

## 8. Referencias para contrastar
Django *Deployment checklist* (`manage.py check --deploy`) · Django *Security* · DRF *Permissions / Throttling / Serializers* · OWASP API Security Top 10 (BOLA/IDOR, Broken Function Level Auth, Mass Assignment, Unrestricted Resource Consumption) · OWASP MASVS (móvil) · PEP 8 / ruff · Flow API docs (webhooks `urlConfirmation`).
Nota: estas guías las cito de conocimiento propio; no las consulté en línea en esta pasada.
