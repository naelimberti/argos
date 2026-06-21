# ============================================================
# ARGOS — Script de déploiement Fly.io
# Exécuter dans PowerShell depuis le dossier du projet
# ============================================================

Set-Location "C:\Users\naeli\OneDrive\Desktop\administratif\vie pro\PROJETS\argos"

Write-Host ""
Write-Host "=== ARGOS — Déploiement Fly.io ===" -ForegroundColor Cyan
Write-Host ""

# 1. Installer flyctl si absent
if (-not (Get-Command flyctl -ErrorAction SilentlyContinue)) {
    Write-Host "[1/6] Installation de flyctl..." -ForegroundColor Yellow
    powershell -Command "iwr https://fly.io/install.ps1 -useb | iex"
    $env:PATH += ";$env:USERPROFILE\.fly\bin"
} else {
    Write-Host "[1/6] flyctl deja installe." -ForegroundColor Green
}

# 2. Connexion
Write-Host ""
Write-Host "[2/6] Connexion a Fly.io (navigateur va s'ouvrir)..." -ForegroundColor Yellow
flyctl auth login

# 3. Créer l'app (si première fois)
Write-Host ""
Write-Host "[3/6] Creation de l'application..." -ForegroundColor Yellow
flyctl apps create argos-trading --org personal 2>&1
# Ignore l'erreur si l'app existe déjà

# 4. Créer le volume persistant (1 Go, gratuit)
Write-Host ""
Write-Host "[4/6] Creation du volume persistant (SQLite + logs)..." -ForegroundColor Yellow
flyctl volumes create argos_data --region cdg --size 1 --app argos-trading 2>&1
# Ignore l'erreur si le volume existe déjà

# 5. Premier déploiement
Write-Host ""
Write-Host "[5/6] Deploiement en cours (2-3 minutes)..." -ForegroundColor Yellow
flyctl deploy --app argos-trading --remote-only

# 6. Statut
Write-Host ""
Write-Host "[6/6] Verification du deploiement..." -ForegroundColor Yellow
flyctl status --app argos-trading

Write-Host ""
Write-Host "=== Deploiement termine ===" -ForegroundColor Green
Write-Host ""
flyctl info --app argos-trading
Write-Host ""
Write-Host "Dashboard disponible sur : https://argos-trading.fly.dev" -ForegroundColor Cyan
Write-Host "Logs en direct            : flyctl logs --app argos-trading" -ForegroundColor Cyan
Write-Host ""
