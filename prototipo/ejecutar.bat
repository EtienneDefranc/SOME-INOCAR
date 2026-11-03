@echo off
rem SOME - Sistema de Observación del Mar Equinoccial (prototipo demostrativo)
rem Doble clic: crea o activa .venv, instala las dependencias y arranca el servidor en http://127.0.0.1:8050
chcp 65001 >nul
cd /d "%~dp0"
rem Contacto en el User-Agent de las peticiones al CHC, como pide el CHC. Se respeta un valor ya definido.

echo.
echo SOME - Sistema de Observación del Mar Equinoccial — prototipo demostrativo
echo Datos reales: NOAA OISST, Copernicus Marine y CHIRPS v3 (si una fuente no responde y su caché no tiene el dato, el SOME usa el simulador y lo señala)
echo Copernicus Marine necesita una cuenta gratuita: si aún no lo hizo, ejecute una vez «copernicusmarine login».
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creando el entorno virtual .venv...
    python -m venv .venv
    if errorlevel 1 (
        echo No se pudo crear el entorno virtual. Compruebe que Python 3.13 esté instalado y en el PATH.
        pause
        exit /b 1
    )
)

echo Comprobando dependencias...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo La instalación de dependencias falló.
    pause
    exit /b 1
)
rem Opcional: réplica dynamical.org de CHIRPS v3 (icechunk, zarr). Si falla, el SOME usa solo el servidor del CHC.
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements-replica.txt
if errorlevel 1 (
    echo Aviso: no se pudieron instalar icechunk y zarr; CHIRPS usará solo el servidor del CHC y la caché local.
)
rem Opcional: Copernicus Marine Toolbox (datos reales de Copernicus). Si falla, Copernicus usa la caché local y,
rem lo que no esté en ella, datos simulados señalados como tales.
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements-copernicus.txt
if errorlevel 1 (
    echo Aviso: no se pudo instalar copernicusmarine; Copernicus Marine usará la caché local o datos simulados señalados.
)
rem Opcional: caché local de NOAA OISST v2.1. Solo si está vacía se llena antes de arrancar (unos 20 minutos: NOAA PSL
rem limita el ritmo de descarga). Si falla o se interrumpe, el servidor arranca igual y la completa en segundo plano.
set "CACHE_NOAA=%~dp0datos\noaa"
if defined SOME_RUTA_NOAA set "CACHE_NOAA=%SOME_RUTA_NOAA%"
if /i "%SOME_NOAA_SIN_RED%"=="1" goto noaa_listo
if /i "%SOME_NOAA_SIN_RED%"=="true" goto noaa_listo
if /i "%SOME_NOAA_MODO%"=="simulado" goto noaa_listo
if exist "%CACHE_NOAA%\diario\*.npz" goto noaa_listo
echo La caché de NOAA OISST está vacía: se prepara ahora (unos 20 minutos; NOAA PSL limita el ritmo de descarga)...
".venv\Scripts\python.exe" -m backend.herramientas.preparar_noaa
if errorlevel 1 echo Aviso: la caché de NOAA OISST quedó incompleta; el SOME la completará en segundo plano y señalará lo que falte.
:noaa_listo

echo.
echo URL de la demo:  http://127.0.0.1:8050/
echo Documentación:   http://127.0.0.1:8050/api/docs
echo.
echo Usuarios de demostración (clave: some2026):
echo   mvera    Dra. María Vera        oceanógrafa
echo   jloor    Ing. Jorge Loor        analista
echo   rcastro  CPFG Ricardo Castro    revisor (jefe de departamento)
echo   admin    Administrador de TI    administrador
echo.
echo Presione Ctrl+C para detener el servidor.
echo.

".venv\Scripts\python.exe" -m uvicorn backend.app:app --host 127.0.0.1 --port 8050
pause
