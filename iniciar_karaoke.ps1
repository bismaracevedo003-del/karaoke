$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$script = Join-Path $root 'pc\karaoke_pc.py'
$requirements = Join-Path $root 'pc\requirements.txt'

$command = Get-Command python -ErrorAction SilentlyContinue
if (-not $command) {
    Write-Error 'No se encontró Python en el PATH. Instalá Python 3.10 o superior y volvé a abrir el archivo.'
    exit 1
}
$python = $command.Source

if (-not (Test-Path -LiteralPath $script)) {
    Write-Error "No se encontró $script"
    exit 1
}

& $python -c "import numpy, sounddevice, soundfile, segno" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Instalando dependencias de Python...'
    & $python -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) {
        Write-Error 'No se pudieron instalar las dependencias. Revisá tu instalación de Python.'
        exit 1
    }
}

& $python $script
exit $LASTEXITCODE
