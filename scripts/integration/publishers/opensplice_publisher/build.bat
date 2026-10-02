@echo off
rem Build the OpenSplice demo participant on Windows with the prebuilt HDE.
rem EXPERIMENTAL, see README.md. Run from an "x64 Native Tools Command Prompt
rem for VS 2019" (or later) so that cl.exe is on PATH. Output: build\ospl_publisher.exe
setlocal EnableDelayedExpansion

set "HERE=%~dp0"

if "%OSPL_HOME%"=="" (
    echo [build.bat] OSPL_HOME is not set.
    echo Download the prebuilt HDE from the GitHub release OSPL_V6_9_210323OSS of
    echo ADLINK-IST/opensplice ^(x86_64.win-vs2019-installer.zip^), extract it, then
    echo in this prompt run:  call ^<extracted dir^>\HDE\x86_64.win64\release.bat
    echo and run build.bat again. Do NOT try to compile OpenSplice from source.
    exit /b 1
)
if "%OSPL_HOME:~-1%"=="\" set "OSPL_HOME=%OSPL_HOME:~0,-1%"

if not exist "!OSPL_HOME!\bin\idlpp.exe" (
    echo [build.bat] !OSPL_HOME!\bin\idlpp.exe not found: OSPL_HOME is not an HDE.
    exit /b 1
)
where cl >nul 2>&1
if errorlevel 1 (
    echo [build.bat] cl.exe not found: use a Visual Studio x64 Developer Command Prompt.
    exit /b 1
)

if not exist "!HERE!build" mkdir "!HERE!build"
pushd "!HERE!build"

rem idlpp writes its output in the current directory. -S is the standalone mode.
"!OSPL_HOME!\bin\idlpp.exe" -S -l c -I "!OSPL_HOME!\etc\idl" "!HERE!Status.idl"
if errorlevel 1 goto fail

rem Link only the HDE import libraries that exist, the set varies between builds.
set "LIBS="
for %%L in (dcpssac ddsuser ddskernel ddsserialization ddsdatabase ddsutil ddsconf ddsconfparser ddsos) do (
    if exist "!OSPL_HOME!\lib\%%L.lib" set "LIBS=!LIBS! %%L.lib"
)
if "!LIBS!"=="" (
    echo [build.bat] no OpenSplice import libraries found in !OSPL_HOME!\lib.
    goto fail
)

cl /nologo /MD /O1 /I. /I"!OSPL_HOME!\include" /I"!OSPL_HOME!\include\sys" /I"!OSPL_HOME!\include\dcps\C\SAC" /Fe:ospl_publisher.exe StatusSacDcps.c StatusSplDcps.c "!HERE!src\ospl_publisher.c" /link /LIBPATH:"!OSPL_HOME!\lib" !LIBS!
if errorlevel 1 goto fail

popd
echo [build.bat] built !HERE!build\ospl_publisher.exe
exit /b 0

:fail
popd
echo [build.bat] build failed, see README.md.
exit /b 1
