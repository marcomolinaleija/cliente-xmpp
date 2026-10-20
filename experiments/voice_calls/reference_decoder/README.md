# Decodificación independiente de paquetes sintéticos

Herramienta de prueba, **no dependencia del puente**. Lee exactamente los 84
paquetes generados por `codecprobe --export-synthetic`: tono, piso de ruido,
tono. No admite una cuenta, no graba micrófonos y no inicia llamadas.

La fuente del decodificador se copia sin modificar desde `whatsapp-rust`
`d1d929aeb3ea9a10ee61cb87a3176b72e90f3dd2`. Su licencia MIT permanece en el
archivo fuente descargado. El adaptador local sólo proporciona descompresión
zlib de tablas y generación de tipos a partir del descriptor upstream; no
reescribe el análisis, lector de tramas o síntesis. Cargo fija versiones directas;
conservar también su `Cargo.lock` y la imagen resultante con la evidencia.

Preparar en un directorio de prueba Linux, nunca junto a datos productivos:

```sh
curl -fL https://codeload.github.com/oxidezap/whatsapp-rust/tar.gz/d1d929aeb3ea9a10ee61cb87a3176b72e90f3dd2 -o rust-reference.tar.gz
echo 'ea382ee899546e7581f9cd135bd165cddf5cf6840460e1a6fcc5c1967989f014  rust-reference.tar.gz' | sha256sum -c -
tar -xzf rust-reference.tar.gz
cp -a whatsapp-rust-d1d929aeb3ea9a10ee61cb87a3176b72e90f3dd2/wacore/src/voip/mlow reference_decoder/mlow
cp whatsapp-rust-d1d929aeb3ea9a10ee61cb87a3176b72e90f3dd2/LICENSE reference_decoder/LICENSE.upstream
docker build -t can-voice-reference reference_decoder
```

`reference_decoder` debe contener previamente los cuatro archivos de esta
carpeta. Para una imagen de probe construida con las fuentes actuales:

```sh
docker run --rm --network none --entrypoint /usr/local/bin/can-codec-probe "$PROBE_IMAGE" --export-synthetic > outgoing.json
docker run --rm -i --network none --cap-drop ALL --security-opt no-new-privileges --memory 1g --cpus 2 can-voice-reference < outgoing.json
```

El criterio exige 252 subtramas de 20 ms decodificadas, cero ocultaciones o
rechazos y energía del segundo segmento de tono dentro de ±6 dB del primero.
El resultado distingue **decodificabilidad del cuerpo** de **actividad correcta**:
la versión anterior también tenía cuerpo decodificable, pero etiquetaba los 84
paquetes como voz. Las regresiones Go comprueban adicionalmente las tres clases
de actividad y el silencio digital.

Control negativo: cambiar `10` por `50` sólo en el primer byte de las tramas
inactivas, conservando el cuerpo. El lector independiente debe rechazar esa
secuencia. El 20 de octubre ocultó las 34 tramas alteradas, frente a cero en la
secuencia correcta. Así se comprueba que el ensayo detecta una cabecera que no
corresponde a sus símbolos y geometría, no sólo salida finita del decodificador
propio.

Este lector procede de la misma familia de implementación que el port Go,
pero ejecuta otra implementación y valida el final del cuerpo. No es el
decodificador del teléfono oficial ni una prueba perceptual o de red real.
