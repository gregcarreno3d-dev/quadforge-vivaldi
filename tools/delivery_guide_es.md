# QuadForge Vivaldi {VERSION}: paquete de entrega

## Que hay aqui
- `ADDON_PARA_BLENDER/`: el zip que se instala en Blender (todas las plataformas).
- `GITHUB_UPLOAD/`: todo lo que se sube a GitHub, ya separado en tandas de menos de 100 archivos.
- `GITHUB_UPLOAD/6_RELEASE/`: los 4 zips por plataforma y el texto de la release.

Esta carpeta se regenera con cada version: cada vez, sube solo lo que cambio (o todo, GitHub reemplaza lo que ya existe).

## Subir a GitHub (repositorio gregcarreno3d-dev/quadforge-vivaldi)
En el repo: **Add file > Upload files**. Arrastra el CONTENIDO de cada carpeta (lo que hay dentro, no la carpeta numerada) y pulsa **Commit changes** con "Commit directly to the main branch".

| Orden | Carpeta | Archivos | Que arrastrar | Mensaje del commit |
|---|---|---|---|---|
| 1 | `1_archivos_de_la_raiz` | {N1} | `README.md` y `.gitignore` (el `LICENSE` ya existe en GitHub, no lo subas) | Add README and gitignore |
| 2 | `2_carpeta_engine` | {N2} | la carpeta `engine` | Add FORGE engine source |
| 3 | `3_tests_y_tools` | {N3} | las carpetas `tests` y `tools` | Add tests and build tools |
| 4 | `4a_addon_parte1` | {N4a} | la carpeta `quadforge` | Add Blender add-on (part 1) |
| 5 | `4b_addon_parte2` | {N4b} | la carpeta `quadforge` | Add Blender add-on (part 2) |

Las dos partes del paso 4 se unen solas en la carpeta `quadforge` del repositorio.

## Release
Columna derecha > **Create a new release**. Datos en `6_RELEASE/TAG_Y_TITULO.txt`; la descripcion es el contenido de `6_RELEASE/RELEASE_NOTES.txt`. Arrastra los 4 zips por plataforma. Pulsa **Publish release**.

## CI opcional (prueba automatica)
`5_CI_opcional/ci.yml`: en el repo, **Add file > Create new file**, escribe el nombre `.github/workflows/ci.yml` (al escribir "/" se crean las carpetas), pega el contenido del archivo y haz commit. Puede fallar en Windows o macOS la primera vez: es normal, mandame una captura.

## Antes de subir
- Comprueba en `Settings > Emails` de GitHub que "Keep my email addresses private" esta activado.
- `6_RELEASE/SHA256SUMS.txt` tiene las huellas de los zips por si quieres pegarlas en la release.
