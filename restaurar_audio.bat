@echo off
setlocal
set "ID=%~1"
if "%ID%"=="" set "ID=Realtek(R) Audio\Device\Speaker\Render"
if not exist "%~dp0herramientas\SoundVolumeView.exe" (
  echo No se encontro herramientas\SoundVolumeView.exe
  exit /b 1
)
"%~dp0herramientas\SoundVolumeView.exe" /SetDefault "%ID%" all
if errorlevel 1 (
  echo No se pudo establecer el dispositivo: %ID%
  echo Uso: restaurar_audio.bat "ID amigable de SoundVolumeView"
  exit /b 1
)
echo Salida predeterminada restaurada: %ID%
