# GitHub Copilot Instructions for WhatsApp Fuel Extractor

## Project Overview

A **dual-language automation tool** that captures fueling reports from a WhatsApp group and exports them to Excel with real-time validation, admin approval workflows, and comprehensive reporting.

**Stack:**
- **Node.js 18+** - WhatsApp connectivity (whatsapp-web.js), admin commands, edit detection
- **Python 3.9+** - Message parsing, validation, Excel export, summary generation
- **JSON files** - Message queue, approvals, cooldown tracking

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                                                                 │
│  WhatsApp        Node.js          JSON Files        Python         WhatsApp    │
│  ┌───────┐      ┌────────┐       ┌──────────┐     ┌─────────┐     ┌────────┐   │
│  │ Group │─────►│listener│──────►│raw_msgs  │────►│processor│────►│Confirm │   │
│  │Message│      │  .js   │       │approvals │     │   .py   │     │ /Error │   │
│  └───────┘      └────────┘       │cooldown  │     └─────────┘     └────────┘   │
│       │              │           └──────────┘          │                        │
│       │         Admin Commands                    Validations:                  │
│       │         !status !summary                  - Required fields            │
│       │         !car !pending                     - Fleet whitelist            │
│       │         !approve !reject                  - 12h car cooldown           │
│       │         !add !remove !list                - Odometer check             │
│       │                                                                         │
│       └─────── Edit Detection ─────────────────────────────────────────────────┘
│                (within 10 min)
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Key Files

| File | Language | Lines | Purpose |
|------|----------|-------|---------|
| `node/listener.js` | JavaScript | ~1500 | WhatsApp connection, admin commands, edit detection, approval management |
| `python/processor.py` | Python | ~1100 | Message parsing, validation, cooldown checks, Excel export |
| `python/weekly_summary.py` | Python | ~850 | Summary generation (daily/weekly/monthly/vehicle) |
| `config.json` | JSON | - | Configuration (group name, output paths) |

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
            # Requires admin approval with detailed comparison
            return False, approval_id
    return True, None
```

### 4. Odometer Validation
New reading must be greater than previous for the same car.

## Admin Commands

| Command | Handler | Description |
|---------|---------|-------------|
| `!status` | `getSystemStatus()` | System health check |
| `!summary [period]` | `buildPythonCommand(weekly_summary.py)` | Generate summary (daily/weekly/monthly) |
| `!car PLATE [days]` | `buildPythonCommand(weekly_summary.py, '--car')` | Vehicle-specific summary |
| `!pending` | `getPendingApprovals()` | View pending approval requests |
| `!approve ID` | `processApproval(id, true)` | Approve pending record |
| `!reject ID` | `processApproval(id, false)` | Reject pending record |
| `!add PLATE` | `addVehicleToFleet()` | Add vehicle to ALLOWED_PLATES |
| `!remove PLATE` | `removeVehicleFromFleet()` | Remove from ALLOWED_PLATES |
| `!list` | `getFleetList()` | List all fleet vehicles |
| `!help` | Direct response | Show all admin commands |
| `!how` | `getFuelUpdateGuide()` | Public guide for drivers |

## Approval System

### Pending Approval Structure (`data/pending_approvals.json`)
```json
{
  "id": "abc12345",
  "type": "car_cooldown",  // or "edit"
  "timestamp": "2026-01-08T10:30:00",
  "record": { "driver": "JOHN", "car": "KCA542Q", ... },
  "original_record": { ... },
  "reason": "Same car fueled 2.5h ago",
  "status": "pending",  // pending, approved, rejected
  "notified": false
}
```

### Car Last Update Tracking (`data/car_last_update.json`)
```json
{
  "KCA542Q": {
    "timestamp": "2026-01-08T08:00:00",
    "driver": "JOHN",
    "liters": 25.0,
    "amount": 10000,
    "odometer": 45230,
    "type": "DIESEL",
    "department": "LOGISTICS"
  }
}
```

## Edit Detection (listener.js)

Key fields tracked for edits within 10 minutes:
- DRIVER, CAR, DEPARTMENT, ODOMETER, LITERS, AMOUNT, TYPE

```javascript
// Check if key fields changed
const keyFieldsChanged = [];
if (oldFields.driver !== newFields.driver) keyFieldsChanged.push('DRIVER');
if (oldFields.car !== newFields.car) keyFieldsChanged.push('CAR');
if (oldFields.department !== newFields.department) keyFieldsChanged.push('DEPARTMENT');
if (oldFields.odometer !== newFields.odometer) keyFieldsChanged.push('ODOMETER');
if (oldFields.liters !== newFields.liters) keyFieldsChanged.push('LITERS');
if (oldFields.amount !== newFields.amount) keyFieldsChanged.push('AMOUNT');
if (oldFields.type !== newFields.type) keyFieldsChanged.push('TYPE');
```

## Summary Types (weekly_summary.py)

| Type | Days | Format Function |
|------|------|-----------------|
| Daily | 1 | `format_daily_summary()` |
| Weekly | 7 | `format_weekly_summary()` |
| Monthly | 30 | `format_monthly_summary()` |
| Vehicle | configurable | `get_car_summary()` |

## Key Functions

### listener.js
- `isFuelReport(body)` - Check if message starts with "FUEL UPDATE"
- `parseFuelFields(body)` - Extract fields for edit comparison
- `handleAdminCommand(msg, body)` - Route admin commands
- `isGroupAdmin(msg)` - Check if sender is group admin
- `getPendingApprovals()` - Get pending approval list
- `processApproval(id, approve)` - Approve/reject with record creation
- `buildPythonCommand(script, ...args)` - Cross-platform Python execution
- `getCondaPaths()` - Auto-discover conda installation

### processor.py
- `FuelReportParser.parse(body)` - Extract all fields from message
- `check_car_cooldown(plate, record)` - 12-hour cooldown validation
- `save_pending_approval(type, record, original, reason)` - Queue for approval
- `update_car_last_update(plate, record)` - Update cooldown tracking
- `ExcelExporter.append_record(record)` - Add row to Excel

### weekly_summary.py
- `calculate_statistics(records, days)` - Compute stats with breakdowns
- `format_daily_summary(stats)` - Today's focused report
- `format_weekly_summary(stats)` - Week overview with top performers
- `format_monthly_summary(stats)` - Executive summary with percentages
- `get_car_summary(plate, days)` - Vehicle-specific analysis

## Data Flow

1. **Message received** → `message_create` event
2. **Fuel report check** → `isFuelReport()` 
3. **Save to JSON** → `data/raw_messages/msg_*.json`
4. **Processor validates** (every 10s):
   - Required fields
   - Fleet whitelist
   - **12-hour cooldown** (primary protection)
   - Odometer validation
5. **If valid** → Excel + confirmation
6. **If cooldown violation** → Pending approval + detailed notification
7. **If edit detected** (within 10 min) → Pending approval + comparison

## Coding Conventions

### JavaScript (Node.js)
- ES6+ syntax, async/await
- `path.join()` for all file paths
- Log with emojis: ✅ ❌ 📱 🔄 💾 📨 🚫 ⏳ ✏️
- Auto-discover conda with `getCondaPaths()`

### Python
- Python 3.9+ with type hints
- `pathlib.Path` for file operations
- `logging` with RotatingFileHandler
- Move processed files (audit trail)

## Configuration (config.json)

```json
{
  "whatsapp": {
    "phoneNumber": "",
    "groupName": "Fuel Reports"
  },
  "output": {
    "excelFolder": "./data/output",
    "excelFileName": "fuel_records.xlsx"
  },
  "schedule": {
    "processingIntervalSeconds": 10
  }
}
```

## Timeouts (processor.py)

```python
CAR_COOLDOWN_HOURS = 12           # Same car can't fuel within 12 hours
EDIT_APPROVAL_TIMEOUT_MINUTES = 10  # Edit detection window
```

## Dependencies

### Node.js
- `whatsapp-web.js` - WhatsApp Web client
- `qrcode-terminal` - QR code display
- `chokidar` - Config file watching

### Python
- `openpyxl` - Excel file handling
- `pandas` - Data manipulation
- `schedule` - Job scheduling
