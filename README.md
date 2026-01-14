## Docker & zrock Setup (Windows)

1. **Clone this branch:**
  ```sh
  git clone -b ilogistics-system <repo-url>
  ```
2. **Build and run with Docker Compose (on Windows):**
  - Make sure `Dockerfile` and `docker-compose.yml` are present.
  - Build and start:
    ```sh
    docker-compose build
    docker-compose up -d
    ```
3. **Tunnel with zrock:**
  - Download and install zrock (https://zrock.io)
  - Start tunnel:
    ```sh
    zrock tunnel --local-port 8000 --remote-name ilogistics-system
    ```
  - Access from anywhere via zrock remote URL
  - See docker-compose.yml for quick reference.
# WhatsApp Fuel Extractor

Automatically extract fueling details from WhatsApp group messages and save them to Excel with real-time validation, admin approval workflows, and comprehensive reporting.

## Features

### Core Features
- **Real-time monitoring** - Captures messages as they arrive in the WhatsApp group
- **Instant confirmation** - Sends acknowledgment within seconds of receiving a report
- **Excel export** - Clean, formatted Excel files with all fuel records
- **Session persistence** - Survives restarts without re-scanning QR
- **Cross-platform** - Works on Linux and Windows with auto-discovery of conda

### Multi-Layer Validation
- **Required fields** - All 7 fields must be present and filled
- **Fleet whitelist** - Only approved vehicle plates are accepted (80+ vehicles)
- **Odometer check** - New reading must be greater than previous
- **12-hour cooldown** - Same car can't fuel again within 12 hours
- **Edit detection** - Tracks message edits and requires approval for key changes

### Fuel Efficiency Tracking
- **Automatic km/L calculation** - Calculates efficiency after each fuel-up using odometer readings
- **Efficiency alerts** - Notifies admins of unusual readings:
  - Low efficiency (<4 km/L): Possible fuel theft or vehicle issues
  - High efficiency (>20 km/L): Possible odometer discrepancy
- **Confirmation with stats** - Each confirmation shows distance traveled and efficiency rating
- **Historical tracking** - Stores efficiency history for trend analysis

### Progressive Web App (PWA)
- **Installable** - Install dashboard as mobile/desktop app
- **Offline support** - Works offline with cached pages
- **Push notifications** - Ready for future push notification support
- **App shortcuts** - Quick access to Dashboard, Records, Analytics, Approvals

### Admin Commands
| Command | Description |
|---------|-------------|
| `!status` | System health check |
| `!summary` | Weekly summary (default) |
| `!summary daily` | Today's summary |
| `!summary weekly` | Last 7 days |
| `!summary monthly` | Last 30 days (executive format) |
| `!car KXX123Y` | Vehicle summary (30 days) |
| `!car KXX123Y 60` | Vehicle summary (60 days) |
| `!pending` | View pending approvals |
| `!approve ID` | Approve pending record |
| `!reject ID` | Reject pending record |
| `!add KXX 123Y` | Add vehicle to fleet |
| `!remove KXX123Y` | Remove vehicle from fleet |
| `!list` | List all fleet vehicles |
| `!help` | Show all admin commands |
| `!how` | Guide for drivers (public) |

### Driver Commands (Public)
Anyone in the group can use these commands:

| Command | Description |
|---------|-------------|
| `!how` | How to send a fuel update |
| `!myrecords` | View your recent fuel records |
| `!myefficiency` | View your fuel efficiency stats |
| `!myvehicles` | View vehicles you've fueled |
| `!commands` | Show available commands |

**Natural Language Queries:**
...existing code...

|-------|-------------|
| `fuel today` | Today's fuel summary |
| `how much KCA542Q` | Vehicle fuel usage |
| `fuel this week` | Weekly fuel summary |

```
Driver sends message → Listener captures → Processor validates → Confirmation/Error/Approval
```

### Validation Flow
1. **Message received** in WhatsApp group starting with "FUEL UPDATE"
3. **Fleet validation** - Vehicle plate must be in approved list
4. **12-hour cooldown** - Check if car already fueled recently
5. **Odometer validation** - Reading must be > previous for same car
6. **Success** → Log to Excel + Send confirmation
7. **Approval needed** → Save for admin review + Send detailed notification

## Project Structure

```
WHATSAPP FUEL EXTRACTOR/
├── cli.py                   # Unified CLI (replaces all .sh/.bat files)
├── config.json              # Configuration file
├── .env                     # Environment variables (DB, Google Sheets)
├── package.json             # Node.js dependencies
├── requirements.txt         # Python dependencies
├── node/
│   └── listener.js          # WhatsApp listener + admin commands (~1500 lines)
├── python/
│   ├── processor.py         # Message parser + validation + Excel (~1100 lines)
│   ├── weekly_summary.py    # Summary generator (~850 lines)
│   ├── db.py                # Database helper (MySQL/PostgreSQL)
│   ├── google_sheets_uploader.py  # Google Sheets integration
│   ├── reset_external.py    # Reset DB and Sheets
│   └── env.py               # Environment loader
├── python/
│   ├── static/              # PWA assets
│   │   ├── manifest.json    # PWA manifest
│   │   ├── sw.js            # Service worker
│   │   ├── offline.html     # Offline fallback
│   │   └── icons/           # App icons
└── data/
    ├── raw_messages/        # Incoming messages (JSON)
    ├── processed/           # Successfully processed
    ├── output/              # Excel files
    ├── session/             # WhatsApp session
    ├── car_last_update.json # Car cooldown tracking
    └── efficiency_history.json # Fuel efficiency records
## Prerequisites

## Quick Start

### Using the Unified Launcher (Recommended)

The easiest way to get started is using the all-in-one launcher:

```bash
# Windows - just double-click or run:
run_fuel_extractor.bat

# Linux/Mac:
chmod +x run_fuel_extractor.sh
./run_fuel_extractor.sh
```

**First run?** The script will automatically detect missing dependencies and offer to install them.

**Features:**
- Auto-detects if setup is needed
- Interactive Python CLI menu
- Setup menu with install/update/clean options
- Pass commands directly: `run_fuel_extractor.bat listen`

**Command-line options:**
```bash
run_fuel_extractor --setup      # Open setup menu
run_fuel_extractor --clean      # Clean install
run_fuel_extractor listen       # Start listener directly
run_fuel_extractor web          # Start web dashboard
run_fuel_extractor --help       # Show help
```

### Manual Setup (Alternative)

#### 1. Install Dependencies

```bash
# Python (using conda)
conda create -n fuel-extractor python=3.11 -y
conda activate fuel-extractor
pip install -r requirements.txt

# Node.js (includes Chromium download)
npm install
```

#### 2. Configure

Edit `config.json`:

```json
{
  "whatsapp": {
    "phoneNumber": "",
    "groupName": "Your Fuel Reports Group"
  }
}
```

#### 3. Start the System

```bash
# Terminal 1 - Start WhatsApp listener
./run_fuel_extractor.sh listen

# Terminal 2 - Start message processor
./run_fuel_extractor.sh process
```

**Windows:** Use `run_fuel_extractor.bat` instead

Scan the QR code with WhatsApp (Settings → Linked Devices → Link a Device)

## CLI Commands

| Command | Description |
|---------|-------------|
| `run_fuel_extractor listen` | Start WhatsApp listener (Node.js) |
| `run_fuel_extractor process` | Start fuel data processor |
| `run_fuel_extractor once` | Process pending messages once and exit |
| `run_fuel_extractor web` | Start web dashboard |
| `run_fuel_extractor summary` | Generate weekly summary |
| `run_fuel_extractor summary --daily` | Generate daily summary |
| `run_fuel_extractor summary --monthly` | Generate monthly summary |
| `run_fuel_extractor summary --car KXX123Y` | Get vehicle-specific summary |
| `run_fuel_extractor reset` | Reset all data (with confirmation) |
| `run_fuel_extractor status` | Show system status |
| `run_fuel_extractor --help` | Show all commands |

## Uploads: Google Sheets + Database (Optional)

You can mirror each successful Excel entry to Google Sheets and your database.

### 1) Enable in config

Edit `config.json` and set:

```json
"upload": {
  "toGoogleSheets": true,
  "toDatabase": true,
  "google": { "sheetName": "FUEL RECORDS", "spreadsheetId": "" },
  "database": { "tableName": "fuel_records" }
}
```

### 2) Environment variables

Copy `.env.example` to `.env` and fill in:

```bash
DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/dbname
# Or mysql+pymysql / sqlite:///...

# Google service account credentials
GOOGLE_SERVICE_ACCOUNT_FILE=/absolute/path/to/service_account.json
# Or provide raw JSON via GOOGLE_SERVICE_ACCOUNT_JSON

# Spreadsheet selection (use one)
GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id
GOOGLE_SHEETS_SPREADSHEET_NAME=Fuel Records
```

The worksheet used inside the spreadsheet defaults to `FUEL RECORDS` (configurable via `upload.google.sheetName`).

### 3) Install extras

If not already installed:

```bash
pip install -r requirements.txt
```

Notes:
- For PostgreSQL, `psycopg2-binary` is included.
- For MySQL, install `pymysql` or `mysqlclient` and set `DATABASE_URL` accordingly.
- For SQLite, set `DATABASE_URL=sqlite:///fuel.db`.

## Message Format

Drivers send messages that **start with "FUEL UPDATE"** with ALL required fields:

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

### Required Fields

| Field | Description | Example |
|-------|-------------|---------|
| DEPARTMENT | Driver's department | LOGISTICS, SALES |
| DRIVER | Driver name | JOHN, MARY |
| CAR | Vehicle registration | KCA 542Q |
| LITERS | Fuel amount | 12.89 |
| AMOUNT | Cost in KSH | 2,000 |
| TYPE | Fuel type | DIESEL, PETROL |
| ODOMETER | Current reading | 19,009 |

## Approval System

### 12-Hour Car Cooldown
When the same car tries to fuel again within 12 hours, admin receives detailed comparison:

```
[!] *DUPLICATE FUEL REPORT - KCZ223P*
━━━━━━━━━━━━━━━━━━━━━━━

*TIME SINCE LAST FUELING:* 2.5 hours
Cooldown remaining: 9.5 hours

*DRIVER COMPARISON*
- Previous: JOHN
- Current: MARY
[!] _Driver changed!_

*ODOMETER / DISTANCE*
- Previous: 45,230 km
- Current: 45,380 km
- Distance traveled: *150 km*

*FUEL COMPARISON*
- Previous: 25.0 L (KSH 10,000)
- Current: 27.5 L (KSH 11,000)
- Efficiency since last: 6.0 km/L

━━━━━━━━━━━━━━━━━━━━━━━
Approval ID: *abc12345*

*!approve abc12345* - Log as new record
*!reject abc12345* - Discard
```

### Message Edit Detection
When a driver edits their message within 10 minutes:

```
[EDIT] *MESSAGE EDIT DETECTED*
━━━━━━━━━━━━━━━━━━━━━━━

*Time since original post:* 3 minutes
*Fields changed:* ODOMETER, AMOUNT

*DETAILED CHANGES*
───────────────────────
*ODOMETER*
   Before: 45,230 km
   After:  45,380 km
   Diff:   +150 km

*AMOUNT*
   Before: KSH 10,000
   After:  KSH 11,500
   Diff:   +KSH 1,500

━━━━━━━━━━━━━━━━━━━━━━━
Approval ID: *xyz789*

*!approve xyz789* - Accept edit
*!reject xyz789* - Keep original
```

## Summary Reports

### Daily Summary
Today's date, totals, fuel type breakdown, top vehicle, department summary.

### Weekly Summary
Week range, totals, daily averages, top performers, efficiency stats.

### Monthly Summary
Executive overview, consumption rates, fleet overview, fuel distribution %, department breakdown %, top 5 vehicles, highlights.

### Vehicle Summary
```
!car KCA542Q
```
Fuel records, total liters, total spent, distance, efficiency, fuel breakdown, odometer range, drivers, departments.

## Managing Fleet

### Via WhatsApp (Recommended)
```
!add KCA 542Q    → Adds to fleet
!remove KCA542Q  → Removes from fleet
!list            → Shows all vehicles
```

### Via Code
Edit `ALLOWED_PLATES` in `python/processor.py`

## Troubleshooting

| Issue | Solution |
|-------|----------|
| QR code not appearing | Delete `data/session/` and restart |
| Messages not captured | Ensure message starts with "FUEL UPDATE" |
| Vehicle not in fleet | Use `!add KXX123Y` command |
| Admin commands denied | Only group admins can use them |
| Conda not found | Install in `~/anaconda3` or `~/miniconda3` |

## Data Files

| File | Purpose |
|------|---------|
| `confirmations.json` | Pending confirmations |
| `validation_errors.json` | Pending error notifications |
| `pending_approvals.json` | Awaiting admin approval |
| `car_last_update.json` | 12h cooldown tracking |
| `weekly_summary.json` | Latest summary |
| `car_summary.json` | Latest vehicle summary |

## License

MIT License
