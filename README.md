# Dossiers

Herramienta interna para convertir vídeos o audios largos de eventos, ponencias,
jornadas, webinars o mesas redondas en un dossier profesional revisable y
descargable.

## Qué hace

- Crea proyectos de dossier.
- Sube vídeo o audio.
- Extrae y divide audio con ffmpeg.
- Transcribe con OpenAI.
- Limpia transcripción, resume por bloques y genera un dossier final.
- Permite revisar transcripción, resumen y dossier en la web.
- Exporta Markdown, DOCX y PDF para transcripciones.
- Mantiene historial, estados y logs por proyecto.

## Arranque local con Docker

1. Copia variables:

   ```bash
   cp .env.example .env
   ```

2. Edita `.env` y añade `OPENAI_API_KEY`.

3. Levanta servicios:

   ```bash
   docker compose up --build
   ```

4. Abre:

   ```text
   http://localhost:3000
   ```

## Variables de entorno

- `SECRET_KEY`: clave de sesión Flask.
- `DATABASE_URL`: URL SQLAlchemy para PostgreSQL.
- `REDIS_URL`: conexión Redis para RQ.
- `STORAGE_ROOT`: carpeta base de archivos, por defecto `/storage`.
- `MAX_UPLOAD_MB`: límite de subida, por defecto `2000`.
- `PORT`: puerto web, por defecto `3000`.
- `OPENAI_API_KEY`: API key de OpenAI.
- `OPENAI_TRANSCRIPTION_MODEL`: por defecto `gpt-4o-transcribe`.
- `OPENAI_SUMMARY_MODEL`: por defecto `gpt-5.4-mini`.
- `TRANSCRIPT_CHUNK_MINUTES`: objetivo de fragmentos, por defecto `20`; los cortes se ajustan a silencios cercanos cuando es posible.
- `YTDLP_COOKIES_FILE`: ruta opcional a un archivo `cookies.txt` para YouTube, por ejemplo `/storage/youtube-cookies.txt`.

## Migraciones

Con Docker:

```bash
docker compose run --rm web alembic upgrade head
```

Sin Docker, con dependencias instaladas:

```bash
alembic upgrade head
```

## Worker

Con Docker el worker se inicia con:

```bash
docker compose up worker
```

Manual:

```bash
rq worker default --url "$REDIS_URL"
```

## Tests

```bash
python -m pip install -r requirements.txt
pytest
```

Los tests no llaman a OpenAI real.

Si prefieres aislar dependencias:

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m pytest
```

## Flujo de uso

1. Crear un nuevo proyecto desde el panel.
2. Seleccionar idioma, modelo de transcripción, plantilla y archivo.
3. La app guarda el original y encola un job.
4. El worker procesa audio, fragmentos por silencios, transcripción, resumen y dossier.
5. La vista de detalle muestra estado, logs e informe de calidad por HTMX.
6. Al completar, se pueden revisar transcripción/glosario, regenerar resumen,
   dossier o exportaciones sin repetir transcripción, y descargar Markdown,
   DOCX y PDF.

## Calidad y revisión

- Cada procesamiento genera un informe interno con métricas de duración, chunks,
  coste estimado, solapes eliminados y checks de secciones esperadas.
- La transcripción puede editarse desde la vista de detalle; si hay versión
  revisada, las regeneraciones usan esa versión.
- El glosario del proyecto se incorpora al prompt de transcripción para preservar
  nombres propios, empresas y terminología.
- El enlace público incluye descargas y transcripción opcional cuando existe.

## Limitaciones actuales

- Herramienta interna sin login.
- Almacenamiento local.
- Sin edición colaborativa ni revisión avanzada.
- Sin detección de hablantes.
- Sin marcas de tiempo en el dossier final.
- Sin exportación PDF del dossier final.
- La calidad depende de OpenAI y de la calidad del audio.

## Despliegue Dokploy

Para esta versión usa **Build Type: Dockerfile**. La app escucha en `PORT=3000`
con Gunicorn. No uses Nixpacks para este MVP porque el worker, ffmpeg y las
dependencias Python forman parte del runtime.

### YouTube pide confirmar que no eres un bot

Si YouTube devuelve `Sign in to confirm you're not a bot`, `yt-dlp` necesita
cookies de una sesión válida de YouTube:

1. Exporta las cookies de `youtube.com` desde tu navegador en formato
   Netscape/cookies.txt.
2. Sube ese archivo al almacenamiento persistente del servidor, por ejemplo:
   `/storage/youtube-cookies.txt`.
3. En Dokploy añade la variable de entorno:

   ```text
   YTDLP_COOKIES_FILE=/storage/youtube-cookies.txt
   ```

4. Haz rebuild/redeploy de la app.

No pegues el contenido de ese archivo en chats, logs ni variables de entorno:
equivale a una sesión iniciada de YouTube.

## Roadmap fase 2

- Login de usuario.
- Stripe.
- Subida mediante enlace público.
- Integración Google Drive.
- Integración Dropbox.
- Plantillas editables desde UI.
- Revisión colaborativa.
- Facturación por minutos.
- Área de cliente.
- Borrado automático de archivos.
- Detección de hablantes.
- Marcas de tiempo.
- Exportación PDF del dossier final.
- Generación de posts para redes.
- Modo multi-evento.
