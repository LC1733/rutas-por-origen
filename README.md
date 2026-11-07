# MARTILINE Global Control: rutas por origen

Dashboard de planificación de conexiones hacia Perú, con consulta periódica de fuentes públicas y una base documental de rutas. Las fuentes de datos utilizadas no requieren una suscripción o una API de pago.

- [Abrir el dashboard en GitHub Pages](https://lc1733.github.io/rutas-por-origen/)
- [Consultar la última ejecución del actualizador](https://github.com/LC1733/rutas-por-origen/actions/workflows/actualizar-fuentes.yml)
- [Ver el snapshot de consultas](https://raw.githubusercontent.com/LC1733/rutas-por-origen/main/data/live.json)

## Cómo se actualiza

El workflow `Actualizar fuentes públicas` consulta las fuentes y guarda el resultado en `data/live.json`. La programación es una vez por hora, en el minuto 17, expresada en UTC. También puede ejecutarse desde **Actions → Actualizar fuentes públicas → Run workflow**.

La interfaz consulta ese archivo directamente desde `raw.githubusercontent.com`. Esto permite mostrar nuevas consultas sin reconstruir GitHub Pages cada hora. Los cambios en la interfaz siguen publicándose mediante el despliegue normal de Pages.

La frecuencia del proceso no equivale a la frecuencia de publicación del organismo. Para interpretar un dato se deben distinguir:

1. **Fecha de consulta:** cuándo el proceso intentó acceder a la fuente.
2. **Fecha de la información:** emisión del boletín, observación o registro recuperado.
3. **Vigencia y alcance:** período, instalación, muelle, servicio o actividad a los que se refiere.
4. **Resultado de acceso:** si se pudo obtener y reconocer la información, si hay un error o si hace falta revisión.

Una consulta fallida no demuestra que un puerto esté cerrado o abierto. Un documento antiguo tampoco se vuelve vigente por haberlo descargado hoy. Cuando se conserva información previa, su fecha original debe seguir visible y la interfaz debe advertir su antigüedad.

## Alcance de la automatización

La consulta de autoridades y boletines puede actualizar el contexto operativo y detectar nuevas publicaciones. El resultado depende de que cada portal responda y mantenga un formato reconocible. Una página pública no garantiza una API estable ni datos disponibles en tiempo real.

La base de alternativas conserva el respaldo y los requisitos de cada ruta. Un cambio de boletín meteorológico o de estado portuario no confirma por sí solo una conexión comercial, una reserva, tarifa, espacio ni ETD/ETA. Esos datos requieren confirmación de la naviera, terminal u operador del viaje correspondiente.

Las casillas del plan de contingencia son un registro local del usuario. No son aprobaciones automáticas ni se comparten entre navegadores.

## Frecuencia y límites de GitHub Actions

- La programación del minuto 17 evita el inicio de hora, que GitHub identifica como un período habitual de carga alta.
- GitHub puede retrasar o descartar ejecuciones programadas. La frecuencia es un objetivo de consulta, sin garantía de puntualidad ni servicio en tiempo real.
- El archivo del workflow debe permanecer en la rama predeterminada. Este proyecto utiliza `main`.
- GitHub desactiva los workflows programados de repositorios públicos tras 60 días sin actividad del repositorio. Las fechas visibles permiten detectar que el actualizador dejó de producir resultados. Puede reactivarse desde Actions.
- Una ejecución dura como máximo ocho minutos. Si falla el proceso o la publicación, el último snapshot permanece disponible y envejece hasta una nueva consulta exitosa.
- El recolector registra la URL de la ejecución cuando dispone de las variables `GITHUB_*`; así el resultado puede relacionarse con su historial técnico.

Estas condiciones proceden de la [documentación oficial sobre eventos programados](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Costo y permisos

El repositorio es público y el workflow usa el runner estándar `ubuntu-24.04`. GitHub mantiene gratuito el cómputo de runners estándar en repositorios públicos. El workflow no crea cachés, no sube artifacts ni usa runners de mayor capacidad. Consulta las [condiciones de facturación de GitHub Actions](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

Si se cambia el repositorio a privado, deben revisarse las condiciones de GitHub Actions y de publicación antes de mantener esta programación. Esta configuración no modifica la visibilidad del repositorio ni contrata servicios.

No hace falta guardar un token personal ni credenciales de las fuentes. GitHub suministra un `GITHUB_TOKEN` temporal a cada ejecución. El workflow solicita `contents: write` para guardar únicamente `data/live.json`; no expone ese token en el HTML.

Los commits de un workflow con `GITHUB_TOKEN` no disparan otro workflow por `push` ni una reconstrucción automática de Pages. Por eso la interfaz lee el JSON publicado directamente. Véanse [la documentación de disparadores](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow#triggering-a-workflow-from-a-workflow) y [la configuración de GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site#troubleshooting-publishing-from-a-branch).

Para instalar o modificar archivos en `.github/workflows`, la conexión utilizada debe contar con permiso de escritura de workflows además de contenidos. Poder editar un HTML no acredita ese permiso. Consulta [la API de contenidos de GitHub](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents).

## Archivos del proyecto

| Archivo | Función |
| --- | --- |
| `index.html` | Dashboard y base documental de planificación |
| `data/live.json` | Resultado de las consultas y sus fechas |
| `scripts/collect_sources.py` | Recolector de fuentes públicas |
| `requirements.txt` | Dependencias del recolector |
| `tests/` | Pruebas del tratamiento de datos y errores |
| `.github/workflows/actualizar-fuentes.yml` | Programación y publicación del snapshot |

## Ejecución local

Desde la raíz del repositorio, con Python 3.12 y un entorno virtual activo:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_*.py' -v
python scripts/collect_sources.py
```

El workflow ejecuta las pruebas antes de consultar y comprueba que la salida sea JSON válido antes de publicarla. Solo añade `data/live.json` al commit. Antes de publicar compara la rama remota con la revisión utilizada; si otro cambio avanzó `main`, termina con un error explicativo. El push normal también rechaza avances concurrentes. No usa force push ni reescribe el historial.

## Dependencias de GitHub Actions

Las acciones se fijan a un SHA completo para que un cambio de etiqueta no cambie silenciosamente el código ejecutado. Las versiones se verificaron el 7 de noviembre de 2026:

| Acción | Release oficial | SHA |
| --- | --- | --- |
| `actions/checkout` | [v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1) | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-python` | [v7.1.0](https://github.com/actions/setup-python/releases/tag/v7.1.0) | `6d4fd22b20465ccc0f7354476588e8df7c9ca0e0` |

La ejecución manual está documentada en [Manually running a workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

## Interfaz y reconstrucción

La interfaz se mantiene en `src/`. Ejecutar `python scripts/build.py` para regenerar `index.html` con la copia de evidencia y la consulta inicial. La aplicación comprueba la publicación JSON remota cada cinco minutos mientras está visible. Las fechas se muestran en hora de Perú (UTC−5). El catálogo y la auditoría conservan su fecha documental.

El job solo se ejecuta si GitHub identifica el repositorio como público. Si se cambia su visibilidad, se detiene para evitar consumo de cuotas de repositorios privados.
