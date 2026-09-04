@echo off
REM Qdrant vector database (ADR-007: native Windows, no Docker).
REM
REM Qdrant MUST be started from its own directory: it resolves config\config.yaml,
REM .\storage and .\static relative to the current working directory. Launching
REM qdrant.exe by full path from elsewhere silently loses all three.
REM
REM config\config.yaml binds to 127.0.0.1. Qdrant's built-in default is
REM 0.0.0.0 with no authentication - see docs/security/security-model.md.

set QDRANT_HOME=C:\qdrant

if not exist "%QDRANT_HOME%\qdrant.exe" (
    echo.
    echo   qdrant.exe not found in %QDRANT_HOME%
    echo.
    echo   Download qdrant-x86_64-pc-windows-msvc.zip from
    echo   https://github.com/qdrant/qdrant/releases and extract it there,
    echo   or set QDRANT_HOME above to wherever it lives.
    echo.
    pause
    exit /b 1
)

cd /d "%QDRANT_HOME%"

echo Starting Qdrant on http://127.0.0.1:6333  ^(dashboard: /dashboard^)
qdrant.exe
