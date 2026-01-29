"""
WhatsApp Fuel Extractor - Web Dashboard

A modern web interface for viewing fuel records, analytics, and managing approvals.

Features:
- Dashboard with key metrics
- Records table with search/filter
- Charts and analytics
- Pending approvals management
- Fleet management
- Real-time updates via Server-Sent Events (SSE)
"""

import asyncio
import fcntl
import io
import json
import os
import socket
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dateutil import parser as date_parser


# Thread-safe file operations
@contextmanager
def file_lock(filepath: Path, mode: str = 'r'):
    """Context manager for thread-safe file access with locking"""
    f = None
    try:
        f = open(filepath, mode)
        fcntl.flock(f.fileno(), fcntl.LOCK_EX if 'w' in mode else fcntl.LOCK_SH)
        yield f
    finally:
        if f:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            f.close()


def safe_json_load(filepath: Path, default: Any = None) -> Any:
    """Safely load JSON with error handling and locking"""
    if not filepath.exists():
        return default if default is not None else {}
    try:
        with file_lock(filepath, 'r') as f:
            content = f.read()
            if not content.strip():
                return default if default is not None else {}
            return json.loads(content)
    except json.JSONDecodeError as e:
        print(f"JSON decode error in {filepath}: {e}")
        # Try to recover by creating backup and returning default
        backup_path = filepath.with_suffix('.json.bak')
        try:
            import shutil
            shutil.copy2(filepath, backup_path)
            print(f"Created backup at {backup_path}")
        except:
            pass
        return default if default is not None else {}
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return default if default is not None else {}


def safe_json_save(filepath: Path, data: Any, indent: int = 2) -> bool:
    """Safely save JSON with atomic write and locking"""
    temp_path = filepath.with_suffix('.json.tmp')
    try:
        # Write to temp file first (atomic write pattern)
        with open(temp_path, 'w') as f:
            json.dump(data, f, indent=indent, default=str)
        # Rename temp to actual (atomic on most filesystems)
        temp_path.rename(filepath)
        return True
    except Exception as e:
        print(f"Error saving {filepath}: {e}")
        # Clean up temp file if exists
        if temp_path.exists():
            try:
                temp_path.unlink()
            except:
                pass
        return False

# Fix Windows console encoding for emoji output
if sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except:
        pass  # Already wrapped or not a TTY

from fastapi import FastAPI, Request, Form, HTTPException, Response, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import hashlib
import secrets
import re as regex_module

# Try imports for data access
try:
    import pandas as pd
    from openpyxl import load_workbook
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    from .env import load_env, get_env
    from .db import Database
    from .google_sheets_uploader import GoogleSheetsUploader
    from .evolution_api import EvolutionAPI, get_evolution_client
    from .webhook_receiver import router as webhook_router
except ImportError:
    from python.env import load_env, get_env
    from python.db import Database
    from python.google_sheets_uploader import GoogleSheetsUploader
    from python.evolution_api import EvolutionAPI, get_evolution_client
    from python.webhook_receiver import router as webhook_router

# Data source constants
DATA_SOURCE_EXCEL = 'excel'
DATA_SOURCE_DB = 'database'
DATA_SOURCE_SHEETS = 'sheets'

# Admin passwords
ADMIN_PASSWORD = "Nala2025"
AUDIT_LOG_PASSWORD = "NDERITU101"

# Session management
ADMIN_SESSIONS = {}  # token -> expiry timestamp
SESSION_DURATION_HOURS = 24

# Paths
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / 'data'
CONFIG_PATH = ROOT_DIR / 'config.json'
TEMPLATES_DIR = Path(__file__).parent / 'templates'
STATIC_DIR = Path(__file__).parent / 'static'
AUDIT_LOG_PATH = DATA_DIR / 'audit_log.json'

# Load environment
load_env(str(ROOT_DIR / '.env'))

# Create FastAPI app
app = FastAPI(
    title="Fuel Extractor Dashboard",
    description="Web dashboard for WhatsApp Fuel Extractor",
    version="1.0.0"
)


# Global exception handler
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions gracefully"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": str(exc.detail), "status_code": exc.status_code}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors gracefully"""
    return JSONResponse(
        status_code=422,
        content={"error": "Validation error", "details": str(exc)}
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler to prevent crashes"""
    error_id = datetime.now().strftime('%Y%m%d%H%M%S')
    print(f"[ERROR {error_id}] Unhandled exception: {exc}")
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "error_id": error_id,
            "message": "An unexpected error occurred. Please try again."
        }
    )


@app.on_event("startup")
async def startup_event():
    """Initialize resources on startup"""
    print("[START] Starting Fuel Extractor Dashboard...")
    # Ensure data directories exist
    for dir_path in [DATA_DIR, DATA_DIR / 'raw_messages', DATA_DIR / 'processed', DATA_DIR / 'errors', DATA_DIR / 'output']:
        dir_path.mkdir(parents=True, exist_ok=True)
    print("[OK] Data directories verified")
    
    # Initialize Evolution API connection
    try:
        api = get_evolution_client()
        if api:
            health = api.health_check()  # Sync method
            if health:
                print(f"[OK] Evolution API connected: {api.base_url}")
            else:
                print("[WARN] Evolution API not responding")
        else:
            print("[WARN] Evolution API not configured")
    except Exception as e:
        print(f"[WARN] Evolution API check failed: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    print("[STOP] Shutting down Fuel Extractor Dashboard...")

# Mount static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Mount Evolution API webhook router
app.include_router(webhook_router)

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def load_config() -> dict:
    """Load configuration with error handling"""
    return safe_json_load(CONFIG_PATH, {})


def get_excel_path() -> Path:
    """Get path to Excel file"""
    config = load_config()
    folder = config.get('output', {}).get('excelFolder', './data/output')
    filename = config.get('output', {}).get('excelFileName', 'fuel_records.xlsx')
    return ROOT_DIR / folder / filename


def parse_datetime_robust(dt_str) -> Optional[datetime]:
    """Parse datetime string robustly, handling various formats"""
    if dt_str is None:
        return None
    
    # Check for pandas NaT/NA
    if PANDAS_AVAILABLE:
        try:
            if pd.isna(dt_str):
                return None
        except:
            pass
    
    try:
        # Handle pandas Timestamp
        if hasattr(dt_str, 'to_pydatetime'):
            dt = dt_str.to_pydatetime()
            # Remove timezone info to avoid offset issues
            if dt.tzinfo:
                return dt.replace(tzinfo=None)
            return dt
        
        # Handle datetime objects
        if isinstance(dt_str, datetime):
            if dt_str.tzinfo:
                return dt_str.replace(tzinfo=None)
            return dt_str
        
        # Convert to string for parsing
        dt_string = str(dt_str).strip()
        
        # Try parsing with dateutil (most flexible)
        try:
            parsed = date_parser.parse(dt_string, fuzzy=True)
            # Remove timezone to avoid offset issues
            if parsed.tzinfo:
                return parsed.replace(tzinfo=None)
            return parsed
        except:
            pass
        
        # Fallback: try common formats manually
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M:%S.%f',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%S.%f',
            '%Y-%m-%dT%H:%M:%SZ',
            '%Y-%m-%dT%H:%M:%S.%fZ',
            '%d/%m/%Y %H:%M:%S',
            '%d-%m-%Y %H:%M:%S',
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(dt_string.replace('Z', ''), fmt.replace('Z', ''))
            except:
                continue
        
        return None
    except Exception as e:
        return None


def load_records(days: int = 30) -> List[Dict]:
    """Load fuel records from Excel with comprehensive error handling"""
    records = []
    excel_path = get_excel_path()
    
    if not excel_path.exists():
        return records
    
    if not PANDAS_AVAILABLE:
        print("Warning: pandas not available, cannot load Excel records")
        return records
    
    try:
        # Check file size to prevent memory issues
        file_size = excel_path.stat().st_size
        if file_size > 50 * 1024 * 1024:  # 50MB limit
            print(f"Warning: Excel file is large ({file_size / 1024 / 1024:.1f}MB), loading may be slow")
        
        df = pd.read_excel(excel_path)
        
        # Convert to list of dicts
        for _, row in df.iterrows():
            # Parse datetime robustly
            dt_val = row.get('DATETIME', '')
            parsed_dt = parse_datetime_robust(dt_val)
            dt_str = parsed_dt.isoformat() if parsed_dt else str(dt_val)
            
            record = {
                'datetime': dt_str,
                'datetime_obj': parsed_dt,
                'department': str(row.get('DEPARTMENT', '')),
                'driver': str(row.get('DRIVER', '')),
                'car': str(row.get('CAR', '')),
                'liters': float(row.get('LITERS', 0)) if pd.notna(row.get('LITERS')) else 0,
                'amount': float(row.get('AMOUNT', 0)) if pd.notna(row.get('AMOUNT')) else 0,
                'type': str(row.get('TYPE', '')),
                'odometer': int(row.get('ODOMETER', 0)) if pd.notna(row.get('ODOMETER')) else 0,
            }
            records.append(record)
        
        # Sort by datetime descending
        records.sort(key=lambda x: x['datetime_obj'] or datetime.min, reverse=True)
        
    except Exception as e:
        print(f"Error loading records: {e}")
    
    return records


def load_records_from_db(start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[Dict]:
    """Load fuel records from the database with optional date filtering."""
    records = []
    
    try:
        db = Database()
        if not db.engine:
            print("Database not configured")
            return records
        
        raw_records = db.get_all_records(start_date=start_date, end_date=end_date)
        
        for row in raw_records:
            # Parse datetime
            dt_val = row.get('datetime') or row.get('created_at')
            parsed_dt = parse_datetime_robust(dt_val)
            dt_str = parsed_dt.isoformat() if parsed_dt else str(dt_val) if dt_val else ''
            
            record = {
                'datetime': dt_str,
                'datetime_obj': parsed_dt,
                'department': str(row.get('department', '') or ''),
                'driver': str(row.get('driver', '') or ''),
                'car': str(row.get('car', '') or ''),
                'liters': float(row.get('liters', 0) or 0),
                'amount': float(row.get('amount', 0) or 0),
                'type': str(row.get('type', '') or ''),
                'odometer': int(row.get('odometer', 0) or 0),
            }
            records.append(record)
        
        # Sort by datetime descending
        records.sort(key=lambda x: x['datetime_obj'] or datetime.min, reverse=True)
        
    except Exception as e:
        print(f"Error loading records from database: {e}")
    
    return records


def load_records_from_sheets(start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> List[Dict]:
    """Load fuel records from Google Sheets with optional date filtering."""
    records = []
    
    try:
        config = load_config()
        # Check both config paths for backwards compatibility
        spreadsheet_id = (
            config.get('upload', {}).get('google', {}).get('spreadsheetId') or
            config.get('google_sheets', {}).get('spreadsheetId') or
            get_env('GOOGLE_SHEETS_SPREADSHEET_ID')
        )
        worksheet_name = (
            config.get('upload', {}).get('google', {}).get('sheetName') or
            config.get('google_sheets', {}).get('worksheetName') or
            'SYSTEM FUEL TRACKER'
        )
        
        if not spreadsheet_id:
            print("Google Sheets not configured (no spreadsheet ID)")
            return records
        
        print(f"[SHEETS] Loading from worksheet: {worksheet_name}")
        uploader = GoogleSheetsUploader(spreadsheet_id=spreadsheet_id, worksheet_name=worksheet_name)
        raw_records = uploader.get_all_records()
        
        for row in raw_records:
            # Parse datetime
            dt_val = row.get('datetime', '')
            parsed_dt = parse_datetime_robust(dt_val)
            
            # Apply date filter
            if parsed_dt:
                if start_date and parsed_dt < start_date:
                    continue
                if end_date and parsed_dt > end_date:
                    continue
            
            dt_str = parsed_dt.isoformat() if parsed_dt else str(dt_val)
            
            # Parse numeric fields
            try:
                liters = float(str(row.get('liters', '0')).replace(',', '')) if row.get('liters') else 0
            except:
                liters = 0
            
            try:
                amount = float(str(row.get('amount', '0')).replace(',', '')) if row.get('amount') else 0
            except:
                amount = 0
            
            try:
                odometer = int(float(str(row.get('odometer', '0')).replace(',', ''))) if row.get('odometer') else 0
            except:
                odometer = 0
            
            record = {
                'datetime': dt_str,
                'datetime_obj': parsed_dt,
                'department': str(row.get('department', '')),
                'driver': str(row.get('driver', '')),
                'car': str(row.get('car', '')),
                'liters': liters,
                'amount': amount,
                'type': str(row.get('type', '')),
                'odometer': odometer,
            }
            records.append(record)
        
        # Sort by datetime descending
        records.sort(key=lambda x: x['datetime_obj'] or datetime.min, reverse=True)
        
    except Exception as e:
        print(f"Error loading records from Google Sheets: {e}")
    
    return records


def load_records_by_source(source: str = DATA_SOURCE_EXCEL, start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> Tuple[List[Dict], str]:
    """Load records from the specified data source with date filtering.
    
    Returns: (records, actual_source_used)
    """
    if source == DATA_SOURCE_DB:
        records = load_records_from_db(start_date=start_date, end_date=end_date)
        if records:
            return records, DATA_SOURCE_DB
    elif source == DATA_SOURCE_SHEETS:
        records = load_records_from_sheets(start_date=start_date, end_date=end_date)
        if records:
            return records, DATA_SOURCE_SHEETS
    else:
        # Default to Excel
        records = load_records()
        
        # Apply date filter for Excel
        if start_date or end_date:
            filtered = []
            for r in records:
                dt = r.get('datetime_obj')
                if dt:
                    if start_date and dt < start_date:
                        continue
                    if end_date and dt > end_date:
                        continue
                filtered.append(r)
            if filtered:
                return filtered, DATA_SOURCE_EXCEL
        elif records:
            return records, DATA_SOURCE_EXCEL
    
    # Return empty if source had no data
    return [], source


def load_records_with_fallback(start_date: Optional[datetime] = None, end_date: Optional[datetime] = None) -> Tuple[List[Dict], str]:
    """Load records with automatic fallback: Database -> Sheets -> Excel.
    
    Priority order:
    1. Database (primary - most reliable)
    2. Google Sheets (backup)
    3. Excel file (last resort)
    
    Returns: (records, source_used)
    """
    # Try Database first (primary source)
    try:
        records = load_records_from_db(start_date=start_date, end_date=end_date)
        if records:
            print(f"[DATA] Loaded {len(records)} records from Database")
            return records, DATA_SOURCE_DB
    except Exception as e:
        print(f"[WARN] Database failed: {e}")
    
    # Try Google Sheets second
    try:
        records = load_records_from_sheets(start_date=start_date, end_date=end_date)
        if records:
            print(f"[DATA] Loaded {len(records)} records from Google Sheets")
            return records, DATA_SOURCE_SHEETS
    except Exception as e:
        print(f"[WARN] Google Sheets failed: {e}")
    
    # Fall back to Excel
    try:
        records = load_records()
        
        # Apply date filter for Excel
        if start_date or end_date:
            filtered = []
            for r in records:
                dt = r.get('datetime_obj')
                if dt:
                    if start_date and dt < start_date:
                        continue
                    if end_date and dt > end_date:
                        continue
                filtered.append(r)
            records = filtered
        
        if records:
            print(f"[DATA] Loaded {len(records)} records from Excel")
            return records, DATA_SOURCE_EXCEL
    except Exception as e:
        print(f"[WARN] Excel failed: {e}")
    
    # No data found
    print("[WARN] No data available from any source")
    return [], 'none'


def get_available_data_sources() -> Dict[str, bool]:
    """Check which data sources are available and configured."""
    sources = {
        DATA_SOURCE_EXCEL: get_excel_path().exists(),
        DATA_SOURCE_DB: False,
        DATA_SOURCE_SHEETS: False,
    }
    
    # Check database
    try:
        db = Database()
        if db.engine:
            # Try a simple query
            count = db.get_record_count()
            sources[DATA_SOURCE_DB] = True
    except:
        pass
    
    # Check Google Sheets
    try:
        config = load_config()
        spreadsheet_id = config.get('google_sheets', {}).get('spreadsheetId') or get_env('GOOGLE_SHEETS_SPREADSHEET_ID')
        if spreadsheet_id:
            sources[DATA_SOURCE_SHEETS] = True
    except:
        pass
    
    return sources


def load_pending_approvals() -> List[Dict]:
    """Load pending approvals with safe JSON loading"""
    path = DATA_DIR / 'pending_approvals.json'
    approvals = safe_json_load(path, [])
    if not isinstance(approvals, list):
        return []
    return [a for a in approvals if isinstance(a, dict) and a.get('status') == 'pending']


def load_fleet() -> List[str]:
    """Load fleet vehicles from processor.py"""
    # Read ALLOWED_PLATES from processor.py
    processor_path = Path(__file__).parent / 'processor.py'
    plates = []
    
    if processor_path.exists():
        try:
            with open(processor_path, 'r') as f:
                content = f.read()
                # Find ALLOWED_PLATES set (use DOTALL to match across multiple lines)
                import re
                match = re.search(r"ALLOWED_PLATES\s*=\s*\{([^}]+)\}", content, re.DOTALL)
                if match:
                    plates_str = match.group(1)
                    # Extract quoted strings
                    plates = re.findall(r"'([^']+)'", plates_str)
        except:
            pass
    
    return sorted(plates)


def get_stats(records: List[Dict]) -> Dict:
    """Calculate dashboard statistics"""
    today = datetime.now().date()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)
    
    stats = {
        'total_records': len(records),
        'total_liters': sum(r['liters'] for r in records),
        'total_amount': sum(r['amount'] for r in records),
        'unique_vehicles': len(set(r['car'] for r in records if r['car'])),
        'unique_drivers': len(set(r['driver'] for r in records if r['driver'])),
        'today_records': 0,
        'today_liters': 0,
        'today_amount': 0,
        'week_records': 0,
        'week_liters': 0,
        'week_amount': 0,
        'pending_approvals': len(load_pending_approvals()),
    }
    
    for r in records:
        try:
            # Use pre-parsed datetime object
            dt = r.get('datetime_obj')
            if dt is None:
                continue
                
            record_date = dt.date()
            
            if record_date == today:
                stats['today_records'] += 1
                stats['today_liters'] += r['liters']
                stats['today_amount'] += r['amount']
            
            if record_date >= week_ago:
                stats['week_records'] += 1
                stats['week_liters'] += r['liters']
                stats['week_amount'] += r['amount']
        except Exception as e:
            pass
    
    return stats


def get_chart_data(records: List[Dict], days: int = 7) -> Dict:
    """Get data for charts"""
    # Daily fuel consumption
    daily_data = {}
    by_department = {}
    by_fuel_type = {}
    top_vehicles = {}
    
    cutoff = datetime.now() - timedelta(days=days)
    
    for r in records:
        try:
            # Use pre-parsed datetime object
            dt = r.get('datetime_obj')
            if dt is None:
                continue
            
            # Make cutoff comparison timezone-naive
            if dt.tzinfo:
                dt = dt.replace(tzinfo=None)
            
            if dt < cutoff:
                continue
            
            date_str = dt.strftime('%Y-%m-%d')
            
            # Daily
            if date_str not in daily_data:
                daily_data[date_str] = {'liters': 0, 'amount': 0, 'count': 0}
            daily_data[date_str]['liters'] += r['liters']
            daily_data[date_str]['amount'] += r['amount']
            daily_data[date_str]['count'] += 1
            
            # By department
            dept = r['department'] or 'Unknown'
            if dept not in by_department:
                by_department[dept] = 0
            by_department[dept] += r['amount']
            
            # By fuel type
            ftype = r['type'] or 'Unknown'
            if ftype not in by_fuel_type:
                by_fuel_type[ftype] = 0
            by_fuel_type[ftype] += r['liters']
            
            # Top vehicles
            car = r['car']
            if car:
                if car not in top_vehicles:
                    top_vehicles[car] = 0
                top_vehicles[car] += r['amount']
        except Exception as e:
            pass
    
    # Sort daily data by date
    sorted_dates = sorted(daily_data.keys())
    
    return {
        'daily': {
            'labels': sorted_dates,
            'liters': [daily_data[d]['liters'] for d in sorted_dates],
            'amount': [daily_data[d]['amount'] for d in sorted_dates],
            'count': [daily_data[d]['count'] for d in sorted_dates],
        },
        'by_department': {
            'labels': list(by_department.keys()),
            'values': list(by_department.values()),
        },
        'by_fuel_type': {
            'labels': list(by_fuel_type.keys()),
            'values': list(by_fuel_type.values()),
        },
        'top_vehicles': dict(sorted(top_vehicles.items(), key=lambda x: x[1], reverse=True)[:10]),
    }


# Routes
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page"""
    records, source = load_records_with_fallback()
    stats = get_stats(records)
    chart_data = get_chart_data(records, days=7)
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "chart_data": json.dumps(chart_data),
        "recent_records": records[:10],
        "data_source": source,
    })


@app.get("/records", response_class=HTMLResponse)
async def records_page(request: Request, search: str = "", page: int = 1):
    """Records listing page"""
    records, source = load_records_with_fallback()
    
    # Filter by search
    if search:
        search_lower = search.lower()
        records = [r for r in records if 
                   search_lower in r['car'].lower() or
                   search_lower in r['driver'].lower() or
                   search_lower in r['department'].lower()]
    
    # Pagination
    per_page = 25
    total_pages = (len(records) + per_page - 1) // per_page
    start = (page - 1) * per_page
    end = start + per_page
    
    return templates.TemplateResponse("records.html", {
        "request": request,
        "records": records[start:end],
        "total": len(records),
        "page": page,
        "total_pages": total_pages,
        "search": search,
        "data_source": source,
    })


@app.get("/approvals", response_class=HTMLResponse)
async def approvals_page(request: Request):
    """Pending approvals page"""
    approvals = load_pending_approvals()
    
    return templates.TemplateResponse("approvals.html", {
        "request": request,
        "approvals": approvals,
    })


@app.post("/approvals/{approval_id}/approve")
async def approve_record(approval_id: str):
    """Approve a pending record - save to DB/Sheets and send WhatsApp notification"""
    path = DATA_DIR / 'pending_approvals.json'
    
    approvals = safe_json_load(path, [])
    if not approvals:
        raise HTTPException(status_code=404, detail="No approvals found")
    
    for approval in approvals:
        if approval.get('id') == approval_id:
            if approval.get('status') != 'pending':
                raise HTTPException(status_code=400, detail=f"Approval already {approval.get('status')}")
            
            approval['status'] = 'approved'
            approval['approved_at'] = datetime.now().isoformat()
            approval['approved_via'] = 'web'
            
            if not safe_json_save(path, approvals):
                raise HTTPException(status_code=500, detail="Failed to save approval")
            
            # Get the record and save it directly
            record = approval.get('record', {})
            if record and approval.get('type') in ['car_cooldown', 'driver_change', 'edit']:
                # Load config for storage settings
                try:
                    with open(CONFIG_PATH, 'r') as f:
                        config = json.load(f)
                except:
                    config = {}
                
                upload_config = config.get('upload', {})
                saved_destinations = []
                
                # Save to MySQL database
                if upload_config.get('toDatabase', True):
                    try:
                        table_name = upload_config.get('database', {}).get('tableName', 'fuel_records')
                        db = Database(table_name=table_name)
                        if db.insert_fuel_record(record):
                            saved_destinations.append("Database")
                            print(f"[DB] Inserted approved record for {record.get('car', 'N/A')}")
                    except Exception as e:
                        print(f"[DB] Error saving to database: {e}")
                
                # Save to Google Sheets
                if upload_config.get('toGoogleSheets', True):
                    try:
                        sheet_config = upload_config.get('google', {})
                        spreadsheet_id = sheet_config.get('spreadsheetId') or get_env('GOOGLE_SHEETS_SPREADSHEET_ID')
                        sheet_name = sheet_config.get('sheetName', 'FUEL RECORDS')
                        
                        if spreadsheet_id:
                            uploader = GoogleSheetsUploader(
                                spreadsheet_id=spreadsheet_id,
                                worksheet_name=sheet_name
                            )
                            columns = ['DATETIME', 'DEPARTMENT', 'DRIVER', 'CAR', 'LITERS', 'AMOUNT', 'TYPE', 'ODOMETER', 'SENDER', 'RAW_MESSAGE']
                            uploader.ensure_headers(columns)
                            uploader.append_record(record, columns)
                            saved_destinations.append("Google Sheets")
                            print(f"[SHEETS] Uploaded approved record for {record.get('car', 'N/A')}")
                    except Exception as e:
                        print(f"[SHEETS] Error saving to Google Sheets: {e}")
                
                # Update car_last_update for cooldown tracking
                try:
                    car_last_update_path = DATA_DIR / 'car_last_update.json'
                    updates = safe_json_load(car_last_update_path, {})
                    car_plate = record.get('car', '')
                    if car_plate:
                        updates[car_plate] = {
                            'timestamp': datetime.now().isoformat(),
                            'driver': record.get('driver', ''),
                            'liters': record.get('liters', ''),
                            'amount': record.get('amount', ''),
                            'odometer': record.get('odometer', ''),
                            'department': record.get('department', '')
                        }
                        safe_json_save(car_last_update_path, updates)
                        print(f"[COOLDOWN] Updated car_last_update for {car_plate}")
                except Exception as e:
                    print(f"[COOLDOWN] Error updating car_last_update: {e}")
                
                # Send WhatsApp confirmation via Evolution API
                try:
                    # Build confirmation message
                    confirm_msg = "[APPROVED] *FUEL REPORT APPROVED*\n\n"
                    
                    if record.get('department'):
                        confirm_msg += f"Department: {record['department']}\n"
                    if record.get('driver'):
                        confirm_msg += f"Driver: {record['driver']}\n"
                    if record.get('car'):
                        confirm_msg += f"Vehicle: {record['car']}\n"
                    if record.get('liters'):
                        try:
                            liters = float(str(record['liters']).replace(',', ''))
                            confirm_msg += f"Fuel: {liters:.2f} L"
                        except:
                            confirm_msg += f"Fuel: {record['liters']} L"
                        if record.get('type'):
                            confirm_msg += f" ({record['type']})"
                        confirm_msg += "\n"
                    if record.get('amount'):
                        try:
                            amount = float(str(record['amount']).replace(',', ''))
                            confirm_msg += f"Amount: KSH {amount:,.0f}\n"
                        except:
                            confirm_msg += f"Amount: KSH {record['amount']}\n"
                    if record.get('odometer'):
                        try:
                            odo = int(float(str(record['odometer']).replace(',', '')))
                            confirm_msg += f"Odometer: {odo:,} km\n"
                        except:
                            confirm_msg += f"Odometer: {record['odometer']} km\n"
                    
                    confirm_msg += f"\nSaved to: {', '.join(saved_destinations) if saved_destinations else 'Local'}\n"
                    confirm_msg += f"\n_Approved via Web Dashboard | {datetime.now().strftime('%Y-%m-%d %H:%M')}_"
                    
                    # Get group JID from config
                    group_jid = config.get('whatsapp', {}).get('groupJid', '')
                    
                    if group_jid:
                        api = EvolutionAPI()
                        result = await api.send_text_message_async(group_jid, confirm_msg)
                        if 'error' not in result:
                            print(f"[WHATSAPP] Sent approval confirmation for {record.get('car', 'N/A')}")
                        else:
                            print(f"[WHATSAPP] Failed to send confirmation: {result}")
                    else:
                        print(f"[WHATSAPP] No group JID configured - confirmation not sent")
                except Exception as e:
                    print(f"[WHATSAPP] Error sending confirmation: {e}")
                
                print(f"[APPROVED] Record saved - Destinations: {saved_destinations}")
            
            return JSONResponse({
                "status": "approved", 
                "id": approval_id,
                "saved_to": saved_destinations if 'saved_destinations' in locals() else []
            })
    
    raise HTTPException(status_code=404, detail="Approval not found")


@app.post("/approvals/{approval_id}/reject")
async def reject_record(approval_id: str):
    """Reject a pending record and send WhatsApp notification"""
    path = DATA_DIR / 'pending_approvals.json'
    
    approvals = safe_json_load(path, [])
    if not approvals:
        raise HTTPException(status_code=404, detail="No approvals found")
    
    for approval in approvals:
        if approval.get('id') == approval_id:
            if approval.get('status') != 'pending':
                raise HTTPException(status_code=400, detail=f"Approval already {approval.get('status')}")
            
            approval['status'] = 'rejected'
            approval['rejected_at'] = datetime.now().isoformat()
            approval['rejected_via'] = 'web'
            
            if not safe_json_save(path, approvals):
                raise HTTPException(status_code=500, detail="Failed to save rejection")
            
            # Send rejection notification directly via Evolution API
            record = approval.get('record', {})
            if record:
                reject_msg = f"[REJECTED] *FUEL REPORT REJECTED*\n\n"
                reject_msg += f"Vehicle: {record.get('car', 'N/A')}\n"
                reject_msg += f"Driver: {record.get('driver', 'N/A')}\n"
                reject_msg += f"Reason: {approval.get('reason', 'Admin rejected via web')}\n"
                reject_msg += f"\n_Rejected via Web Dashboard | {datetime.now().strftime('%Y-%m-%d %H:%M')}_"
                
                # Send via Evolution API
                try:
                    # Load config for group JID
                    try:
                        with open(CONFIG_PATH, 'r') as f:
                            config = json.load(f)
                    except:
                        config = {}
                    
                    group_jid = config.get('whatsapp', {}).get('groupJid', '')
                    
                    if group_jid:
                        api = EvolutionAPI()
                        result = await api.send_text_message_async(group_jid, reject_msg)
                        if 'error' not in result:
                            print(f"[WHATSAPP] Sent rejection notification for {record.get('car', 'N/A')}")
                        else:
                            print(f"[WHATSAPP] Failed to send rejection: {result}")
                    else:
                        print(f"[WHATSAPP] No group JID configured - rejection notification not sent")
                except Exception as e:
                    print(f"[WHATSAPP] Error sending rejection notification: {e}")
            
            return JSONResponse({"status": "rejected", "id": approval_id})
    
    raise HTTPException(status_code=404, detail="Approval not found")


@app.get("/fleet", response_class=HTMLResponse)
async def fleet_page(request: Request):
    """Fleet management page"""
    fleet = load_fleet()
    
    return templates.TemplateResponse("fleet.html", {
        "request": request,
        "fleet": fleet,
        "total": len(fleet),
    })


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(
    request: Request, 
    days: int = 30,
    source: str = DATA_SOURCE_EXCEL,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Analytics page with data source selection and date range filtering"""
    
    # Parse date strings if provided
    start_dt = None
    end_dt = None
    
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        except:
            pass
    
    if end_date:
        try:
            # Set to end of day
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        except:
            pass
    
    # If no custom dates, use days parameter
    if not start_dt and not end_dt and days:
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=days)
    
    # Load records with automatic fallback (Sheets -> DB -> Excel)
    records, actual_source = load_records_with_fallback(start_date=start_dt, end_date=end_dt)
    
    # Get chart data (don't apply days filter again since we already filtered)
    chart_data = get_chart_data(records, days=365)  # Use large value since records are pre-filtered
    
    # Get available data sources
    available_sources = get_available_data_sources()
    
    # Calculate summary stats for the selected period
    total_liters = sum(r['liters'] for r in records)
    total_amount = sum(r['amount'] for r in records)
    unique_vehicles = len(set(r['car'] for r in records if r['car']))
    unique_drivers = len(set(r['driver'] for r in records if r['driver']))
    
    summary_stats = {
        'total_records': len(records),
        'total_liters': total_liters,
        'total_amount': total_amount,
        'unique_vehicles': unique_vehicles,
        'unique_drivers': unique_drivers,
        'avg_per_fueling': total_liters / len(records) if records else 0,
        'avg_cost_per_liter': total_amount / total_liters if total_liters > 0 else 0,
    }
    
    return templates.TemplateResponse("analytics.html", {
        "request": request,
        "chart_data": json.dumps(chart_data),
        "days": days,
        "source": actual_source,  # Show which source was actually used
        "available_sources": available_sources,
        "start_date": start_date or (start_dt.strftime('%Y-%m-%d') if start_dt else ''),
        "end_date": end_date or (end_dt.strftime('%Y-%m-%d') if end_dt else ''),
        "summary_stats": summary_stats,
        "DATA_SOURCE_EXCEL": DATA_SOURCE_EXCEL,
        "DATA_SOURCE_DB": DATA_SOURCE_DB,
        "DATA_SOURCE_SHEETS": DATA_SOURCE_SHEETS,
    })


@app.get("/api/analytics")
async def api_analytics(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: int = 30
):
    """API endpoint for analytics data with automatic fallback (Sheets -> DB -> Excel)"""
    
    # Parse date strings
    start_dt = None
    end_dt = None
    
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        except:
            pass
    
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
        except:
            pass
    
    if not start_dt and not end_dt and days:
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=days)
    
    # Use fallback: Sheets -> DB -> Excel
    records, actual_source = load_records_with_fallback(start_date=start_dt, end_date=end_dt)
    chart_data = get_chart_data(records, days=365)
    
    total_liters = sum(r['liters'] for r in records)
    total_amount = sum(r['amount'] for r in records)
    
    return {
        'chart_data': chart_data,
        'summary': {
            'total_records': len(records),
            'total_liters': total_liters,
            'total_amount': total_amount,
            'unique_vehicles': len(set(r['car'] for r in records if r['car'])),
            'unique_drivers': len(set(r['driver'] for r in records if r['driver'])),
        },
        'date_range': {
            'start': start_dt.isoformat() if start_dt else None,
            'end': end_dt.isoformat() if end_dt else None,
        },
        'source': actual_source,
    }


@app.get("/api/stats")
async def api_stats():
    """API endpoint for stats"""
    records = load_records()
    return get_stats(records)


@app.get("/api/health")
async def health_check():
    """Health check endpoint for monitoring"""
    health = {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'checks': {}
    }
    
    # Check Excel file
    excel_path = get_excel_path()
    health['checks']['excel'] = {
        'exists': excel_path.exists(),
        'path': str(excel_path)
    }
    if excel_path.exists():
        health['checks']['excel']['size_mb'] = round(excel_path.stat().st_size / 1024 / 1024, 2)
        health['checks']['excel']['modified'] = datetime.fromtimestamp(excel_path.stat().st_mtime).isoformat()
    
    # Check data directories
    for name in ['raw_messages', 'processed', 'errors']:
        dir_path = DATA_DIR / name
        file_count = len(list(dir_path.glob('*.json'))) if dir_path.exists() else 0
        health['checks'][name] = {
            'exists': dir_path.exists(),
            'file_count': file_count
        }
    
    # Check pending approvals
    approvals = load_pending_approvals()
    health['checks']['pending_approvals'] = len(approvals)
    
    # Check Evolution API
    try:
        api = get_evolution_client()
        if api:
            evo_health = api.health_check()  # Sync method, don't await
            health['checks']['evolution_api'] = {
                'connected': evo_health is not None,
                'url': api.base_url
            }
            if evo_health:
                health['checks']['evolution_api']['version'] = evo_health.get('version', 'unknown')
        else:
            health['checks']['evolution_api'] = {'connected': False, 'error': 'Not configured'}
    except Exception as e:
        health['checks']['evolution_api'] = {'connected': False, 'error': str(e)}
    
    # Overall status
    if not excel_path.exists():
        health['status'] = 'degraded'
        health['message'] = 'Excel file not found'
    elif health['checks'].get('raw_messages', {}).get('file_count', 0) > 100:
        health['status'] = 'warning'
        health['message'] = 'Large queue of unprocessed messages'
    elif not health['checks'].get('evolution_api', {}).get('connected'):
        health['status'] = 'warning'
        health['message'] = 'Evolution API not connected'
    
    return health


@app.get("/api/evolution/status")
async def evolution_api_status():
    """Get detailed Evolution API status"""
    try:
        api = get_evolution_client()
        if not api:
            return JSONResponse(
                status_code=503,
                content={
                    'status': 'not_configured',
                    'error': 'Evolution API not configured',
                    'help': 'Set EVOLUTION_API_URL and EVOLUTION_API_KEY environment variables'
                }
            )
        
        # Get health check
        health = api.health_check()  # Sync method
        if not health:
            return JSONResponse(
                status_code=503,
                content={
                    'status': 'unreachable',
                    'url': api.base_url,
                    'error': 'Evolution API not responding'
                }
            )
        
        # Get instance status
        instance = api.get_instance_status()  # Sync method
        
        return {
            'status': 'connected',
            'url': api.base_url,
            'version': health.get('version', 'unknown'),
            'instance': instance,
            'timestamp': datetime.now().isoformat()
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                'status': 'error',
                'error': str(e)
            }
        )


@app.post("/api/evolution/send")
async def evolution_send_message(
    to: str = Form(...),
    message: str = Form(...)
):
    """Send a WhatsApp message via Evolution API"""
    try:
        api = get_evolution_client()
        if not api:
            raise HTTPException(status_code=503, detail="Evolution API not configured")
        
        # Send message
        result = api.send_text(to, message)  # Sync method
        
        if result:
            return {'status': 'sent', 'to': to, 'result': result}
        else:
            raise HTTPException(status_code=500, detail="Failed to send message")
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/evolution/initialize")
async def evolution_initialize():
    """Initialize/create Evolution API instance and configure webhook"""
    try:
        api = get_evolution_client()
        if not api:
            raise HTTPException(status_code=503, detail="Evolution API not configured")
        
        # Get webhook URL from config or environment
        config = load_config()
        webhook_url = config.get('evolution', {}).get('webhookUrl') or get_env('EVOLUTION_WEBHOOK_URL')
        
        if not webhook_url:
            # Auto-generate webhook URL
            host = get_env('WEB_HOST', 'localhost')
            port = get_env('WEB_PORT', '8000')
            webhook_url = f"http://{host}:{port}/webhook/evolution"
        
        # Create instance if not exists
        instance_status = api.get_instance_status()  # Sync method
        
        if not instance_status or instance_status.get('state') != 'open':
            # Create new instance
            result = api.create_instance(webhook_url=webhook_url)  # Sync method
            if not result:
                raise HTTPException(status_code=500, detail="Failed to create instance")
        
        # Configure webhook if URL provided
        if webhook_url:
            webhook_result = api.set_webhook(  # Sync method
                webhook_url=webhook_url,
                events=['MESSAGES_UPSERT', 'CONNECTION_UPDATE', 'QRCODE_UPDATED']
            )
        
        # Get current status
        status = api.get_instance_status()  # Sync method
        
        return {
            'status': 'initialized',
            'instance': status,
            'webhook_url': webhook_url
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/backup")
async def create_backup():
    """Create a backup of the Excel file and data files"""
    import shutil
    
    backup_results = {'timestamp': datetime.now().isoformat(), 'files': []}
    backup_dir = DATA_DIR / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Backup Excel file
    excel_path = get_excel_path()
    if excel_path.exists():
        try:
            backup_name = f"fuel_records_{timestamp}.xlsx"
            backup_path = backup_dir / backup_name
            shutil.copy2(excel_path, backup_path)
            backup_results['files'].append({'file': 'fuel_records.xlsx', 'status': 'success', 'backup': backup_name})
        except Exception as e:
            backup_results['files'].append({'file': 'fuel_records.xlsx', 'status': 'error', 'error': str(e)})
    
    # Backup JSON data files
    json_files = ['pending_approvals.json', 'car_last_update.json', 'confirmations.json', 'validation_errors.json']
    for json_file in json_files:
        src = DATA_DIR / json_file
        if src.exists():
            try:
                backup_name = f"{json_file.replace('.json', '')}_{timestamp}.json"
                shutil.copy2(src, backup_dir / backup_name)
                backup_results['files'].append({'file': json_file, 'status': 'success', 'backup': backup_name})
            except Exception as e:
                backup_results['files'].append({'file': json_file, 'status': 'error', 'error': str(e)})
    
    # Clean old backups (keep last 20)
    for pattern in ['fuel_records_*.xlsx', '*_*.json']:
        try:
            backups = sorted(backup_dir.glob(pattern))
            if len(backups) > 20:
                for old_backup in backups[:-20]:
                    old_backup.unlink()
        except:
            pass
    
    backup_results['total'] = len(backup_results['files'])
    backup_results['success_count'] = sum(1 for f in backup_results['files'] if f['status'] == 'success')
    
    return backup_results


@app.post("/api/cleanup")
async def cleanup_old_data():
    """Clean up old processed messages and error files"""
    cleanup_results = {'timestamp': datetime.now().isoformat(), 'cleaned': []}
    
    cutoff = datetime.now() - timedelta(days=30)
    
    # Clean old processed files
    processed_dir = DATA_DIR / 'processed'
    if processed_dir.exists():
        count = 0
        for f in processed_dir.glob('*.json'):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                    f.unlink()
                    count += 1
            except:
                pass
        cleanup_results['cleaned'].append({'folder': 'processed', 'files_removed': count})
    
    # Clean old error files
    errors_dir = DATA_DIR / 'errors'
    if errors_dir.exists():
        count = 0
        for f in errors_dir.glob('*.json'):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                    f.unlink()
                    count += 1
            except:
                pass
        cleanup_results['cleaned'].append({'folder': 'errors', 'files_removed': count})
    
    # Clean old approvals (non-pending, older than 30 days)
    approvals_path = DATA_DIR / 'pending_approvals.json'
    approvals = safe_json_load(approvals_path, [])
    if approvals:
        original_count = len(approvals)
        new_approvals = []
        for a in approvals:
            if a.get('status') == 'pending':
                new_approvals.append(a)
            else:
                try:
                    ts = datetime.fromisoformat(a.get('timestamp', '2000-01-01').replace('Z', '+00:00'))
                    if ts.replace(tzinfo=None) > cutoff:
                        new_approvals.append(a)
                except:
                    new_approvals.append(a)  # Keep if we can't parse
        
        removed = original_count - len(new_approvals)
        if removed > 0:
            safe_json_save(approvals_path, new_approvals)
            cleanup_results['cleaned'].append({'file': 'pending_approvals.json', 'entries_removed': removed})
    
    return cleanup_results


@app.post("/api/sync")
async def sync_data_sources():
    """
    Synchronize records between Database and Google Sheets.
    
    - Records in DB but not in Sheets → Add to Sheets
    - Records in Sheets but not in DB → Add to DB
    
    Uses (datetime, car, odometer) as unique identifier for each record.
    """
    sync_results = {
        'timestamp': datetime.now().isoformat(),
        'db_to_sheets': 0,
        'sheets_to_db': 0,
        'errors': [],
        'status': 'success'
    }
    
    try:
        # Load config
        config = load_config()
        spreadsheet_id = (
            config.get('upload', {}).get('google', {}).get('spreadsheetId') or
            get_env('GOOGLE_SHEETS_SPREADSHEET_ID')
        )
        worksheet_name = (
            config.get('upload', {}).get('google', {}).get('sheetName') or
            'SYSTEM FUEL TRACKER'
        )
        
        if not spreadsheet_id:
            sync_results['status'] = 'error'
            sync_results['errors'].append('Google Sheets not configured')
            return sync_results
        
        # Load records from both sources
        db_records = load_records_from_db()
        sheets_records = load_records_from_sheets()
        
        print(f"[SYNC] DB has {len(db_records)} records, Sheets has {len(sheets_records)} records")
        
        # Create unique keys for comparison: (datetime, car, odometer)
        def make_key(r):
            dt = r.get('datetime', '')[:16] if r.get('datetime') else ''  # Truncate to minute
            car = str(r.get('car', '')).upper().replace(' ', '')
            odo = str(r.get('odometer', '0'))
            return f"{dt}|{car}|{odo}"
        
        db_keys = {make_key(r): r for r in db_records}
        sheets_keys = {make_key(r): r for r in sheets_records}
        
        # Find records in DB but not in Sheets → Add to Sheets
        db_only = [r for k, r in db_keys.items() if k not in sheets_keys]
        if db_only:
            print(f"[SYNC] Found {len(db_only)} records in DB not in Sheets")
            try:
                uploader = GoogleSheetsUploader(spreadsheet_id=spreadsheet_id, worksheet_name=worksheet_name)
                columns = ['DATETIME', 'DEPARTMENT', 'DRIVER', 'CAR', 'LITERS', 'AMOUNT', 'TYPE', 'ODOMETER', 'SENDER', 'RAW_MESSAGE']
                uploader.ensure_headers(columns)
                
                for record in db_only:
                    try:
                        uploader.append_record(record, columns)
                        sync_results['db_to_sheets'] += 1
                    except Exception as e:
                        sync_results['errors'].append(f"Failed to add record to Sheets: {e}")
                print(f"[SYNC] Added {sync_results['db_to_sheets']} records to Sheets")
            except Exception as e:
                sync_results['errors'].append(f"Sheets connection error: {e}")
        
        # Find records in Sheets but not in DB → Add to DB
        sheets_only = [r for k, r in sheets_keys.items() if k not in db_keys]
        if sheets_only:
            print(f"[SYNC] Found {len(sheets_only)} records in Sheets not in DB")
            try:
                db = Database()
                for record in sheets_only:
                    try:
                        # Format record for DB
                        db_record = {
                            'datetime': record.get('datetime', ''),
                            'department': record.get('department', ''),
                            'driver': record.get('driver', ''),
                            'car': record.get('car', ''),
                            'liters': record.get('liters', 0),
                            'amount': record.get('amount', 0),
                            'type': record.get('type', ''),
                            'odometer': record.get('odometer', 0),
                            'sender': record.get('sender', ''),
                            'raw_message': record.get('raw_message', ''),
                        }
                        if db.insert_fuel_record(db_record):
                            sync_results['sheets_to_db'] += 1
                    except Exception as e:
                        sync_results['errors'].append(f"Failed to add record to DB: {e}")
                print(f"[SYNC] Added {sync_results['sheets_to_db']} records to DB")
            except Exception as e:
                sync_results['errors'].append(f"DB connection error: {e}")
        
        if sync_results['errors']:
            sync_results['status'] = 'partial'
        
        print(f"[SYNC] Complete: DB→Sheets: {sync_results['db_to_sheets']}, Sheets→DB: {sync_results['sheets_to_db']}")
        
    except Exception as e:
        sync_results['status'] = 'error'
        sync_results['errors'].append(str(e))
        print(f"[SYNC] Error: {e}")
    
    return sync_results


# ============================================================================
# ADMIN PANEL ROUTES
# ============================================================================

def create_admin_session() -> str:
    """Create a new admin session token"""
    token = secrets.token_urlsafe(32)
    expiry = datetime.now() + timedelta(hours=SESSION_DURATION_HOURS)
    ADMIN_SESSIONS[token] = expiry
    return token


def verify_admin_session(token: Optional[str]) -> bool:
    """Verify if admin session is valid"""
    if not token or token not in ADMIN_SESSIONS:
        return False
    expiry = ADMIN_SESSIONS.get(token)
    if expiry and datetime.now() < expiry:
        return True
    # Remove expired session
    ADMIN_SESSIONS.pop(token, None)
    return False


def log_admin_action(action: str, details: str = '', result: str = 'success'):
    """Log an admin action to the audit log"""
    log_entry = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'action': action,
        'details': details,
        'result': result
    }
    
    logs = safe_json_load(AUDIT_LOG_PATH, [])
    if not isinstance(logs, list):
        logs = []
    
    logs.append(log_entry)
    
    # Keep only last 500 entries
    if len(logs) > 500:
        logs = logs[-500:]
    
    safe_json_save(AUDIT_LOG_PATH, logs)
    print(f"[AUDIT] {action}: {details} - {result}")


def save_fleet_to_processor(plates: set) -> bool:
    """Save updated fleet to processor.py"""
    processor_path = Path(__file__).parent / 'processor.py'
    
    if not processor_path.exists():
        return False
    
    try:
        with open(processor_path, 'r') as f:
            content = f.read()
        
        # Build new ALLOWED_PLATES set
        sorted_plates = sorted(plates)
        plates_per_line = 7
        lines = []
        for i in range(0, len(sorted_plates), plates_per_line):
            batch = sorted_plates[i:i+plates_per_line]
            lines.append("    " + ", ".join(f"'{p}'" for p in batch))
        
        new_plates_str = "ALLOWED_PLATES = {\n" + ",\n".join(lines) + "\n}"
        
        # Replace the existing ALLOWED_PLATES
        pattern = r"ALLOWED_PLATES\s*=\s*\{[^}]+\}"
        new_content = regex_module.sub(pattern, new_plates_str, content, count=1)
        
        with open(processor_path, 'w') as f:
            f.write(new_content)
        
        return True
    except Exception as e:
        print(f"[ERROR] Failed to save fleet: {e}")
        return False


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, error: str = None):
    """Admin login page"""
    return templates.TemplateResponse("admin_login.html", {
        "request": request,
        "error": error
    })


@app.post("/admin/login")
async def admin_login(request: Request, password: str = Form(...)):
    """Process admin login"""
    if password == ADMIN_PASSWORD:
        token = create_admin_session()
        log_admin_action('admin_login', 'Admin logged in', 'success')
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie(
            key="admin_session",
            value=token,
            max_age=SESSION_DURATION_HOURS * 3600,
            httponly=True,
            samesite="lax"
        )
        return response
    
    log_admin_action('admin_login', 'Failed login attempt', 'failed')
    return templates.TemplateResponse("admin_login.html", {
        "request": request,
        "error": "Invalid password"
    })


@app.post("/admin/logout")
async def admin_logout(request: Request):
    """Process admin logout"""
    token = request.cookies.get("admin_session")
    if token:
        ADMIN_SESSIONS.pop(token, None)
    log_admin_action('admin_logout', 'Admin logged out', 'success')
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("admin_session")
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Admin panel page"""
    token = request.cookies.get("admin_session")
    if not verify_admin_session(token):
        return RedirectResponse(url="/admin/login", status_code=303)
    
    # Get system stats
    config = load_config()
    fleet = load_fleet()
    
    # Count records from both sources
    db_records = []
    sheets_records = []
    evolution_connected = False
    
    try:
        db_records = load_records_from_db()
    except:
        pass
    
    try:
        sheets_records = load_records_from_sheets()
    except:
        pass
    
    try:
        api = get_evolution_client()
        if api:
            health = api.health_check()
            evolution_connected = bool(health)
    except:
        pass
    
    pending = load_pending_approvals()
    
    stats = {
        'db_records': len(db_records),
        'sheets_records': len(sheets_records),
        'pending_approvals': len(pending),
        'evolution_connected': evolution_connected
    }
    
    config_info = {
        'group_jid': config.get('whatsapp', {}).get('groupJid', 'Not set'),
        'cooldown_hours': 12,  # From processor.py
        'webhook_url': config.get('evolution', {}).get('webhookUrl', 'Not set'),
        'upload_db': config.get('upload', {}).get('toDatabase', False),
        'upload_sheets': config.get('upload', {}).get('toGoogleSheets', False)
    }
    
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "stats": stats,
        "config": config_info,
        "fleet": fleet,
        "fleet_count": len(fleet)
    })


@app.get("/api/admin/export")
async def admin_export_backup(request: Request):
    """Export all data as JSON backup"""
    token = request.cookies.get("admin_session")
    if not verify_admin_session(token):
        raise HTTPException(status_code=403, detail="Not authenticated")
    
    backup = {
        'exported_at': datetime.now().isoformat(),
        'db_records': [],
        'sheets_records': [],
        'pending_approvals': [],
        'fleet': []
    }
    
    try:
        backup['db_records'] = [
            {k: v for k, v in r.items() if k != 'datetime_obj'}
            for r in load_records_from_db()
        ]
    except Exception as e:
        backup['db_error'] = str(e)
    
    try:
        backup['sheets_records'] = [
            {k: v for k, v in r.items() if k != 'datetime_obj'}
            for r in load_records_from_sheets()
        ]
    except Exception as e:
        backup['sheets_error'] = str(e)
    
    backup['pending_approvals'] = load_pending_approvals()
    backup['fleet'] = load_fleet()
    
    log_admin_action('export_backup', f"Exported {len(backup['db_records'])} DB records, {len(backup['sheets_records'])} Sheets records")
    
    return Response(
        content=json.dumps(backup, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=fuel_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        }
    )


@app.post("/api/admin/clear-all")
async def admin_clear_all(request: Request):
    """Clear ALL data - DB, Sheets, and all local JSON/cache files. Fresh start!"""
    token = request.cookies.get("admin_session")
    if not verify_admin_session(token):
        raise HTTPException(status_code=403, detail="Not authenticated")
    
    results = {
        'db_cleared': False, 
        'sheets_cleared': False, 
        'json_files_cleared': 0,
        'cache_folders_cleared': 0,
        'errors': []
    }
    
    # 1. Clear Database
    try:
        db = Database()
        if db.engine:
            from sqlalchemy import text
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM fuel_records"))
                db_count = result.scalar()
                conn.execute(text("DELETE FROM fuel_records"))
                conn.commit()
            results['db_cleared'] = True
            results['db_records_deleted'] = db_count
            print(f"[ADMIN] Cleared {db_count} records from database")
    except Exception as e:
        results['errors'].append(f"DB: {e}")
    
    # 2. Clear Google Sheets
    try:
        config = load_config()
        spreadsheet_id = config.get('upload', {}).get('google', {}).get('spreadsheetId') or get_env('GOOGLE_SHEETS_SPREADSHEET_ID')
        worksheet_name = config.get('upload', {}).get('google', {}).get('sheetName') or 'SYSTEM FUEL TRACKER'
        
        if spreadsheet_id:
            uploader = GoogleSheetsUploader(spreadsheet_id=spreadsheet_id, worksheet_name=worksheet_name)
            worksheet = uploader.worksheet
            sheets_count = max(0, worksheet.row_count - 1)
            if worksheet.row_count > 1:
                worksheet.delete_rows(2, worksheet.row_count)
            results['sheets_cleared'] = True
            results['sheets_records_deleted'] = sheets_count
            print(f"[ADMIN] Cleared {sheets_count} records from Google Sheets")
    except Exception as e:
        results['errors'].append(f"Sheets: {e}")
    
    # 3. Clear all JSON data files in data/ folder
    json_files_to_clear = [
        'car_last_update.json',
        'car_summary.json', 
        'driver_history.json',
        'last_processed.json',
        'pending_approvals.json',
        'validation_errors.json',
        'audit_log.json',
    ]
    
    for json_file in json_files_to_clear:
        file_path = DATA_DIR / json_file
        try:
            if file_path.exists():
                # Reset to empty state
                if json_file in ['car_last_update.json', 'car_summary.json', 'driver_history.json']:
                    safe_json_save(file_path, {})
                else:
                    safe_json_save(file_path, [])
                results['json_files_cleared'] += 1
                print(f"[ADMIN] Cleared {json_file}")
        except Exception as e:
            results['errors'].append(f"{json_file}: {e}")
    
    # 4. Clear all cache folders (processed, raw_messages, errors, backups, output)
    cache_folders = ['processed', 'raw_messages', 'errors', 'backups', 'output']
    
    for folder_name in cache_folders:
        folder_path = DATA_DIR / folder_name
        try:
            if folder_path.exists():
                files_removed = 0
                for f in folder_path.glob('*'):
                    if f.is_file():
                        try:
                            f.unlink()
                            files_removed += 1
                        except:
                            pass
                if files_removed > 0:
                    results['cache_folders_cleared'] += 1
                    print(f"[ADMIN] Cleared {files_removed} files from {folder_name}/")
        except Exception as e:
            results['errors'].append(f"{folder_name}/: {e}")
    
    # Log the action (this creates a new audit log entry in the now-empty log)
    summary = f"DB: {results.get('db_records_deleted', 0)}, Sheets: {results.get('sheets_records_deleted', 0)}, JSON: {results['json_files_cleared']}, Folders: {results['cache_folders_cleared']}"
    log_admin_action('clear_all_data', summary, 'success' if not results['errors'] else 'partial')
    
    return {
        'status': 'success' if not results['errors'] else 'partial',
        'message': f"🧹 FULL RESET COMPLETE!\n• Database: {results.get('db_records_deleted', 0)} records deleted\n• Sheets: {results.get('sheets_records_deleted', 0)} records deleted\n• JSON files reset: {results['json_files_cleared']}\n• Cache folders cleared: {results['cache_folders_cleared']}",
        **results
    }


@app.post("/api/admin/clear-db")
async def admin_clear_db(request: Request):
    """Clear all data from Database only"""
    token = request.cookies.get("admin_session")
    if not verify_admin_session(token):
        raise HTTPException(status_code=403, detail="Not authenticated")
    
    try:
        db = Database()
        if db.engine:
            from sqlalchemy import text
            with db.engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM fuel_records"))
                count = result.scalar()
                conn.execute(text("DELETE FROM fuel_records"))
                conn.commit()
            
            log_admin_action('clear_db', f"Deleted {count} records from database")
            return {'status': 'success', 'message': f'Deleted {count} records from database'}
    except Exception as e:
        log_admin_action('clear_db', str(e), 'failed')
        return {'status': 'error', 'message': str(e)}


@app.post("/api/admin/clear-sheets")
async def admin_clear_sheets(request: Request):
    """Clear all data from Google Sheets only"""
    token = request.cookies.get("admin_session")
    if not verify_admin_session(token):
        raise HTTPException(status_code=403, detail="Not authenticated")
    
    try:
        config = load_config()
        spreadsheet_id = config.get('upload', {}).get('google', {}).get('spreadsheetId') or get_env('GOOGLE_SHEETS_SPREADSHEET_ID')
        worksheet_name = config.get('upload', {}).get('google', {}).get('sheetName') or 'SYSTEM FUEL TRACKER'
        
        if spreadsheet_id:
            uploader = GoogleSheetsUploader(spreadsheet_id=spreadsheet_id, worksheet_name=worksheet_name)
            worksheet = uploader.worksheet
            row_count = worksheet.row_count - 1  # Exclude header
            if row_count > 0:
                worksheet.delete_rows(2, worksheet.row_count)
            
            log_admin_action('clear_sheets', f"Deleted {row_count} records from Google Sheets")
            return {'status': 'success', 'message': f'Deleted {row_count} records from Google Sheets'}
        else:
            return {'status': 'error', 'message': 'Google Sheets not configured'}
    except Exception as e:
        log_admin_action('clear_sheets', str(e), 'failed')
        return {'status': 'error', 'message': str(e)}


@app.post("/api/admin/fleet/add")
async def admin_add_vehicle(request: Request):
    """Add a vehicle to the fleet"""
    token = request.cookies.get("admin_session")
    if not verify_admin_session(token):
        raise HTTPException(status_code=403, detail="Not authenticated")
    
    data = await request.json()
    plate = data.get('plate', '').upper().replace(' ', '')
    
    if not plate:
        return {'status': 'error', 'message': 'Plate number required'}
    
    # Load current fleet
    fleet = set(load_fleet())
    
    if plate in fleet:
        return {'status': 'exists', 'message': f'{plate} already in fleet'}
    
    fleet.add(plate)
    
    if save_fleet_to_processor(fleet):
        log_admin_action('add_vehicle', plate)
        return {'status': 'added', 'message': f'{plate} added to fleet'}
    else:
        return {'status': 'error', 'message': 'Failed to save fleet'}


@app.post("/api/admin/fleet/remove")
async def admin_remove_vehicle(request: Request):
    """Remove a vehicle from the fleet"""
    token = request.cookies.get("admin_session")
    if not verify_admin_session(token):
        raise HTTPException(status_code=403, detail="Not authenticated")
    
    data = await request.json()
    plate = data.get('plate', '').upper().replace(' ', '')
    
    if not plate:
        return {'status': 'error', 'message': 'Plate number required'}
    
    # Load current fleet
    fleet = set(load_fleet())
    
    if plate not in fleet:
        return {'status': 'not_found', 'message': f'{plate} not in fleet'}
    
    fleet.discard(plate)
    
    if save_fleet_to_processor(fleet):
        log_admin_action('remove_vehicle', plate)
        return {'status': 'removed', 'message': f'{plate} removed from fleet'}
    else:
        return {'status': 'error', 'message': 'Failed to save fleet'}


@app.post("/api/admin/clear-cooldowns")
async def admin_clear_cooldowns(request: Request):
    """Clear all car cooldowns"""
    token = request.cookies.get("admin_session")
    if not verify_admin_session(token):
        raise HTTPException(status_code=403, detail="Not authenticated")
    
    cooldowns_path = DATA_DIR / 'car_last_update.json'
    
    try:
        if cooldowns_path.exists():
            data = safe_json_load(cooldowns_path, {})
            count = len(data)
            safe_json_save(cooldowns_path, {})
            log_admin_action('clear_cooldowns', f"Cleared {count} cooldowns")
            return {'status': 'success', 'message': f'Cleared {count} cooldowns'}
        return {'status': 'success', 'message': 'No cooldowns to clear'}
    except Exception as e:
        log_admin_action('clear_cooldowns', str(e), 'failed')
        return {'status': 'error', 'message': str(e)}


@app.post("/api/admin/clear-pending")
async def admin_clear_pending(request: Request):
    """Clear all pending approvals"""
    token = request.cookies.get("admin_session")
    if not verify_admin_session(token):
        raise HTTPException(status_code=403, detail="Not authenticated")
    
    pending_path = DATA_DIR / 'pending_approvals.json'
    
    try:
        if pending_path.exists():
            data = safe_json_load(pending_path, [])
            count = len(data) if isinstance(data, list) else 0
            safe_json_save(pending_path, [])
            log_admin_action('clear_pending', f"Cleared {count} pending approvals")
            return {'status': 'success', 'message': f'Cleared {count} pending approvals'}
        return {'status': 'success', 'message': 'No pending approvals to clear'}
    except Exception as e:
        log_admin_action('clear_pending', str(e), 'failed')
        return {'status': 'error', 'message': str(e)}


@app.post("/api/admin/clear-messages")
async def admin_clear_messages(request: Request):
    """Clear processed and raw message cache"""
    token = request.cookies.get("admin_session")
    if not verify_admin_session(token):
        raise HTTPException(status_code=403, detail="Not authenticated")
    
    files_removed = 0
    
    try:
        for folder in ['processed', 'raw_messages']:
            folder_path = DATA_DIR / folder
            if folder_path.exists():
                for f in folder_path.glob('*.json'):
                    try:
                        f.unlink()
                        files_removed += 1
                    except:
                        pass
        
        log_admin_action('clear_messages', f"Removed {files_removed} message files")
        return {'status': 'success', 'files_removed': files_removed}
    except Exception as e:
        log_admin_action('clear_messages', str(e), 'failed')
        return {'status': 'error', 'message': str(e)}


@app.post("/api/admin/audit-log")
async def admin_get_audit_log(request: Request):
    """Get audit log (requires separate password)"""
    token = request.cookies.get("admin_session")
    if not verify_admin_session(token):
        raise HTTPException(status_code=403, detail="Not authenticated")
    
    data = await request.json()
    password = data.get('password', '')
    
    if password != AUDIT_LOG_PASSWORD:
        return JSONResponse(status_code=403, content={'error': 'Invalid audit log password'})
    
    logs = safe_json_load(AUDIT_LOG_PATH, [])
    return {'logs': logs}


# ============================================================================
# END ADMIN PANEL ROUTES
# ============================================================================


@app.get("/api/records")
async def api_records(limit: int = 100, offset: int = 0):
    """API endpoint for records"""
    records = load_records()
    return records[offset:offset+limit]


@app.get("/api/chart-data")
async def api_chart_data(days: int = 7):
    """API endpoint for chart data"""
    records = load_records()
    return get_chart_data(records, days=days)


# Real-time updates via Server-Sent Events (SSE)
async def event_generator():
    """Generate SSE events for real-time updates"""
    last_modified = {}
    files_to_watch = [
        get_excel_path(),
        DATA_DIR / 'pending_approvals.json',
        DATA_DIR / 'car_last_update.json',
        DATA_DIR / 'confirmations.json',
    ]
    
    # Initialize last modified times
    for file_path in files_to_watch:
        if file_path.exists():
            last_modified[str(file_path)] = file_path.stat().st_mtime
        else:
            last_modified[str(file_path)] = 0
    
    while True:
        try:
            changed = False
            
            # Check for file changes
            for file_path in files_to_watch:
                path_str = str(file_path)
                if file_path.exists():
                    current_mtime = file_path.stat().st_mtime
                    if current_mtime != last_modified.get(path_str, 0):
                        last_modified[path_str] = current_mtime
                        changed = True
            
            if changed:
                # Load fresh data
                records = load_records()
                stats = get_stats(records)
                chart_data = get_chart_data(records, days=7)
                approvals = load_pending_approvals()
                
                # Build update payload
                update_data = {
                    'type': 'update',
                    'timestamp': datetime.now().isoformat(),
                    'stats': stats,
                    'chart_data': chart_data,
                    'pending_approvals': len(approvals),
                    'recent_records': [
                        {k: v for k, v in r.items() if k != 'datetime_obj'} 
                        for r in records[:10]
                    ],
                }
                
                yield f"data: {json.dumps(update_data)}\n\n"
            
            # Send heartbeat every 30 seconds
            await asyncio.sleep(2)
            yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': datetime.now().isoformat()})}\n\n"
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            await asyncio.sleep(5)


@app.get("/api/stream")
async def stream_updates():
    """SSE endpoint for real-time updates"""
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/api/dashboard")
async def api_dashboard():
    """API endpoint for dashboard data (for real-time updates)"""
    records = load_records()
    stats = get_stats(records)
    chart_data = get_chart_data(records, days=7)
    
    return {
        'stats': stats,
        'chart_data': chart_data,
        'recent_records': [
            {k: v for k, v in r.items() if k != 'datetime_obj'} 
            for r in records[:10]
        ],
    }


def is_port_available(port: int, host: str = "0.0.0.0") -> bool:
    """Check if a port is available for binding"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True
    except OSError:
        return False


def find_available_port(start_port: int = 8080, max_attempts: int = 100, host: str = "0.0.0.0") -> Tuple[int, bool]:
    """
    Find an available port starting from start_port.
    
    Returns:
        Tuple of (port, was_original_available)
        - port: The available port found
        - was_original_available: True if start_port was available, False if we had to find another
    """
    # First check if the requested port is available
    if is_port_available(start_port, host):
        return start_port, True
    
    # Search for an available port
    for offset in range(1, max_attempts + 1):
        port = start_port + offset
        if port > 65535:
            break
        if is_port_available(port, host):
            return port, False
    
    raise RuntimeError(f"Could not find an available port after {max_attempts} attempts starting from {start_port}")


def run_server(host: str = "0.0.0.0", port: int = 8080, auto_port: bool = True):
    """
    Run the web server.
    
    Args:
        host: Host to bind to
        port: Preferred port to use
        auto_port: If True, automatically find an available port if preferred port is in use
    """
    import uvicorn
    
    actual_port = port
    
    if auto_port:
        try:
            actual_port, was_original = find_available_port(port, host=host)
            if not was_original:
                print(f"\n[!] Port {port} is in use, switching to port {actual_port}")
        except RuntimeError as e:
            print(f"\n[ERROR] {e}")
            return
    else:
        # Check if port is available when auto_port is disabled
        if not is_port_available(port, host):
            print(f"\n[ERROR] Port {port} is already in use.")
            print(f"   Try a different port with --port <number>")
            print(f"   Or let the system find one with --port auto")
            return
    
    print(f"\n[START] Starting Fuel Extractor Dashboard...")
    print(f"   Local:   http://localhost:{actual_port}")
    print(f"   Network: http://{host}:{actual_port}")
    print(f"\n   Press Ctrl+C to stop\n")
    
    uvicorn.run(app, host=host, port=actual_port, log_level="info")


if __name__ == "__main__":
    run_server()
