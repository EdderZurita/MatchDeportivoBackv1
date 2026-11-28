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

## Dependencias con hashes fijos

CI instala con `pip install --require-hashes`, que rechaza cualquier paquete cuyo contenido no
coincida con el hash registrado (protección contra paquetes alterados). Los archivos son:

| Archivo | Para qué | Se genera desde |
|---|---|---|
| `requirements-ci.txt` | tests (todo `requirements.txt` + coverage) | `requirements-ci.in` |
| `requirements-lint.txt` | ruff, bandit, pip-audit | `requirements-lint.in` |

`requirements.txt` (el que usa Render) no cambia. Al subir una versión en `requirements.txt`
o en un `.in`, regenerar los archivos con hashes (Python 3.13, Linux):

```bash
pip install uv
uv pip compile requirements-ci.in   --generate-hashes --python-version 3.13 --python-platform x86_64-manylinux_2_28 --no-header -o requirements-ci.txt
uv pip compile requirements-lint.in --generate-hashes --python-version 3.13 --python-platform x86_64-manylinux_2_28 --no-header -o requirements-lint.txt
```
