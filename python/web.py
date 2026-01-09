"""
WhatsApp Fuel Extractor - Web Dashboard

A modern web interface for viewing fuel records, analytics, and managing approvals.

Features:
- Dashboard with key metrics
- Records table with search/filter
- Charts and analytics
- Pending approvals management
- Fleet management
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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
    from .env import load_env
    from .db import Database
except ImportError:
    from env import load_env
    from db import Database

# Paths
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / 'data'
CONFIG_PATH = ROOT_DIR / 'config.json'
TEMPLATES_DIR = Path(__file__).parent / 'templates'
STATIC_DIR = Path(__file__).parent / 'static'

# Load environment
load_env(ROOT_DIR / '.env')

# Create FastAPI app
app = FastAPI(
    title="Fuel Extractor Dashboard",
    description="Web dashboard for WhatsApp Fuel Extractor",
    version="1.0.0"
)

# Mount static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def load_config() -> dict:
    """Load configuration"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    return {}


def get_excel_path() -> Path:
    """Get path to Excel file"""
    config = load_config()
    folder = config.get('output', {}).get('excelFolder', './data/output')
    filename = config.get('output', {}).get('excelFileName', 'fuel_records.xlsx')
    return ROOT_DIR / folder / filename


def load_records(days: int = 30) -> List[Dict]:
    """Load fuel records from Excel"""
    records = []
    excel_path = get_excel_path()
    
    if not excel_path.exists():
        return records
    
    try:
        df = pd.read_excel(excel_path)
        
        # Convert to list of dicts
        for _, row in df.iterrows():
            record = {
                'datetime': str(row.get('DATETIME', '')),
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
        records.sort(key=lambda x: x['datetime'], reverse=True)
        
    except Exception as e:
        print(f"Error loading records: {e}")
    
    return records


def load_pending_approvals() -> List[Dict]:
    """Load pending approvals"""
    path = DATA_DIR / 'pending_approvals.json'
    if path.exists():
        try:
            with open(path, 'r') as f:
                approvals = json.load(f)
                return [a for a in approvals if a.get('status') == 'pending']
        except:
            pass
    return []


def load_fleet() -> List[str]:
    """Load fleet vehicles from processor.py"""
    # Read ALLOWED_PLATES from processor.py
    processor_path = Path(__file__).parent / 'processor.py'
    plates = []
    
    if processor_path.exists():
        try:
            with open(processor_path, 'r') as f:
                content = f.read()
                # Find ALLOWED_PLATES set
                import re
                match = re.search(r"ALLOWED_PLATES\s*=\s*\{([^}]+)\}", content)
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
            dt = datetime.fromisoformat(r['datetime'].replace('Z', '+00:00'))
            record_date = dt.date()
            
            if record_date == today:
                stats['today_records'] += 1
                stats['today_liters'] += r['liters']
                stats['today_amount'] += r['amount']
            
            if record_date >= week_ago:
                stats['week_records'] += 1
                stats['week_liters'] += r['liters']
                stats['week_amount'] += r['amount']
        except:
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
            dt = datetime.fromisoformat(r['datetime'].replace('Z', '+00:00'))
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
        except:
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
    """Approve a pending record"""
    path = DATA_DIR / 'pending_approvals.json'
    
    if not path.exists():
        raise HTTPException(status_code=404, detail="No approvals found")
    
    with open(path, 'r') as f:
        approvals = json.load(f)
    
    for approval in approvals:
        if approval.get('id') == approval_id:
            approval['status'] = 'approved'
            approval['approved_at'] = datetime.now().isoformat()
            approval['approved_via'] = 'web'
            
            with open(path, 'w') as f:
                json.dump(approvals, f, indent=2)
            
            # TODO: Trigger reprocessing of the approved record
            return JSONResponse({"status": "approved", "id": approval_id})
    
    raise HTTPException(status_code=404, detail="Approval not found")


@app.post("/approvals/{approval_id}/reject")
async def reject_record(approval_id: str):
    """Reject a pending record"""
    path = DATA_DIR / 'pending_approvals.json'
    
    if not path.exists():
        raise HTTPException(status_code=404, detail="No approvals found")
    
    with open(path, 'r') as f:
        approvals = json.load(f)
    
    for approval in approvals:
        if approval.get('id') == approval_id:
            approval['status'] = 'rejected'
            approval['rejected_at'] = datetime.now().isoformat()
            approval['rejected_via'] = 'web'
            
            with open(path, 'w') as f:
                json.dump(approvals, f, indent=2)
            
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
async def analytics_page(request: Request, days: int = 30):
    """Analytics page with more charts"""
    records = load_records()
    chart_data = get_chart_data(records, days=days)
    
    return templates.TemplateResponse("analytics.html", {
        "request": request,
        "chart_data": json.dumps(chart_data),
        "days": days,
    })


@app.get("/api/stats")
async def api_stats():
    """API endpoint for stats"""
    records = load_records()
    return get_stats(records)


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


def run_server(host: str = "0.0.0.0", port: int = 8080):
    """Run the web server"""
    import uvicorn
    
    print(f"\n🌐 Starting Fuel Extractor Dashboard...")
    print(f"   Local:   http://localhost:{port}")
    print(f"   Network: http://{host}:{port}")
    print(f"\n   Press Ctrl+C to stop\n")
    
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_server()
