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

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
except ImportError:
    from env import load_env, get_env
    from db import Database
    from google_sheets_uploader import GoogleSheetsUploader

# Data source constants
DATA_SOURCE_EXCEL = 'excel'
DATA_SOURCE_DB = 'database'
DATA_SOURCE_SHEETS = 'sheets'

# Paths
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / 'data'
CONFIG_PATH = ROOT_DIR / 'config.json'
TEMPLATES_DIR = Path(__file__).parent / 'templates'
STATIC_DIR = Path(__file__).parent / 'static'
EFFICIENCY_HISTORY_PATH = DATA_DIR / 'efficiency_history.json'

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


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    print("[STOP] Shutting down Fuel Extractor Dashboard...")

# Mount static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- Admin Panel & Mobile View ---
from fastapi import Response, Cookie
from fastapi.responses import RedirectResponse

ADMIN_PASSWORD = "Nala2024"

def is_admin_authenticated(request: Request) -> bool:
    return request.cookies.get("admin_auth") == "1"

@app.get("/admin", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    if is_admin_authenticated(request):
        return templates.TemplateResponse("admin.html", {"request": request})
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})

@app.post("/admin", response_class=HTMLResponse)
async def admin_login(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        response = RedirectResponse("/admin", status_code=302)
        response.set_cookie(key="admin_auth", value="1", httponly=True, max_age=3600)
        return response
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": "Incorrect password."})

@app.get("/admin/logout")
async def admin_logout():
    response = RedirectResponse("/admin", status_code=302)
    response.delete_cookie("admin_auth")
    return response

# --- Admin API Endpoints ---
@app.post("/api/admin/{action}")
async def admin_action(action: str, request: Request):
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=403, detail="Not authorized")
    # Dummy implementations for now
    if action == "start_listener":
        # TODO: Implement actual start logic
        return {"message": "Listener started"}
    if action == "stop_listener":
        return {"message": "Listener stopped"}
    if action == "start_processor":
        return {"message": "Processor started"}
    if action == "stop_processor":
        return {"message": "Processor stopped"}
    if action == "reset_system":
        return {"message": "System reset"}
    if action == "scan_qr":
        return {"message": "QR code scan triggered"}
    return {"message": "Unknown action"}

@app.get("/api/admin/status")
async def admin_status(request: Request):
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=403, detail="Not authorized")
    # Dummy status for now
    return {"listener": "running", "processor": "running"}

@app.get("/api/admin/logs")
async def admin_logs(request: Request):
    if not is_admin_authenticated(request):
        raise HTTPException(status_code=403, detail="Not authorized")
    # Dummy logs for now
    return "[OK] Listener running\n[OK] Processor running\n[MSG] System healthy"


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
        spreadsheet_id = config.get('google_sheets', {}).get('spreadsheetId') or get_env('GOOGLE_SHEETS_SPREADSHEET_ID')
        worksheet_name = config.get('google_sheets', {}).get('worksheetName', 'FUEL RECORDS')
        
        if not spreadsheet_id:
            print("Google Sheets not configured (no spreadsheet ID)")
            return records
        
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
    """Load records with automatic fallback: Sheets -> Database -> Excel.
    
    Priority order:
    1. Google Sheets (primary)
    2. Database (fallback)
    3. Excel file (last resort)
    
    Returns: (records, source_used)
    """
    # Try Google Sheets first
    try:
        records = load_records_from_sheets(start_date=start_date, end_date=end_date)
        if records:
            print(f"[DATA] Loaded {len(records)} records from Google Sheets")
            return records, DATA_SOURCE_SHEETS
    except Exception as e:
        print(f"[WARN] Google Sheets failed: {e}")
    
    # Try Database second
    try:
        records = load_records_from_db(start_date=start_date, end_date=end_date)
        if records:
            print(f"[DATA] Loaded {len(records)} records from Database")
            return records, DATA_SOURCE_DB
    except Exception as e:
        print(f"[WARN] Database failed: {e}")
    
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


def load_efficiency_history(days: int = 30) -> List[Dict]:
    """Load fuel efficiency history records."""
    history = safe_json_load(EFFICIENCY_HISTORY_PATH, [])
    if not isinstance(history, list):
        return []
    
    cutoff = datetime.now() - timedelta(days=days)
    filtered = []
    
    for record in history:
        try:
            record_time = datetime.fromisoformat(record.get('timestamp', ''))
            if record_time >= cutoff:
                filtered.append(record)
        except:
            pass
    
    return filtered


def get_efficiency_stats(days: int = 30) -> Dict:
    """Calculate fleet-wide efficiency statistics."""
    history = load_efficiency_history(days)
    
    if not history:
        return {
            'records': 0,
            'avg_efficiency': 0,
            'min_efficiency': 0,
            'max_efficiency': 0,
            'total_distance': 0,
            'total_liters': 0,
            'by_vehicle': [],
            'alerts': {'low': 0, 'high': 0}
        }
    
    efficiencies = [r['efficiency'] for r in history if r.get('efficiency')]
    total_distance = sum(r.get('distance', 0) for r in history)
    total_liters = sum(r.get('liters', 0) for r in history)
    
    # Count alerts (thresholds from processor)
    EFFICIENCY_ALERT_LOW = 4.0
    EFFICIENCY_ALERT_HIGH = 20.0
    low_alerts = sum(1 for e in efficiencies if e < EFFICIENCY_ALERT_LOW)
    high_alerts = sum(1 for e in efficiencies if e > EFFICIENCY_ALERT_HIGH)
    
    # Group by vehicle
    vehicle_stats = {}
    for record in history:
        car = record.get('car', 'Unknown')
        if car not in vehicle_stats:
            vehicle_stats[car] = {'efficiencies': [], 'distance': 0, 'liters': 0}
        vehicle_stats[car]['efficiencies'].append(record.get('efficiency', 0))
        vehicle_stats[car]['distance'] += record.get('distance', 0)
        vehicle_stats[car]['liters'] += record.get('liters', 0)
    
    by_vehicle = []
    for car, stats in vehicle_stats.items():
        effs = [e for e in stats['efficiencies'] if e]
        if effs:
            by_vehicle.append({
                'car': car,
                'avg_efficiency': round(sum(effs) / len(effs), 2),
                'records': len(effs),
                'total_distance': stats['distance'],
                'total_liters': round(stats['liters'], 2)
            })
    
    # Sort by efficiency (best first)
    by_vehicle.sort(key=lambda x: x['avg_efficiency'], reverse=True)
    
    return {
        'records': len(history),
        'avg_efficiency': round(sum(efficiencies) / len(efficiencies), 2) if efficiencies else 0,
        'min_efficiency': round(min(efficiencies), 2) if efficiencies else 0,
        'max_efficiency': round(max(efficiencies), 2) if efficiencies else 0,
        'total_distance': total_distance,
        'total_liters': round(total_liters, 2),
        'by_vehicle': by_vehicle[:20],  # Top 20
        'alerts': {'low': low_alerts, 'high': high_alerts}
    }


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


# PWA Routes - Service Worker needs to be at root for proper scope
@app.get("/sw.js")
async def service_worker():
    """Serve service worker from root for proper PWA scope"""
    sw_path = STATIC_DIR / 'sw.js'
    if sw_path.exists():
        content = sw_path.read_text()
        return HTMLResponse(content=content, media_type="application/javascript")
    raise HTTPException(status_code=404, detail="Service worker not found")


@app.get("/offline.html", response_class=HTMLResponse)
async def offline_page():
    """Serve offline fallback page"""
    offline_path = STATIC_DIR / 'offline.html'
    if offline_path.exists():
        return HTMLResponse(content=offline_path.read_text())
    return HTMLResponse(content="<h1>Offline</h1><p>Please check your connection.</p>")


# Routes
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page"""
    records = load_records()
    stats = get_stats(records)
    chart_data = get_chart_data(records, days=7)
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "chart_data": json.dumps(chart_data),
        "recent_records": records[:10],
    })


@app.get("/records", response_class=HTMLResponse)
async def records_page(request: Request, search: str = "", page: int = 1):
    """Records listing page"""
    records = load_records()
    
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
    """Approve a pending record with safe file operations"""
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
            
            # Create raw message file for processor (like Node.js does)
            record = approval.get('record', {})
            if record and approval.get('type') in ['car_cooldown', 'driver_change', 'edit']:
                raw_messages_dir = DATA_DIR / 'raw_messages'
                raw_messages_dir.mkdir(exist_ok=True)
                
                raw_msg_file = raw_messages_dir / f"msg_approved_{approval_id}_{int(datetime.now().timestamp() * 1000)}.json"
                
                msg_data = {
                    'id': f'approved_{approval_id}',
                    'timestamp': int(datetime.now().timestamp()),
                    'datetime': datetime.now().isoformat(),
                    'groupName': 'Approved',
                    'groupId': '',
                    'senderPhone': '',
                    'senderName': record.get('sender', 'Web Approved'),
                    'body': f"FUEL UPDATE\nDEPARTMENT: {record.get('department', '')}\nDRIVER: {record.get('driver', '')}\nCAR: {record.get('car', '')}\nLITERS: {record.get('liters', '')}\nAMOUNT: {record.get('amount', '')}\nTYPE: {record.get('type', '')}\nODOMETER: {record.get('odometer', '')}",
                    'capturedAt': datetime.now().isoformat(),
                    'isApproved': True,
                    'approvalType': approval.get('type'),
                    'originalApprovalId': approval_id
                }
                
                safe_json_save(raw_msg_file, msg_data)
            
            return JSONResponse({"status": "approved", "id": approval_id})
    
    raise HTTPException(status_code=404, detail="Approval not found")


@app.post("/approvals/{approval_id}/reject")
async def reject_record(approval_id: str):
    """Reject a pending record with safe file operations"""
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
            
            # Send rejection notification to WhatsApp
            record = approval.get('record', {})
            if record:
                confirmations_path = DATA_DIR / 'confirmations.json'
                confirmations = safe_json_load(confirmations_path, [])
                
                reject_msg = f"[REJECTED] *FUEL REPORT REJECTED*\n\n"
                reject_msg += f"Vehicle: {record.get('car', 'N/A')}\n"
                reject_msg += f"Driver: {record.get('driver', 'N/A')}\n"
                reject_msg += f"Reason: {approval.get('reason', 'Admin rejected via web')}\n"
                reject_msg += f"\n_Rejected via Web Dashboard_"
                
                confirmations.append({
                    'timestamp': datetime.now().isoformat(),
                    'message': reject_msg,
                    'notified': False
                })
                
                safe_json_save(confirmations_path, confirmations)
            
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


@app.get("/api/efficiency")
async def api_efficiency(days: int = 30):
    """API endpoint for fuel efficiency statistics"""
    return get_efficiency_stats(days)


@app.get("/api/efficiency/{car}")
async def api_efficiency_vehicle(car: str, days: int = 30):
    """API endpoint for vehicle-specific efficiency data"""
    history = load_efficiency_history(days)
    normalized_car = car.upper().replace(' ', '').replace('-', '')
    
    vehicle_records = [r for r in history if r.get('car', '').replace(' ', '').replace('-', '') == normalized_car]
    
    if not vehicle_records:
        return {'car': car, 'records': 0, 'message': 'No efficiency data found'}
    
    efficiencies = [r['efficiency'] for r in vehicle_records if r.get('efficiency')]
    
    return {
        'car': car,
        'records': len(vehicle_records),
        'avg_efficiency': round(sum(efficiencies) / len(efficiencies), 2) if efficiencies else 0,
        'min_efficiency': round(min(efficiencies), 2) if efficiencies else 0,
        'max_efficiency': round(max(efficiencies), 2) if efficiencies else 0,
        'total_distance': sum(r.get('distance', 0) for r in vehicle_records),
        'total_liters': round(sum(r.get('liters', 0) for r in vehicle_records), 2),
        'history': vehicle_records[-10:]  # Last 10 records
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
    
    # Overall status
    if not excel_path.exists():
        health['status'] = 'degraded'
        health['message'] = 'Excel file not found'
    elif health['checks'].get('raw_messages', {}).get('file_count', 0) > 100:
        health['status'] = 'warning'
        health['message'] = 'Large queue of unprocessed messages'
    
    return health


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
