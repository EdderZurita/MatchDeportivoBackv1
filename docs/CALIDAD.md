# Calidad: CI y SonarCloud

El workflow `CI` corre en cada PR y en `main`:

- **Tests y migraciones** (bloquea el merge): `check`, `makemigrations --check` y la suite con cobertura.
- **Calidad y seguridad** (informativo por ahora): ruff, bandit y pip-audit.
- **SonarCloud** (se activa al configurarlo): analiza el código y la cobertura.

## Activar SonarCloud (gratis para repositorios públicos)

1. Entra a https://sonarcloud.io con tu cuenta de GitHub e importa este repositorio.
2. Desactiva el *Automatic Analysis* del proyecto (Administration → Analysis Method), para no duplicar con CI.
3. En SonarCloud: *My Account → Security → Generate Token*.
4. En GitHub (Settings → Secrets and variables → Actions):
   - Secreto `SONAR_TOKEN` = el token generado.
   - Variables `SONAR_PROJECT_KEY` y `SONAR_ORGANIZATION` = los valores que muestra SonarCloud al importar.

Hasta que existan esos tres valores, el job de Sonar se omite sin fallar.
