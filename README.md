# Karaoke PC + Karaoke Móvil

Karaoke que usa el **celular como micrófono por Wi‑Fi**. La PC reproduce la pista (o una entrada en vivo), mezcla la voz recibida por UDP y saca el resultado por los parlantes.

## Cómo funciona

```
Celular (app Karaoke Móvil) --UDP:50007--> PC (Karaoke PC) --> parlantes
                                          |
                                          +-- HTTP:50007 --> página de conexión y APK
```

- `pc/`: aplicación de escritorio (Tkinter + sounddevice) y servidor de la página de conexión.
- `android/`: app nativa (Kotlin) que captura el micro y envía audio en tiempo real.
- `karaoke-mic.apk`: APK de depuración ya compilado; la PC lo sirve para instalarlo desde el celular.

## Requisitos

- Windows 10/11 con Python 3.10 o superior.
- Android 5.0 (API 21) o superior.
- PC y celular en la misma red Wi‑Fi.
- Opcional: VB‑CABLE para cantar sobre audio de YouTube u otra app.
- Opcional: `segno` para mostrar el QR en la PC (si falta, se puede copiar el enlace).

## Uso rápido

1. Doble clic en `iniciar_karaoke.bat`. El lanzador instala las dependencias la primera vez y abre la ventana **Karaoke PC**.
2. En la PC, tocar **Conectar celular (QR)** y escanear el código con la cámara del celular (o copiar/abrir el enlace).
3. En el celular, instalar la app desde la página, permitir el micrófono y tocar **Conectar**.
4. Cargar una pista con **Buscar...** y tocar **Reproducir**. Ajustar música, micro y puerta de ruido.

Si hay un archivo `.lrc` con el mismo nombre que la pista, la letra se muestra sincronizada.

### Entrada en vivo (YouTube, Spotify, etc.)

1. Enrutar la música de la otra app a **CABLE Input**.
2. Elegir **Entrada en vivo** como fuente y seleccionar **CABLE Output** en el desplegable.
3. Elegir los parlantes reales en **Salida** y apretar **Aplicar**.
4. Tocar **Reproducir** para comenzar a escuchar (en vivo no hace falta cargar una pista).

La app cambia el dispositivo de reproducción por defecto a CABLE Input al iniciar y lo restaura al cerrar. Si queda cambiado tras un cierre forzado, ejecutar `restaurar_audio.bat` (acepta un ID amigable de SoundVolumeView como parámetro; por defecto usa `Realtek(R) Audio\Device\Speaker\Render`).

`herramientas/SoundVolumeView.exe` es una utilidad de NirSoft. Se puede borrar: el karaoke sigue funcionando, solo se pierde el cambio automático de dispositivo.

## Instalación manual

```powershell
python -m pip install -r pc\requirements.txt
python pc\karaoke_pc.py
```

Las preferencias de dispositivos se guardan en `pc/config.json` (se crea automáticamente; no se versiona).

## Compilar el APK

```powershell
cd android
.\gradlew assembleDebug
```

El APK queda en `android\app\build\outputs\apk\debug\app-debug.apk`. Copiarlo a la raíz como `karaoke-mic.apk` para que la PC lo sirva. La compilación usa el wrapper de Gradle 8.9, AGP 8.7.3 y Kotlin 2.0.21; hace falta Android SDK 35 y JDK 17.

## Protocolo

- Audio: UDP, paquetes `KRM1` con cabecera `<4sII` (magic, secuencia, sample rate) y PCM16 mono. Por defecto 48 kHz, 960 muestras (20 ms) por paquete.
- Confirmación: `KRM2` con `<4sII` (magic, paquetes recibidos, perdidos), enviada por la PC cada 0,5 s.
- Puerto 50007: UDP para el micro y TCP/HTTP para la página de conexión y la descarga del APK. Si el HTTP está ocupado, la PC prueba los siguientes 9 puertos.
- El motor de audio usa un buffer de 8 frames, ocultación de paquetes perdidos y remuestreo a la tasa de salida disponible.

## Pruebas

```powershell
python -m unittest discover -s pc -p "test_*.py" -v
python pc\smoke_test.py
```

`smoke_test.py` necesita un dispositivo de audio funcionando; los tests unitarios no.

## Limitaciones

- El tráfico UDP y HTTP no tiene autenticación ni cifrado: usar solo en una red local de confianza.
- El APK incluido es de depuración (no firmado para release).
- El botón **Escanear QR** de la app abre la cámara del celular; la decodificación la hace la app de cámara, no la app del karaoke. También se puede usar **Copiar enlace** en la PC.
- No hay control de acceso a la página que sirve el APK.
