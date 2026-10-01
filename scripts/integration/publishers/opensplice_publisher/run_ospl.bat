@echo off
rem Run the OpenSplice demo participant on Windows. Arguments go to the binary:
rem   set OSPL_HOME=<HDE dir>   then   run_ospl.bat --domain 0
rem EXPERIMENTAL, see README.md.
setlocal EnableDelayedExpansion

set "HERE=%~dp0"
set "BIN=!HERE!build\ospl_publisher.exe"

if "%OSPL_HOME%"=="" (
    echo [run_ospl.bat] OSPL_HOME is not set ^(see README.md^).
    exit /b 1
)
if not exist "!BIN!" (
    echo [run_ospl.bat] !BIN! missing: run build.bat first.
    exit /b 1
)
if "%OSPL_HOME:~-1%"=="\" set "OSPL_HOME=%OSPL_HOME:~0,-1%"

rem The OpenSplice domain comes from Domain/Id of the XML named by OSPL_URI.
set "DOMAIN=0"
set "PREV="
for %%A in (%*) do (
    if "!PREV!"=="--domain" set "DOMAIN=%%A"
    set "PREV=%%A"
)

rem Pick the config before release.bat, which only sets OSPL_URI when empty.
if "%OSPL_URI%"=="" (
    set "CFG=!OSPL_HOME!\etc\config\ospl_sp_ddsi.xml"
    if not exist "!CFG!" set "CFG=!OSPL_HOME!\etc\ospl_sp_ddsi.xml"
    if not exist "!CFG!" (
        echo [run_ospl.bat] ospl_sp_ddsi.xml not found under !OSPL_HOME!\etc.
        exit /b 1
    )
    if not "!DOMAIN!"=="0" (
        set "PATCHED=!HERE!build\ospl_sp_ddsi_domain_!DOMAIN!.xml"
        powershell -NoProfile -Command "(Get-Content -Raw '!CFG!') -replace '<Id>0</Id>','<Id>!DOMAIN!</Id>' | Set-Content -NoNewline '!PATCHED!'"
        set "CFG=!PATCHED!"
    )
    set "OSPL_URI=file://!CFG!"
) else (
    if not "!DOMAIN!"=="0" echo [run_ospl.bat] OSPL_URI is set, --domain !DOMAIN! is ignored: edit Domain/Id there. 1>&2
)

call "!OSPL_HOME!\release.bat" >nul

"!BIN!" %*
exit /b %ERRORLEVEL%
