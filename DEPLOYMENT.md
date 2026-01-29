# 🚀 Deployment Guide - WhatsApp Fuel Extractor

## Overview

This guide deploys two services to your VPS (`93.127.202.11`):
- **Evolution API** → `wa.firesideafrica.cloud` (WhatsApp connectivity)
- **Fuel Extractor** → `fuel.firesideafrica.cloud` (Dashboard & webhook)

---

## Prerequisites

- VPS with Docker & Docker Compose installed
- Domain DNS configured (already done ✅):
  - `wa.firesideafrica.cloud` → `93.127.202.11`
  - `fuel.firesideafrica.cloud` → `93.127.202.11`
- Coolify or Traefik for SSL/reverse proxy

---

## Step 1: SSH into your VPS

```bash
ssh root@93.127.202.11
# or
ssh root@firesideafrica.cloud
```

---

## Step 2: Create project directory

```bash
mkdir -p /opt/apps
cd /opt/apps
```

---

## Step 3: Clone Evolution API (if not already running)

If you don't have Evolution API running yet:

```bash
git clone https://github.com/josephndex/Evolution_api.git evolution-api
cd evolution-api
```

Make sure Evolution API has these settings in `docker-compose.yml`:
```yaml
services:
  evolution-api:
    ports:
      - "8080:8080"
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.evolution.rule=Host(`wa.firesideafrica.cloud`)"
      - "traefik.http.routers.evolution.entrypoints=websecure"
      - "traefik.http.routers.evolution.tls.certresolver=letsencrypt"
```

Start Evolution API:
```bash
docker-compose up -d
```

---

## Step 4: Clone Fuel Extractor

```bash
cd /opt/apps
git clone https://github.com/YOUR_USERNAME/whatsapp-fuel-extractor.git fuel-extractor
cd fuel-extractor
```

---

## Step 5: Verify configuration

Check that these files exist and have correct values:
- `.env` - Environment variables
- `config.json` - App configuration  
- `google_credentials.json` - Google Sheets API credentials

```bash
# Check .env
cat .env

# Verify Evolution API URL points to your domain
grep EVOLUTION .env
# Should show: EVOLUTION_API_URL=https://wa.firesideafrica.cloud
```

---

## Step 6: Build and start the Fuel Extractor

```bash
# Build the Docker image
docker-compose build

# Start in background
docker-compose up -d

# Check logs
docker-compose logs -f fuel-extractor
```

---

## Step 7: Configure Coolify (if using Coolify)

If you're using Coolify for deployment:

1. Go to Coolify dashboard (`coolify.firesideafrica.cloud` or your Coolify URL)
2. Add new service → Docker Compose
3. Connect to your GitHub repo
4. Set the domain: `fuel.firesideafrica.cloud`
5. Deploy

---

## Step 8: Configure Evolution API webhook

After both services are running, configure the webhook:

```bash
# From your local machine or VPS
curl -X POST "https://wa.firesideafrica.cloud/webhook/set/fuel-extractor" \
  -H "apikey: B6D711FCDE4D4FD5936544120E713976" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://fuel.firesideafrica.cloud/webhook/evolution",
    "webhook_by_events": true,
    "events": [
      "MESSAGES_UPSERT",
      "CONNECTION_UPDATE",
      "QRCODE_UPDATED"
    ]
  }'
```

---

## Step 9: Verify everything works

```bash
# Check Fuel Extractor health
curl https://fuel.firesideafrica.cloud/api/health

# Check Evolution API health  
curl https://wa.firesideafrica.cloud/instance/connectionState/fuel-extractor \
  -H "apikey: B6D711FCDE4D4FD5936544120E713976"
```

Open in browser:
- Dashboard: https://fuel.firesideafrica.cloud
- Records: https://fuel.firesideafrica.cloud/records

---

## Updating the application

```bash
cd /opt/apps/fuel-extractor

# Pull latest changes
git pull

# Rebuild and restart
docker-compose down
docker-compose build
docker-compose up -d
```

---

## Troubleshooting

### Check container status
```bash
docker ps
docker-compose logs -f
```

### Check Evolution API connection
```bash
docker-compose exec fuel-extractor curl http://evolution-api:8080/health
```

### Restart services
```bash
docker-compose restart
```

### View real-time logs
```bash
docker-compose logs -f fuel-extractor
```

### Database connection issues
Make sure your VPS can reach `100.83.80.26:3306` (your Tailscale MySQL server).

---

## Architecture

```
                                    ┌─────────────────────────┐
                                    │   firesideafrica.cloud   │
                                    │    DNS: 93.127.202.11    │
                                    └───────────┬─────────────┘
                                                │
                        ┌───────────────────────┴───────────────────────┐
                        │                                               │
               ┌────────▼────────┐                            ┌────────▼────────┐
               │  wa.subdomain   │                            │ fuel.subdomain  │
               │  Evolution API  │◄───────webhook────────────►│  Fuel Extractor │
               │   Port 8080     │                            │    Port 8000    │
               └────────┬────────┘                            └────────┬────────┘
                        │                                               │
                   WhatsApp                                    ┌────────┴────────┐
                   Connection                                  │                 │
                                                        ┌──────▼─────┐   ┌───────▼──────┐
                                                        │   MySQL    │   │Google Sheets │
                                                        │100.83.80.26│   │   (Backup)   │
                                                        └────────────┘   └──────────────┘
```

---

## Security Notes

⚠️ **This repository contains credentials!**

Make sure to:
1. Keep the GitHub repository **PRIVATE**
2. Use strong passwords
3. Regularly rotate API keys
4. Use Tailscale or VPN for database access

---

## Quick Commands Reference

| Command | Description |
|---------|-------------|
| `docker-compose up -d` | Start services |
| `docker-compose down` | Stop services |
| `docker-compose logs -f` | View logs |
| `docker-compose restart` | Restart services |
| `docker-compose build` | Rebuild image |
| `docker-compose pull && docker-compose up -d` | Update |
