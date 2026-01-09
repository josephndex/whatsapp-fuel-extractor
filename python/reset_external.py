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
            print("    ⚠️ Database not configured, skipping...")
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
            print(f"    ✅ Deleted {result.rowcount} records from {table_name}")
        
        return True
    except Exception as e:
        print(f"    ❌ Database reset failed: {e}")
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
            print("    ⚠️ No spreadsheet ID found in config or environment, skipping...")
            return False
        
        uploader = GoogleSheetsUploader(spreadsheet_id=spreadsheet_id, worksheet_name=sheet_name)
        if not uploader.worksheet:
            print("    ⚠️ Google Sheets not configured, skipping...")
            return False
        
        # Get current row count
        all_values = uploader.worksheet.get_all_values()
        data_rows = len(all_values) - 1 if len(all_values) > 1 else 0
        
        if data_rows > 0:
            # Clear all rows except header (row 1)
            # Get the range to clear: from row 2 to last row
            uploader.worksheet.batch_clear([f"A2:Z{len(all_values) + 100}"])
            print(f"    ✅ Cleared {data_rows} rows from '{sheet_name}'")
        else:
            print(f"    ✅ Sheet '{sheet_name}' already empty")
        
        return True
    except Exception as e:
        print(f"    ❌ Google Sheet reset failed: {e}")
        return False


def reset_car_summary():
    """Reset the car_summary.json file."""
    print("  Resetting car summary...")
    summary_path = ROOT_DIR / 'data' / 'car_summary.json'
    try:
        with open(summary_path, 'w') as f:
            json.dump({}, f, indent=2)
        print("    ✅ Cleared car_summary.json")
        return True
    except Exception as e:
        print(f"    ❌ Failed to reset car_summary.json: {e}")
        return False


def main():
    print("")
    print("=" * 60)
    print("  Resetting External Data Sources")
    print("=" * 60)
    print("")
    
    db_ok = reset_database()
    sheets_ok = reset_google_sheets()
    summary_ok = reset_car_summary()
    
    print("")
    print("=" * 60)
    if db_ok and sheets_ok and summary_ok:
        print("  ✅ External reset complete!")
    else:
        print("  ⚠️ Some resets may have failed (see above)")
    print("=" * 60)
    print("")


if __name__ == '__main__':
    main()
