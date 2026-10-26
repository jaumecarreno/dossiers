# Controles de integridad de transcripción — Dossiers 1.12

## Problema corregido

Enviar unos 20 minutos a `gpt-4o-transcribe` permitía recibir respuestas que
alcanzaban el límite de salida y dejaban fuera el resto del audio. Se aceptaban
como completas porque la petición HTTP había terminado correctamente.

Los proyectos existentes no se migran ni se vuelven a procesar al desplegar.
Los proyectos 45 y 46 solo se consultaron como referencia de lectura.

## Comportamiento

- Cada petición nueva cubre como máximo unos 30 segundos (más el pequeño
  relleno del códec). Las configuraciones antiguas de 3 o 20 minutos no evitan este límite.
  La variable de configuración actual es `TRANSCRIPT_CHUNK_SECONDS=30`.
- Se mantienen 2 segundos de solapamiento. Solo se eliminan coincidencias
  exactas al unir extremos; no se usa otra IA para reconstruir uniones.
- Los modelos GPT-4o de transcripción se comprueban contra un umbral conservador
  de 1.900 tokens, tanto con el uso informado por la API como con un tokenizador
  local. También se rechaza una indicación explícita de límite de generación.
- Respuestas vacías o inusualmente breves se revisan en intervalos menores.
  Una respuesta vacía solo se acepta si ffmpeg confirma silencio en todo el
  intervalo con un umbral de -50 dB. Esto es una comprobación de silencio,
  no un detector semántico de voz.
- Los fragmentos sospechosos se dividen en dos, con un máximo de tres niveles
  de recuperación. Una respuesta recortada nunca se mezcla con sus reemplazos.
  Si algún reemplazo falla, no se acepta el fragmento entero ni se genera un
  dossier a partir del texto parcial.
- Se guardan los fragmentos completos que pasan los controles; un reintento
  explícito reutiliza esos fragmentos. Una interrupción dentro de la recuperación
  de un fragmento puede requerir repetir ese fragmento, pero no el proyecto entero.
- Se comprueba que los intervalos de los fragmentos cubran el audio sin saltos
  y se conserva la fracción final de segundo al cortar los archivos.
- El idioma del dossier sigue siendo el seleccionado por el usuario. El audio
  se transcribe con detección automática para conservar sesiones bilingües.
- La limpieza normaliza saltos de línea sin reescribir, traducir ni eliminar
  palabras. No hace una llamada de IA. La corrección editorial sigue disponible
  en la transcripción revisada.
- Sin alineación proporcionada por el modelo, los tiempos corresponden a los
  intervalos de audio enviados. No se presentan como tiempos exactos de palabras.
  Si los segmentos temporales no contienen todo el texto, la exportación conserva
  el texto completo del fragmento.

## Coste y funcionamiento

Una respuesta normal requiere una sola petición de transcripción por fragmento.
Los controles del texto son locales, sin coste de tokens. Los reintentos de
transporte están limitados a cuatro intentos y no se multiplican por reintentos
internos del SDK. Los reintentos por contenido vuelven a enviar únicamente el
intervalo sospechoso, dividido.

La prueba con 5.960,75 segundos obtiene 213 fragmentos y un 7,11 % de audio
adicional por solapamiento sin silencios. El porcentaje varía con los cortes.
Los bloques más cortos aumentan el número de peticiones y de contextos enviados frente a la versión 1.1. No es una estimación del coste total: también intervienen el texto de salida,
el contexto y los reintentos. El contexto se limita a 384 tokens por petición,
con un máximo de 60 palabras del final del texto anterior. Se elimina el coste
de la antigua llamada de limpieza. La transcripción completa tiene más texto
de salida que una transcripción erróneamente recortada; esa diferencia es necesaria.

El contenedor descarga el tokenizador durante la construcción para que el
worker no necesite descargarlo al iniciar una transcripción. En ejecución Python
sin Docker, `tiktoken` descarga su recurso la primera vez; puede precargarse con
`python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"`.

## Validación realizada

- Suite de 92 pruebas aprobada, incluyendo la extracción real de audio con ffmpeg.
- Reproducción simulada del recorte a 2.047 tokens; recuperación de ambas mitades,
  offsets, límites de reintento y rechazo de resultados parcialmente recuperados.
- Prueba del worker con base de datos: si la recuperación falla, el proyecto y
  fragmento quedan fallidos, no se genera el resumen y se conserva el fragmento
  anterior que ya estaba comprobado.
- Exportación con segmentos vacíos o incompletos sin pérdida del texto original.
- Conservación de texto largo bilingüe durante la limpieza, sin llamadas a IA.
- Corte real de audio de 31,75 segundos con una señal solo en el último cuarto
  de segundo; la señal sobrevive al corte y a la extracción para recuperación.
- Lectura de las exportaciones existentes: el detector rechaza los cinco bloques
  de 2.047 tokens del proyecto 45 y el primer bloque del proyecto 46. Los otros dos
  bloques del 46 no alcanzan el umbral; eso no certifica su cobertura.

No se ha realizado una nueva transcripción pagada ni una comparación auditiva
completa con los vídeos originales. Estos controles corrigen el truncamiento
observado y detectan algunos otros fallos, pero no prueban la exactitud de cada
palabra ni permiten garantizar que un modelo nunca omita una frase. Silencio,
música o audio muy difícil pueden requerir revisión manual.

## Exportación corregida en 1.12

El procesado y la exportación usan la misma unión conservadora. Antes, la
exportación con tiempos reconstruía los fragmentos sin quitar el prefijo que
ya había eliminado el procesado. Se comprueba que todas las palabras coincidan
con la transcripción principal; si los datos antiguos no permiten reconstruirla,
se entrega el texto principal completo sin inventar marcas de tiempo.

Los intervalos sintéticos, los segmentos parciales y las uniones recortadas se
presentan como referencias de inicio «aprox.». No son alineación de palabras.
Se conservan los rangos del proveedor solo si contienen todo el texto y no se
solapan. La revisión humana sigue teniendo prioridad. No hay llamadas nuevas de
IA, migraciones ni reprocesado de proyectos existentes.

Comprobación de lectura del proyecto 49: 120 bloques, 6.140 palabras en los
fragmentos del archivo con tiempos frente a 5.989 en el texto principal. La nueva
exportación conserva exactamente las 5.989 palabras y no reintroduce las 151
palabras de diferencia. Este dato comprueba la exportación, no la fidelidad al audio.

### Pendiente para una sincronización precisa

La versión 1.12 corrige la duplicación al exportar; no resuelve toda ambigüedad de
reconocimiento en las uniones. Se mantienen las coincidencias cortas de una o dos
palabras y las variantes que no coinciden exactamente para no borrar habla real.
Para decidirlas hace falta contrastar el audio, con tiempos de palabras o un
alineador. Antes de introducirlo: evaluar español/catalán, pausas, repeticiones
reales y palabras cortadas; medir coste y recursos de despliegue; validar contra
fragmentos de audio escuchados. No basta con cambiar las etiquetas de tiempo ni
con deduplicar una palabra de forma indiscriminada. Los TXT de TurboScribe no
permiten inferir su mecanismo interno. No se ha hecho una nueva prueba pagada.
