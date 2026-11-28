# Respaldo automático de Supabase

El workflow `Respaldo Supabase` (`.github/workflows/respaldo-supabase.yml`) hace cada día un
volcado del esquema `public`, lo **cifra con AES-256**, comprueba que se puede restaurar en una
base temporal y lo guarda 30 días como artefacto de GitHub Actions.

> Los volcados **nunca** se guardan en git: contienen datos personales y quedarían en el
> historial para siempre. El esquema ya está versionado en `core/migrations/`.

## Configuración (una sola vez)

En GitHub: **Settings → Secrets and variables → Actions → New repository secret**.

| Secreto | Contenido |
|---|---|
| `BACKUP_PASSPHRASE` | Contraseña de cifrado, mínimo 24 caracteres. Genérala con `openssl rand -base64 36`. Guárdala en tu gestor de contraseñas: **sin ella el respaldo no se puede abrir**. |
| `SUPABASE_DB_URL_STAGING` | Cadena de conexión del proyecto de staging. |
| `SUPABASE_DB_URL_PROD` | Igual, para producción (opcional; si falta, ese entorno se omite). |

Cadena de conexión: Supabase → **Connect → Session pooler** (puerto 5432, usuario
`postgres.<ref>`). Usa el *session pooler* y no la conexión directa: GitHub solo tiene IPv4.
Si la contraseña tiene caracteres especiales (`@ : / # %`), codifícalos como URL.

Primera prueba: pestaña **Actions → Respaldo Supabase → Run workflow**.

## Restaurar

```bash
# 1. Descargar el artefacto desde la pestaña Actions y descifrar
gpg --output respaldo.dump --decrypt respaldo-STAGING-<id-de-ejecucion>-1.dump.gpg

# 2. Restaurar en una base NUEVA (recomendado) o sobre la existente
pg_restore --no-owner --no-privileges --clean --if-exists \
  --dbname "postgresql://USUARIO:CLAVE@HOST:5432/postgres" respaldo.dump
```

Usa un cliente `pg_restore` de la versión 17 o superior. Antes de restaurar sobre producción,
prueba en una base nueva y revisa los datos.

## Límites conocidos

- El nombre del archivo lleva el id de la ejecución; la fecha se ve en la pestaña Actions.
- Retención de 30 días en GitHub; para más tiempo, copiar los artefactos a almacenamiento externo.
- GitHub desactiva los workflows programados tras 60 días sin actividad en el repositorio.
- Si un respaldo falla, GitHub avisa por correo a quien editó el workflow por última vez.
- Con el repositorio público, los artefactos son descargables por cualquier cuenta de GitHub:
  la seguridad descansa por completo en la contraseña de cifrado. Lo ideal es repositorio privado.
