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
- Exporta Markdown y DOCX.
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
- `OPENAI_TRANSCRIPTION_MODEL`: por defecto `gpt-4o-mini-transcribe`.
- `OPENAI_SUMMARY_MODEL`: por defecto `gpt-5.4-mini`.
- `TRANSCRIPT_CHUNK_MINUTES`: tamaño de fragmentos, por defecto `20`.

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
pytest
```

Los tests no llaman a OpenAI real.

## Flujo de uso

1. Crear un nuevo proyecto desde el panel.
2. Seleccionar idioma, plantilla y archivo.
3. La app guarda el original y encola un job.
4. El worker procesa audio, chunks, transcripción, resumen y dossier.
5. La vista de detalle muestra estado y logs por HTMX.
6. Al completar, se pueden descargar Markdown y DOCX.

## Limitaciones actuales

- Herramienta interna sin login.
- Almacenamiento local.
- Sin edición colaborativa ni revisión avanzada.
- Sin detección de hablantes.
- Sin marcas de tiempo en el dossier final.
- Sin exportación PDF.
- La calidad depende de OpenAI y de la calidad del audio.

## Despliegue Dokploy

Para esta versión usa **Build Type: Dockerfile**. La app escucha en `PORT=3000`
con Gunicorn. No uses Nixpacks para este MVP porque el worker, ffmpeg y las
dependencias Python forman parte del runtime.

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
- Exportación PDF.
- Generación de posts para redes.
- Modo multi-evento.
