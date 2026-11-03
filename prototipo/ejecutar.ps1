# SOME — Sistema de Observación del Mar Equinoccial (prototipo demostrativo)
# Crea o activa el entorno virtual, instala las dependencias y arranca el servidor en http://127.0.0.1:8050
# Uso: .\ejecutar.ps1   (si PowerShell bloquea el script: powershell -ExecutionPolicy Bypass -File .\ejecutar.ps1)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location -Path $PSScriptRoot
# Contacto en el User-Agent de las peticiones al CHC, como pide el CHC. Se respeta un valor ya definido.

Write-Host ""
Write-Host "SOME · Sistema de Observación del Mar Equinoccial — prototipo demostrativo" -ForegroundColor Cyan
Write-Host "Datos reales: NOAA OISST, Copernicus Marine y CHIRPS v3 (si una fuente no responde y su caché no tiene el dato, el SOME usa el simulador y lo señala)" -ForegroundColor Yellow
Write-Host "Copernicus Marine necesita una cuenta gratuita: si aún no lo hizo, ejecute una vez «copernicusmarine login»." -ForegroundColor Yellow
Write-Host ""

# 1. Entorno virtual
if (-not (Test-Path ".venv")) {
    Write-Host "Creando el entorno virtual .venv…"
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el entorno virtual. ¿Está instalado Python 3.13?" }
}
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "No se encontró $python" }

# 2. Dependencias (silencioso si ya están instaladas)
Write-Host "Comprobando dependencias…"
& $python -m pip install --quiet --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "La instalación de dependencias falló." }
# Opcional: réplica dynamical.org de CHIRPS v3 (icechunk, zarr). Si falla, el SOME usa solo el servidor del CHC.
& $python -m pip install --quiet --disable-pip-version-check -r requirements-replica.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "Aviso: no se pudieron instalar icechunk y zarr; CHIRPS usará solo el servidor del CHC y la caché local." -ForegroundColor Yellow
}
# Opcional: Copernicus Marine Toolbox (datos reales de Copernicus). Si falla, Copernicus usa la caché local y,
# lo que no esté en ella, datos simulados señalados como tales.
& $python -m pip install --quiet --disable-pip-version-check -r requirements-copernicus.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "Aviso: no se pudo instalar copernicusmarine; Copernicus Marine usará la caché local o datos simulados señalados." -ForegroundColor Yellow
}
# Opcional: caché local de NOAA OISST v2.1. Solo si está vacía se llena antes de arrancar (unos 20 minutos: NOAA PSL
# limita el ritmo de descarga). Si falla o se interrumpe, el servidor arranca igual y la completa en segundo plano.
$sinRedNoaa = @("1", "true", "si", "sí", "yes") -contains "$env:SOME_NOAA_SIN_RED".Trim().ToLower()
$modoNoaa = "$env:SOME_NOAA_MODO".Trim().ToLower()
$cacheNoaa = if ($env:SOME_RUTA_NOAA) { $env:SOME_RUTA_NOAA } else { Join-Path $PSScriptRoot "datos\noaa" }
if (-not $sinRedNoaa -and $modoNoaa -ne "simulado" -and -not (Test-Path (Join-Path $cacheNoaa "diario\*.npz"))) {
    Write-Host "La caché de NOAA OISST está vacía: se prepara ahora (unos 20 minutos; NOAA PSL limita el ritmo de descarga)…"
    & $python -m backend.herramientas.preparar_noaa
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Aviso: la caché de NOAA OISST quedó incompleta; el SOME la completará en segundo plano y señalará lo que falte." -ForegroundColor Yellow
    }
}

# 3. Servidor
Write-Host ""
Write-Host "URL de la demo:  http://127.0.0.1:8050/" -ForegroundColor Green
Write-Host "Documentación:   http://127.0.0.1:8050/api/docs"
Write-Host ""
Write-Host "Usuarios de demostración (clave: some2026):"
Write-Host "  mvera    Dra. María Vera        oceanógrafa"
Write-Host "  jloor    Ing. Jorge Loor        analista"
Write-Host "  rcastro  CPFG Ricardo Castro    revisor (jefe de departamento)"
Write-Host "  admin    Administrador de TI    administrador"
Write-Host ""
Write-Host "Presione Ctrl+C para detener el servidor."
Write-Host ""

& $python -m uvicorn backend.app:app --host 127.0.0.1 --port 8050
