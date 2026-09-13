@echo off
setlocal
set "JEET_ROOT=%~dp0.."

if not exist "%JEET_ROOT%\.venv\Scripts\python.exe" (
  echo Missing .venv. Run: py -m venv .venv
  echo Then run: .venv\Scripts\python -m pip install -e ".[ui,test]"
  exit /b 1
)

if not exist "%JEET_ROOT%\frontend\node_modules" (
  echo Missing frontend dependencies. Run npm install in frontend.
  exit /b 1
)

start "Jeet Analyzer API" cmd /k "cd /d ""%JEET_ROOT%"" && .venv\Scripts\python -m jeet_analyzer_api"
start "Jeet Analyzer UI" cmd /k "cd /d ""%JEET_ROOT%\frontend"" && npm run dev"
endlocal
