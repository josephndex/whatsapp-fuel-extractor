# 🚀 Deployment Guide - WhatsApp Fuel Extractor

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         firesideafrica.cloud                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  fuel.firesideafrica.cloud        wa.firesideafrica.cloud              │
│  ┌─────────────────────┐          ┌─────────────────────┐              │
│  │   Fuel Extractor    │◄────────►│   Evolution API     │              │
│  │   (Port 8000)       │          │   (Port 8080)       │              │
│  └─────────────────────┘          └─────────────────────┘              │
│            │                               │                            │
│            ▼                               ▼                            │
│  ┌─────────────────────┐          ┌─────────────────────┐              │
│  │   MySQL (Remote)    │          │   PostgreSQL+Redis  │              │
│  │   100.83.80.26      │          │   (Docker)          │              │
│  └─────────────────────┘          └─────────────────────┘              │
│                                                                         │
│                    Coolify (coolify.firesideafrica.cloud)              │
└─────────────────────────────────────────────────────────────────────────┘
```

## Subdomains Setup

| Subdomain | Service | Port |
|-----------|---------|------|
| `fuel.firesideafrica.cloud` | Fuel Extractor Web App | 8000 |
| `wa.firesideafrica.cloud` | Evolution API (WhatsApp) | 8080 |
| `coolify.firesideafrica.cloud` | Coolify Dashboard | 8000 |

---

## Step 1: DNS Setup (Hostinger)

Go to Hostinger DNS settings for `firesideafrica.cloud` and add:

| Type | Name | Value | TTL |
|------|------|-------|-----|
| A | @ | `93.127.202.11` | 3600 |
| A | fuel | `93.127.202.11` | 3600 |
| A | wa | `93.127.202.11` | 3600 |
| A | coolify | `93.127.202.11` | 3600 |

---

## Step 2: SSH into VM

```bash
ssh nderitu@93.127.202.11
```

---

## Step 3: Deploy with Coolify

### 3.1 Access Coolify Dashboard
Open: `https://coolify.firesideafrica.cloud`

### 3.2 Add GitHub Repository

1. Go to **Projects** → **NDERITU LABS** → **+ Add Resource**
2. Select **Public Repository**
3. Enter: `https://github.com/josephndex/whatsapp-fuel-extractor.git`
4. Select **Docker Compose**
5. Choose `docker-compose.yml`

### 3.3 Configure Environment Variables

In Coolify, add these environment variables:

```env
DB_HOST=100.83.80.26
DB_NAME=logistics_department
DB_USER=RITA
DB_PASSWORD=RITANDEX101/
DB_PORT=3306
DB_DRIVER=mysql+pymysql
GOOGLE_SHEETS_SPREADSHEET_ID=1gAq2TUBWPIKUAXRcHYeeq85ltktWgXUoe9QDDkRYSQo
EVOLUTION_API_KEY=B6D711FCDE4D4FD5936544120E713976
EVOLUTION_INSTANCE_NAME=fuel-extractor
```

### 3.4 Configure Domains

**For Fuel Extractor:**
- Domain: `fuel.firesideafrica.cloud`
- Port: `8000`
- Enable SSL (Let's Encrypt)

**For Evolution API:**
- Domain: `wa.firesideafrica.cloud`
- Port: `8080`
- Enable SSL (Let's Encrypt)

### 3.5 Deploy

Click **Deploy** and wait for containers to start.

---

## Step 4: Initialize WhatsApp Connection

1. Open: `https://wa.firesideafrica.cloud`
2. Create instance if needed:

```bash
curl -X POST "https://wa.firesideafrica.cloud/instance/create" \
  -H "apikey: B6D711FCDE4D4FD5936544120E713976" \
  -H "Content-Type: application/json" \
  -d '{
    "instanceName": "fuel-extractor",
    "qrcode": true,
    "integration": "WHATSAPP-BAILEYS"
  }'
```

3. Get QR Code:
```bash
curl "https://wa.firesideafrica.cloud/instance/connect/fuel-extractor" \
  -H "apikey: B6D711FCDE4D4FD5936544120E713976"
```

4. Scan QR with WhatsApp

5. Set Webhook:
```bash
curl -X POST "https://wa.firesideafrica.cloud/webhook/set/fuel-extractor" \
  -H "apikey: B6D711FCDE4D4FD5936544120E713976" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://fuel.firesideafrica.cloud/webhook/evolution",
    "webhook_by_events": false,
    "webhook_base64": false,
    "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"]
  }'
```

---

## Step 5: Verify Deployment

### Check Fuel Extractor:
```bash
curl https://fuel.firesideafrica.cloud/api/health
```

### Check Evolution API:
```bash
curl https://wa.firesideafrica.cloud/ -H "apikey: B6D711FCDE4D4FD5936544120E713976"
```

### Access Web Dashboard:
- URL: `https://fuel.firesideafrica.cloud`
- Admin: `https://fuel.firesideafrica.cloud/admin`
- Password: `Nala2025`
- Audit Log Password: `NDERITU101`

---

## Credentials Summary

| Service | URL | Credentials |
|---------|-----|-------------|
| Fuel Dashboard | fuel.firesideafrica.cloud | Admin: `Nala2025` |
| Audit Log | fuel.firesideafrica.cloud/admin | `NDERITU101` |
| Evolution API | wa.firesideafrica.cloud | Key: `B6D711FCDE4D4FD5936544120E713976` |
| MySQL | 100.83.80.26:3306 | User: `RITA`, Pass: `RITANDEX101/` |
| Google Sheets | - | ID: `1gAq2TUBWPIKUAXRcHYeeq85ltktWgXUoe9QDDkRYSQo` |

---

## Troubleshooting

### View Logs
```bash
# In Coolify or via SSH:
docker logs fuel-extractor -f
docker logs evolution-api -f
```

### Restart Services
```bash
docker restart fuel-extractor
docker restart evolution-api
```

### Check Network
```bash
docker network ls
docker network inspect coolify
```
