@echo off
:: Lanceur ARGOS — Paper Trading Bot
:: Démarre le scheduler + le dashboard Streamlit

cd /d "C:\Users\naeli\OneDrive\Desktop\administratif\vie pro\PROJETS\argos"

echo [ARGOS] Démarrage du bot paper trading...
echo [ARGOS] Logs : logs\argos.log
echo [ARGOS] Dashboard : http://localhost:8501

:: Init DB si première fois
"C:\Users\naeli\.conda\envs\argos\python.exe" -m argos.main init-db

:: Lancer le watchdog (il démarre et surveille le scheduler)
start "ARGOS Watchdog" "C:\Users\naeli\.conda\envs\argos\python.exe" watchdog.py

:: Attendre 3s puis lancer le dashboard
timeout /t 3 /nobreak >nul
start "ARGOS Dashboard" "C:\Users\naeli\.conda\envs\argos\python.exe" -m streamlit run src/argos/dashboard/app.py --server.port 8501

echo [ARGOS] Bot démarré. Ferme cette fenêtre pour laisser tourner en arrière-plan.
timeout /t 5 /nobreak >nul
