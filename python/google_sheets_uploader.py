from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import gspread
from google.oauth2.service_account import Credentials

try:
    from .env import get_env
except ImportError:
    from env import get_env

# Project root directory (parent of python/)
ROOT_DIR = Path(__file__).parent.parent

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# Default credential file names to look for in project root
DEFAULT_CREDS_FILES = ['google_credentials.json', 'credentials.json', 'service_account.json']


def _find_credentials_file() -> Optional[Path]:
    """Find credentials file in project root, checking common names."""
    # First check environment variable
    creds_file = get_env('GOOGLE_SERVICE_ACCOUNT_FILE')
    if creds_file:
        creds_path = Path(creds_file)
        if creds_path.exists():
            return creds_path
    
    # Fallback: look in project root for common credential file names
    for filename in DEFAULT_CREDS_FILES:
        creds_path = ROOT_DIR / filename
        if creds_path.exists():
            return creds_path
    
    return None


class GoogleSheetsUploader:
    def __init__(self, spreadsheet_id: Optional[str] = None, spreadsheet_name: Optional[str] = None, worksheet_name: str = 'FUEL RECORDS'):
        creds_file = _find_credentials_file()
        creds_json = get_env('GOOGLE_SERVICE_ACCOUNT_JSON')

        if creds_file:
            credentials = Credentials.from_service_account_file(str(creds_file), scopes=SCOPES)
        elif creds_json:
            import json
            info = json.loads(creds_json)
            credentials = Credentials.from_service_account_info(info, scopes=SCOPES)
        else:
            raise ValueError(
                'Google credentials not found. Place google_credentials.json in project root, '
                'or set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON environment variable.'
            )

        self.client = gspread.authorize(credentials)

        # Open or create spreadsheet
        if spreadsheet_id:
            self.spreadsheet = self.client.open_by_key(spreadsheet_id)
        elif spreadsheet_name:
            try:
                self.spreadsheet = self.client.open(spreadsheet_name)
            except gspread.SpreadsheetNotFound:
                self.spreadsheet = self.client.create(spreadsheet_name)
        else:
            raise ValueError('Provide spreadsheet_id or spreadsheet_name')

        # Ensure worksheet exists
        self.worksheet = self._get_or_create_worksheet(worksheet_name)

    def _get_or_create_worksheet(self, name: str):
        try:
            return self.spreadsheet.worksheet(name)
        except gspread.WorksheetNotFound:
            return self.spreadsheet.add_worksheet(title=name, rows=1000, cols=20)

    def ensure_headers(self, columns: List[str]):
        values = self.worksheet.get('A1:Z1')
        existing = values[0] if values else []
        if [c.upper() for c in existing] != [c.upper() for c in columns]:
            self.worksheet.update('A1', [columns])

    def append_record(self, record: Dict, columns: List[str]):
        # Map record to column order
        row = []
        for col in columns:
            key = col.lower()
            row.append(record.get(key, ''))
        self.worksheet.append_row(row, value_input_option='USER_ENTERED')

    def update_record(self, original_datetime: str, original_car: str, new_record: Dict, columns: List[str]) -> bool:
        """Find and update an existing row by datetime (col A) and car (col D).
        
        Returns True if record was found and updated, False otherwise.
        """
        try:
            # Get all data
            all_values = self.worksheet.get_all_values()
            if len(all_values) <= 1:
                return False  # Only header or empty
            
            # Find the row (datetime is col 0, car is col 3)
            target_row = None
            for idx, row in enumerate(all_values[1:], start=2):  # Start at 2 (row 1 is header)
                if len(row) >= 4:
                    row_datetime = row[0]
                    row_car = row[3].upper().replace(' ', '')
                    if row_datetime == original_datetime and row_car == original_car.upper().replace(' ', ''):
                        target_row = idx
                        break
            
            if not target_row:
                return False
            
            # Build new row data
            new_row = []
            for col in columns:
                key = col.lower()
                new_row.append(new_record.get(key, ''))
            
            # Update the row
            cell_range = f'A{target_row}:{chr(65 + len(columns) - 1)}{target_row}'
            self.worksheet.update(cell_range, [new_row], value_input_option='USER_ENTERED')
            return True
        except Exception:
            return False

    def get_all_records(self) -> List[Dict]:
        """Fetch all records from the worksheet as a list of dictionaries."""
        try:
            all_values = self.worksheet.get_all_values()
            if len(all_values) <= 1:
                return []  # Only header or empty
            
            # First row is header
            headers = [h.lower() for h in all_values[0]]
            
            records = []
            for row in all_values[1:]:
                if not any(row):  # Skip empty rows
                    continue
                
                record = {}
                for idx, header in enumerate(headers):
                    if idx < len(row):
                        record[header] = row[idx]
                    else:
                        record[header] = ''
                records.append(record)
            
            return records
        except Exception as e:
            print(f"Error fetching records from Google Sheets: {e}")
            return []

    def get_record_count(self) -> int:
        """Get total number of records (excluding header)."""
        try:
            all_values = self.worksheet.get_all_values()
            return max(0, len(all_values) - 1)
        except Exception:
            return 0
