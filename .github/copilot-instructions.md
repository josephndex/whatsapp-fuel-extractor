# GitHub Copilot Instructions for WhatsApp Fuel Extractor

## Project Overview

A **Python-based automation tool** that captures fueling reports from a WhatsApp group via Evolution API and stores them in MySQL + Google Sheets with real-time validation, admin approval workflows, and a web dashboard.

**Stack:**
- **Python 3.9+** - All backend logic (FastAPI, validation, webhooks)
- **Evolution API** - WhatsApp connectivity via Docker (localhost:8080)
- **MySQL** - Primary data storage
- **Google Sheets** - Backup storage and reporting
- **FastAPI** - Web dashboard and webhook receiver

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                                                                 │
│  WhatsApp      Evolution API       Python Backend         Storage               │
│  ┌───────┐     ┌───────────┐      ┌─────────────┐      ┌──────────┐            │
│  │ User  │────►│  Docker   │─────►│  FastAPI    │─────►│  MySQL   │            │
│  │Message│     │ localhost │      │  :8000      │      │  (primary)│            │
│  └───────┘     │   :8080   │      └─────────────┘      └──────────┘            │
│                └───────────┘             │                   │                  │
│                     │                    │              ┌──────────┐            │
│                  Webhook              Validates         │  Google  │            │
│               /webhook/evolution      Processes         │  Sheets  │            │
│                     │                 Replies           │ (backup) │            │
│                     ▼                    │              └──────────┘            │
│              ┌─────────────┐             │                                      │
│              │webhook_     │◄────────────┘                                      │
│              │receiver.py  │                                                    │
│              └─────────────┘                                                    │
│                     │                                                           │
│              ┌─────────────┐     ┌─────────────┐     ┌─────────────┐           │
│              │ processor   │     │ evolution   │     │    web.py   │           │
│              │ validation  │     │  _api.py    │     │  dashboard  │           │
│              └─────────────┘     └─────────────┘     └─────────────┘           │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Key Files

| File | Lines | Purpose |
|------|-------|---------|
| `python/evolution_api.py` | ~700 | Evolution API client - messaging, instance management, webhooks, message history |
| `python/webhook_receiver.py` | ~850 | FastAPI router - receives webhooks, processes messages, admin commands |
| `python/processor.py` | ~1100 | Message parsing, validation, cooldown checks |
| `python/message_sync.py` | ~400 | Offline message recovery, deduplication, fallback storage |
| `python/web.py` | ~1400 | FastAPI dashboard - records, analytics, approvals, fleet |
| `python/db.py` | ~200 | MySQL database helper (SQLAlchemy) |
| `python/google_sheets_uploader.py` | ~300 | Google Sheets integration |
| `python/weekly_summary.py` | ~850 | Summary generation (daily/weekly/monthly/vehicle) |
| `python/startup.py` | ~450 | Startup script - initializes all components, syncs missed messages |
| `config.json` | - | Configuration (group name, Evolution settings) |

## Message Format

Messages **MUST start with "FUEL UPDATE"** and include ALL required fields:

```
FUEL UPDATE
DEPARTMENT: LOGISTICS
DRIVER: JOHN  
CAR: KCA 542Q  
LITERS: 12.89  
AMOUNT: 2,000  
TYPE: DIESEL  
ODOMETER: 19,009
```

### Required Fields (ALL must be present)

| Field | Parser Key | Description |
|-------|------------|-------------|
| DEPARTMENT | `department` | Driver's department |
| DRIVER | `driver` | Driver name |
| CAR | `car` | Vehicle registration plate |
| LITERS | `liters` | Fuel amount in liters |
| AMOUNT | `amount` | Cost in KSH |
| TYPE | `type` | DIESEL, PETROL, SUPER, V-POWER |
| ODOMETER | `odometer` | Current odometer reading |

## Validation Layers

### 1. Required Fields Validation
All 7 fields must be present and have non-empty values.

### 2. Fleet Whitelist Validation
```python
ALLOWED_PLATES = { 'KCA542Q', 'KCB711C', ... }  # 80+ vehicles

def normalize_plate(plate: str) -> str:
    return re.sub(r'\s+', '', str(plate)).upper()
```

### 3. 12-Hour Car Cooldown (PRIMARY)
Same car cannot fuel within 12 hours without admin approval:
```python
CAR_COOLDOWN_HOURS = 12

def check_car_cooldown(car_plate: str, record: Dict) -> Tuple[bool, Optional[str]]:
    last_update = get_car_last_update(car_plate)
    if last_update:
        hours_since = (datetime.now() - last_time).total_seconds() / 3600
        if hours_since < CAR_COOLDOWN_HOURS:
            return False, approval_id  # Requires admin approval
    return True, None
```

### 4. Odometer Validation
New reading must be greater than previous for the same car.

## Admin Commands

| Command | Description |
|---------|-------------|
| `!status` | System health check (DB, Sheets, Evolution API) |
| `!summary [period]` | Generate summary (daily/weekly/monthly) |
| `!car PLATE [days]` | Vehicle-specific summary |
| `!pending` | View pending approval requests |
| `!approve ID` | Approve pending record |
| `!reject ID` | Reject pending record |
| `!add PLATE` | Add vehicle to ALLOWED_PLATES |
| `!remove PLATE` | Remove from ALLOWED_PLATES |
| `!list` | List all fleet vehicles |
| `!help` | Show all admin commands |
| `!how` | Public guide for drivers |

## Evolution API Integration

### Configuration (.env)
```bash
EVOLUTION_API_URL=http://localhost:8080
EVOLUTION_API_KEY=B6D711FCDE4D4FD5936544120E713976
EVOLUTION_INSTANCE_NAME=fuel-extractor
```

### Key Classes (evolution_api.py)

```python
class EvolutionAPI:
    """Evolution API client for WhatsApp integration"""
    
    async def send_text(self, to: str, message: str) -> dict
    async def send_to_group(self, group_jid: str, message: str) -> dict
    async def health_check(self) -> dict
    async def get_instance_status(self) -> dict
    async def create_instance(self, webhook_url: str) -> dict
    async def set_webhook(self, webhook_url: str, events: list) -> dict
    async def fetch_messages_async(self, chat_id: str, count: int = 50) -> List[Dict]
```

### Webhook Receiver (webhook_receiver.py)

```python
@router.post("/webhook/evolution")
async def evolution_webhook(request: Request):
    """Receive Evolution API webhook events"""
    # Handles: MESSAGES_UPSERT, CONNECTION_UPDATE, QRCODE_UPDATED
    # Includes message deduplication via MessageSyncManager
```

### Message Sync (message_sync.py)

```python
class MessageSyncManager:
    """Manages message deduplication and offline recovery"""
    def get_last_processed_time() -> Optional[datetime]
    def update_last_processed_time(timestamp: Optional[datetime] = None)
    def is_message_processed(message_id: str) -> bool
    def mark_message_processed(message_id: str, data: Dict, folder: str = 'raw')

async def fetch_missed_messages(
    evolution_api, group_jid: str, process_callback,
    max_messages: int = 50, max_offline_hours: int = 24
) -> Dict

def load_records_with_fallback() -> Tuple[List[Dict], str]  # Sheets → DB → Excel
async def save_record_with_fallback(record: Dict) -> Tuple[bool, str]  # DB → Sheets → JSON
```

## Offline Message Recovery

When the system starts up, it automatically:
1. Checks `data/last_processed.json` for the shutdown timestamp
2. Calculates time elapsed since last sync
3. Fetches up to 50 recent messages from the target group via Evolution API
4. Filters messages newer than the last sync timestamp
5. Processes any fuel reports that weren't processed (using deduplication)
6. Updates the last processed timestamp

### Shutdown Handler
On graceful shutdown (SIGINT/SIGTERM), the system saves the current timestamp to `data/last_processed.json`. This allows accurate recovery when the system restarts.

### Deduplication
Message IDs are tracked in three folders:
- `data/raw_messages/` - All received messages
- `data/processed/` - Successfully processed fuel reports
- `data/errors/` - Messages that failed processing

## Database Fallback Logic

### Reading Records (Priority: Sheets → DB → Excel)
```python
records, source = load_records_with_fallback()
# Tries: Google Sheets → MySQL → Excel files → Processed JSON
```

### Saving Records (Multi-destination)
```python
success, destinations = await save_record_with_fallback(record)
# Saves to: Database + Google Sheets + Local JSON (backup)
```

## Data Storage

### MySQL (Primary)
```python
# Table: fuel_records
Column('id', Integer, primary_key=True)
Column('created_at', DateTime)
Column('datetime', String(32))
Column('department', String(64))
Column('driver', String(128))
Column('car', String(32), index=True)
Column('liters', Float)
Column('amount', Float)
Column('type', String(32))
Column('odometer', Integer)
Column('sender', String(128))
Column('raw_message', Text)
```

### Google Sheets (Backup)
- Spreadsheet ID: `1gAq2TUBWPIKUAXRcHYeeq85ltktWgXUoe9QDDkRYSQo`
- Worksheet: `SYSTEM FUEL TRACKER`

## Web Dashboard Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Dashboard with metrics |
| `/records` | GET | Records table with search/filter |
| `/approvals` | GET | Pending approvals management |
| `/fleet` | GET | Fleet vehicle management |
| `/analytics` | GET | Charts and analytics |
| `/api/health` | GET | Health check (includes Evolution API status) |
| `/api/evolution/status` | GET | Detailed Evolution API status |
| `/api/evolution/send` | POST | Send WhatsApp message |
| `/api/evolution/initialize` | POST | Initialize Evolution instance |
| `/webhook/evolution` | POST | Evolution API webhook receiver |
| `/webhook/evolution/sync` | POST | Manual trigger for missed message sync |
| `/webhook/evolution/health` | GET | Webhook health with sync status |

## Configuration (config.json)

```json
{
    "whatsapp": {
        "phoneNumber": "254108661898",
        "groupName": "Fuel Reports",
        "groupJid": "120363304885288170@g.us"
    },
    "evolution": {
        "apiUrl": "http://localhost:8080",
        "apiKey": "B6D711FCDE4D4FD5936544120E713976",
        "instanceName": "fuel-extractor",
        "webhookUrl": "http://localhost:8000/webhook/evolution"
    },
    "upload": {
        "toGoogleSheets": true,
        "toDatabase": true
    }
}
```

## Environment Variables (.env)

```bash
# Database (MySQL)
DB_HOST=localhost
DB_NAME=logistics_department
DB_USER=root
DB_PASSWORD=your_password
DB_PORT=3306
DB_DRIVER=mysql+pymysql

# Google Sheets
GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id

# Evolution API
EVOLUTION_API_URL=http://localhost:8080
EVOLUTION_API_KEY=your_api_key
EVOLUTION_INSTANCE_NAME=fuel-extractor

# Web Server
WEB_HOST=0.0.0.0
WEB_PORT=8000
```

## Running the Application

```bash
# Start everything (recommended)
./start.sh

# Or manually
python -m python.startup

# Or just the web server
uvicorn python.web:app --host 0.0.0.0 --port 8000
```

## Key Functions

### webhook_receiver.py
- `evolution_webhook()` - Main webhook handler
- `process_fuel_report()` - Parse and validate fuel messages
- `process_admin_command()` - Handle admin commands
- `send_reply()` - Send WhatsApp response via Evolution API

### processor.py
- `FuelReportParser.parse(body)` - Extract all fields from message
- `check_car_cooldown(plate, record)` - 12-hour cooldown validation
- `normalize_plate(plate)` - Standardize plate format
- `ALLOWED_PLATES` - Set of valid fleet vehicles

### evolution_api.py
- `EvolutionAPI.send_text()` - Send message to number
- `EvolutionAPI.send_to_group()` - Send message to group
- `EvolutionAPI.health_check()` - Check API health
- `get_evolution_api()` - Get configured API instance

### db.py
- `Database.insert_fuel_record()` - Save record to MySQL
- `Database.get_all_records()` - Retrieve records with filters
- `Database.get_record_count()` - Count total records

## Coding Conventions

### Python
- Python 3.9+ with type hints
- `async/await` for Evolution API calls
- `pathlib.Path` for file operations
- `logging` with proper levels
- FastAPI for web and webhooks

### Logging Markers
- `[OK]` - Success
- `[ERROR]` - Error
- `[WEBHOOK]` - Webhook received
- `[FUEL]` - Fuel report processed
- `[ADMIN]` - Admin command
- `[PENDING]` - Pending approval created
- `[DENIED]` - Validation failed

## Dependencies (requirements.txt)

```
fastapi>=0.104.0
uvicorn>=0.24.0
httpx>=0.25.0          # Evolution API client
SQLAlchemy>=2.0.0
pymysql>=1.1.0         # MySQL driver
gspread>=6.0.0         # Google Sheets
pandas>=2.0.0
openpyxl>=3.1.2
python-dotenv>=1.0.0
```

## Docker Services (External)

The Evolution API runs in Docker (managed separately):
- **Evolution API**: `localhost:8080`
- **API Key**: Configured in `.env`

## Timeouts

```python
CAR_COOLDOWN_HOURS = 12           # Same car can't fuel within 12 hours
EDIT_APPROVAL_TIMEOUT_MINUTES = 10  # Edit detection window
```
