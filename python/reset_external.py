#!/usr/bin/env python3
"""
Reset External Data Sources

This script clears data from:
- MySQL database (fuel_records table)
- Google Sheets (SYSTEM FUEL RECORDS worksheet)

Run this during development to get a clean slate.
"""

import sys
import os
import io

# Fix Windows console encoding for emoji output
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path
from env import load_env
from db import Database
from google_sheets_uploader import GoogleSheetsUploader
import json

# Load environment variables
ROOT_DIR = Path(__file__).parent.parent
load_env(ROOT_DIR / '.env')


def reset_database():
    """Clear all records from the fuel_records table."""
    print("  Resetting database...")
    
    try:
        db = Database()
        if not db.engine:
            print("    [WARN] Database not configured, skipping...")
            return False
        
        # Load config to get table name
        config_path = ROOT_DIR / 'config.json'
        table_name = 'fuel_records'
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                table_name = config.get('upload', {}).get('database', {}).get('tableName', 'fuel_records')
            except:
                pass
        
        # Delete all records from the table
        from sqlalchemy import text
        with db.engine.connect() as conn:
            result = conn.execute(text(f"DELETE FROM {table_name}"))
            conn.commit()
            print(f"    [OK] Deleted {result.rowcount} records from {table_name}")
        
        return True
    except Exception as e:
        print(f"    [ERROR] Database reset failed: {e}")
        return False


def reset_google_sheets():
    """Clear all data from the Google Sheet (keep header)."""
    print("  Resetting Google Sheet...")
    
    try:
        # Load config to get sheet name and spreadsheet ID
        config_path = ROOT_DIR / 'config.json'
        sheet_name = 'SYSTEM FUEL RECORDS'
        spreadsheet_id = None
        
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                sheet_name = config.get('upload', {}).get('google', {}).get('sheetName', 'SYSTEM FUEL RECORDS')
                spreadsheet_id = config.get('upload', {}).get('google', {}).get('spreadsheetId')
            except:
                pass
        
        # Also check environment variable
        if not spreadsheet_id:
            spreadsheet_id = os.environ.get('GOOGLE_SHEETS_SPREADSHEET_ID')
        
        if not spreadsheet_id:
            print("    [WARN] No spreadsheet ID found in config or environment, skipping...")
            return False
        
        uploader = GoogleSheetsUploader(spreadsheet_id=spreadsheet_id, worksheet_name=sheet_name)
        if not uploader.worksheet:
            print("    [WARN] Google Sheets not configured, skipping...")
            return False
        
        # Get current row count
        all_values = uploader.worksheet.get_all_values()
        data_rows = len(all_values) - 1 if len(all_values) > 1 else 0
        
        if data_rows > 0:
            # Clear all rows except header (row 1)
            # Get the range to clear: from row 2 to last row
            uploader.worksheet.batch_clear([f"A2:Z{len(all_values) + 100}"])
            print(f"    [OK] Cleared {data_rows} rows from '{sheet_name}'")
        else:
            print(f"    [OK] Sheet '{sheet_name}' already empty")
        
        return True
    except Exception as e:
        print(f"    [ERROR] Google Sheet reset failed: {e}")
        return False


def reset_car_summary():
    """Reset the car_summary.json file."""
    print("  Resetting car summary...")
    summary_path = ROOT_DIR / 'data' / 'car_summary.json'
    try:
        with open(summary_path, 'w') as f:
            json.dump({}, f, indent=2)
        print("    [OK] Cleared car_summary.json")
        return True
    except Exception as e:
        print(f"    [ERROR] Failed to reset car_summary.json: {e}")
        return False


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Reset external data sources')
    parser.add_argument('--database-only', action='store_true', 
                        help='Reset only the database records')
    parser.add_argument('--sheets-only', action='store_true', 
                        help='Reset only Google Sheets data')
    parser.add_argument('--summary-only', action='store_true',
                        help='Reset only car summary file')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Suppress banner output')
    
    args = parser.parse_args()
    
    # Determine what to reset
    reset_db = args.database_only or (not args.database_only and not args.sheets_only and not args.summary_only)
    reset_sheets = args.sheets_only or (not args.database_only and not args.sheets_only and not args.summary_only)
    reset_summary = args.summary_only or (not args.database_only and not args.sheets_only and not args.summary_only)
    
    if not args.quiet:
        print("")
        print("=" * 60)
        print("  Resetting External Data Sources")
        print("=" * 60)
        print("")
    
    results = {}
    
    if reset_db:
        results['database'] = reset_database()
    
    if reset_sheets:
        results['sheets'] = reset_google_sheets()
    
    if reset_summary:
        results['summary'] = reset_car_summary()
    
    if not args.quiet:
        print("")
        print("=" * 60)
        if all(results.values()):
            print("  [OK] External reset complete!")
        else:
            print("  [WARN] Some resets may have failed (see above)")
        print("=" * 60)
        print("")
    
    # Return 0 if all succeeded, 1 if any failed
    return 0 if all(results.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
