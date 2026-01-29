"""
WhatsApp Webhook Receiver for Evolution API

Handles incoming webhooks from Evolution API and processes fuel reports.
Integrates with existing processor.py validation logic.

Webhook URL: http://your-server:8000/webhook/evolution

Features:
- Real-time fuel report processing via webhooks
- Message deduplication (check if already processed)
- Fallback storage (DB → Sheets → Local JSON)
- Admin command processing
"""

import json
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List
from contextlib import asynccontextmanager

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

# Import Evolution API client
try:
    from .evolution_api import (
        EvolutionAPI, get_evolution_client, parse_webhook_event,
        is_fuel_report, is_admin_command, extract_message_text
    )
    from .processor import (
        FuelReportParser, normalize_plate, is_allowed_plate,
        check_car_cooldown, check_car_cooldown_with_message, update_car_last_update,
        save_validation_error, save_pending_approval, get_pending_approvals,
        approve_pending, reject_pending, ALLOWED_PLATES, safe_json_load, safe_json_save,
        CAR_LAST_UPDATE_PATH
    )
    from .db import Database
    from .google_sheets_uploader import GoogleSheetsUploader
    from .env import load_env, get_env
    from .message_sync import (
        MessageSyncManager, save_record_with_fallback,
        load_records_with_fallback, save_shutdown_timestamp
    )
except ImportError:
    from evolution_api import (
        EvolutionAPI, get_evolution_client, parse_webhook_event,
        is_fuel_report, is_admin_command, extract_message_text
    )
    from processor import (
        FuelReportParser, normalize_plate, is_allowed_plate,
        check_car_cooldown, check_car_cooldown_with_message, update_car_last_update,
        save_validation_error, save_pending_approval, get_pending_approvals,
        approve_pending, reject_pending, ALLOWED_PLATES, safe_json_load, safe_json_save,
        CAR_LAST_UPDATE_PATH
    )
    from db import Database
    from google_sheets_uploader import GoogleSheetsUploader
    from env import load_env, get_env
    from message_sync import (
        MessageSyncManager, save_record_with_fallback,
        load_records_with_fallback, save_shutdown_timestamp
    )

# Setup logging
logger = logging.getLogger(__name__)

# Paths
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / 'data'
CONFIG_PATH = ROOT_DIR / 'config.json'

# Create router for webhook endpoints
router = APIRouter(prefix="/webhook", tags=["webhook"])

# Global Evolution API client
_evo_client: Optional[EvolutionAPI] = None

# Message sync manager for deduplication
_sync_manager: Optional[MessageSyncManager] = None


def get_evo_client() -> EvolutionAPI:
    """Get or create Evolution API client."""
    global _evo_client
    if _evo_client is None:
        _evo_client = EvolutionAPI()
    return _evo_client


def get_sync_manager() -> MessageSyncManager:
    """Get or create message sync manager."""
    global _sync_manager
    if _sync_manager is None:
        _sync_manager = MessageSyncManager()
    return _sync_manager


def load_config() -> Dict:
    """Load configuration."""
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return {}


async def send_whatsapp_message(
    to: str,
    text: str,
    mentions: Optional[List[str]] = None
) -> bool:
    """Send a message via Evolution API."""
    try:
        client = get_evo_client()
        result = await client.send_text_message_async(to, text, mentions=mentions)
        return "error" not in result
    except Exception as e:
        logger.error(f"Failed to send WhatsApp message: {e}")
        return False


async def get_group_admin_phones(group_jid: str) -> List[str]:
    """Get admin phone numbers for a group."""
    try:
        client = get_evo_client()
        return client.get_group_admins(group_jid)
    except Exception as e:
        logger.error(f"Failed to get group admins: {e}")
        return []


def format_datetime(timestamp: int) -> str:
    """Format Unix timestamp to YYYY-MM-DD-HH-MM."""
    try:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime('%Y-%m-%d-%H-%M')
    except:
        return datetime.now().strftime('%Y-%m-%d-%H-%M')


def format_confirmation(record: Dict, sender: str) -> str:
    """Format a confirmation message for successful fuel report."""
    msg = "[LOGGED] *FUEL REPORT LOGGED*\n\n"
    
    if record.get('department'):
        msg += f"Department: {record['department']}\n"
    if record.get('driver'):
        msg += f"Driver: {record['driver']}\n"
    if record.get('car'):
        msg += f"Vehicle: {record['car']}\n"
    if record.get('liters'):
        try:
            liters = float(str(record['liters']).replace(',', ''))
            msg += f"Fuel: {liters:.2f} L"
        except:
            msg += f"Fuel: {record['liters']} L"
        if record.get('type'):
            msg += f" ({record['type']})"
        msg += "\n"
    if record.get('amount'):
        try:
            amount = float(str(record['amount']).replace(',', ''))
            msg += f"Amount: KSH {amount:,.0f}\n"
        except:
            msg += f"Amount: KSH {record['amount']}\n"
    if record.get('odometer'):
        try:
            odo = int(float(str(record['odometer']).replace(',', '')))
            msg += f"Odometer: {odo:,} km\n"
        except:
            msg += f"Odometer: {record['odometer']} km\n"
    
    dt_str = record.get('datetime', datetime.now().strftime('%Y-%m-%d %H:%M'))
    msg += f"\n_{dt_str} | {sender}_"
    
    return msg


async def process_fuel_report(
    message_text: str,
    sender_phone: str,
    sender_name: str,
    group_jid: str,
    message_timestamp: int
) -> None:
    """
    Process a fuel report message.
    
    Validates the message, checks cooldown, and saves to database/Google Sheets.
    Sends confirmation or error message back to WhatsApp.
    """
    config = load_config()
    parser = FuelReportParser(config)
    
    # Parse the message
    parsed, errors = parser.parse(message_text)
    
    if not parsed or len(parsed) < 3:
        # Could not parse enough fields
        error_msg = "Could not extract required fields from message"
        if errors:
            error_msg = "; ".join(errors[:3])  # First 3 errors
        
        await send_whatsapp_message(
            group_jid,
            f"[ERROR] *FUEL REPORT ERROR*\n\n"
            f"@{sender_phone}\n"
            f"Issue: {error_msg}\n\n"
            f"Please check your message format.\n"
            f"Type *!how* for guidance.",
            mentions=[sender_phone]
        )
        return
    
    # Build record
    record = {
        'datetime': format_datetime(message_timestamp),
        'department': parsed.get('department', ''),
        'driver': parsed.get('driver', ''),
        'car': parsed.get('car', ''),
        'liters': parsed.get('liters', ''),
        'amount': parsed.get('amount', ''),
        'type': parsed.get('type', ''),
        'odometer': parsed.get('odometer', ''),
        'sender': sender_name,
        'sender_phone': sender_phone,
        'raw_message': message_text,
    }
    
    # Validate required fields
    required_fields = {
        'department': 'DEPARTMENT',
        'driver': 'DRIVER',
        'car': 'CAR/VEHICLE',
        'liters': 'LITERS',
        'amount': 'AMOUNT',
        'type': 'TYPE (DIESEL/PETROL)',
        'odometer': 'ODOMETER',
    }
    
    missing_fields = []
    for field, label in required_fields.items():
        value = record.get(field)
        if not value or (isinstance(value, str) and not value.strip()):
            missing_fields.append(label)
    
    if missing_fields:
        await send_whatsapp_message(
            group_jid,
            f"[ERROR] *MISSING REQUIRED FIELDS*\n\n"
            f"@{sender_phone}\n"
            f"Missing: {', '.join(missing_fields)}\n\n"
            f"All 7 fields are required.\n"
            f"Type *!how* for guidance.",
            mentions=[sender_phone]
        )
        return
    
    # Normalize car plate
    normalized_plate = normalize_plate(record['car'])
    
    # Validate against fleet whitelist
    if normalized_plate not in ALLOWED_PLATES:
        await send_whatsapp_message(
            group_jid,
            f"[ERROR] *VEHICLE NOT IN FLEET*\n\n"
            f"@{sender_phone}\n"
            f"Vehicle {record['car']} is not in the approved fleet list.\n"
            f"Please check the registration number.",
            mentions=[sender_phone]
        )
        return
    
    # Update record with normalized plate
    record['car'] = normalized_plate
    
    # Check 12-hour car cooldown
    cooldown_ok, cooldown_approval_id, cooldown_message = check_car_cooldown_with_message(normalized_plate, record)
    
    if not cooldown_ok and cooldown_message:
        # Cooldown violation - send notification directly via Evolution API
        admin_phones = await get_group_admin_phones(group_jid)
        
        # Build admin mentions
        admin_mentions = [phone for phone in admin_phones if phone]
        admin_tags = " ".join([f"@{phone}" for phone in admin_mentions[:3]])  # Max 3 admins
        
        # Send cooldown violation message with admin mentions
        notification_msg = f"{admin_tags}\n\n{cooldown_message}" if admin_tags else cooldown_message
        
        await send_whatsapp_message(
            group_jid,
            notification_msg,
            mentions=admin_mentions[:3]
        )
        
        logger.info(f"[PENDING] Car cooldown violation for {normalized_plate} - Approval ID: {cooldown_approval_id} - Notification sent")
        return
    elif not cooldown_ok:
        # Cooldown failed but no message (shouldn't happen)
        logger.warning(f"[PENDING] Car cooldown violation for {normalized_plate} - No message generated")
        return
    
    # Validate odometer (must be greater than previous)
    last_update = safe_json_load(CAR_LAST_UPDATE_PATH, {}).get(normalized_plate, {})
    if last_update:
        try:
            last_odo = int(float(str(last_update.get('odometer', 0)).replace(',', '')))
            new_odo = int(float(str(record.get('odometer', 0)).replace(',', '')))
            
            if new_odo <= last_odo and new_odo > 0 and last_odo > 0:
                await send_whatsapp_message(
                    group_jid,
                    f"[ERROR] *ODOMETER ERROR*\n\n"
                    f"@{sender_phone}\n"
                    f"New reading ({new_odo:,} km) must be greater than previous ({last_odo:,} km).\n"
                    f"Please verify and resend.",
                    mentions=[sender_phone]
                )
                return
        except:
            pass
    
    # All validations passed - save to database and Google Sheets
    # Using fallback logic: DB → Sheets → Local JSON
    success = False
    error_msg = None
    saved_destinations = []
    
    # Use fallback save mechanism
    try:
        success, destinations = await save_record_with_fallback(record)
        saved_destinations = destinations.split(", ")
        if not success:
            error_msg = "All save methods failed"
        else:
            logger.info(f"[SAVE] Saved record for {normalized_plate} to: {destinations}")
    except Exception as e:
        logger.error(f"[SAVE] Error in fallback save: {e}")
        error_msg = str(e)
        
        # Try individual save methods as ultimate fallback
        # Save to PostgreSQL database
        try:
            upload_config = config.get('upload', {})
            if upload_config.get('toDatabase', True):
                table_name = upload_config.get('database', {}).get('tableName', 'fuel_records')
                db = Database(table_name=table_name)
                if db.insert_fuel_record(record):
                    logger.info(f"[DB] Inserted record for {normalized_plate}")
                    success = True
                    saved_destinations.append("Database")
                else:
                    logger.error(f"[DB] Failed to insert record for {normalized_plate}")
        except Exception as e:
            logger.error(f"[DB] Database error: {e}")
            error_msg = str(e)
        
        # Save to Google Sheets
        try:
            upload_config = config.get('upload', {})
            if upload_config.get('toGoogleSheets', True):
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
                    logger.info(f"[SHEETS] Uploaded record for {normalized_plate}")
                    success = True
                    saved_destinations.append("Google Sheets")
        except Exception as e:
            logger.error(f"[SHEETS] Google Sheets error: {e}")
            if not error_msg:
                error_msg = str(e)
    
    if success:
        # Update car last update for cooldown tracking
        update_car_last_update(normalized_plate, record)
        
        # Send confirmation
        confirmation_msg = format_confirmation(record, sender_name)
        await send_whatsapp_message(group_jid, confirmation_msg)
        logger.info(f"[OK] Processed fuel report for {normalized_plate} from {sender_name}")
    else:
        # Send error notification
        await send_whatsapp_message(
            group_jid,
            f"[ERROR] *FAILED TO SAVE RECORD*\n\n"
            f"@{sender_phone}\n"
            f"Error: {error_msg or 'Unknown error'}\n"
            f"Please try again or contact admin.",
            mentions=[sender_phone]
        )


async def process_admin_command(
    command_text: str,
    sender_phone: str,
    sender_name: str,
    group_jid: str,
    is_admin: bool
) -> None:
    """Process admin commands (!status, !approve, !reject, etc.)"""
    
    text = command_text.strip().lower()
    parts = text.split()
    command = parts[0]
    
    # Public commands (available to everyone)
    if command == '!how':
        guide = get_fuel_update_guide()
        await send_whatsapp_message(group_jid, guide)
        return
    
    # Admin-only commands
    if not is_admin:
        await send_whatsapp_message(
            group_jid,
            "[DENIED] *Access Denied*\n\nOnly group admins can use admin commands."
        )
        return
    
    response = ""
    
    if command == '!status':
        response = await get_system_status()
    
    elif command == '!pending':
        response = get_pending_approvals_message()
    
    elif command == '!approve':
        if len(parts) < 2:
            response = "[USAGE] Usage: !approve <ID>\n\nUse !pending to see pending approvals."
        else:
            approval_id = parts[1]
            success, msg, record = approve_pending(approval_id)
            if success and record:
                # Process the approved record
                await process_approved_record(record, group_jid)
                response = f"[APPROVED] Approved: *{approval_id}*\n\nThe record has been processed."
            else:
                response = f"[ERROR] {msg}"
    
    elif command == '!reject':
        if len(parts) < 2:
            response = "[USAGE] Usage: !reject <ID>\n\nUse !pending to see pending approvals."
        else:
            approval_id = parts[1]
            success, msg = reject_pending(approval_id)
            response = f"[REJECTED] {msg}" if success else f"[ERROR] {msg}"
    
    elif command == '!list':
        response = get_fleet_list_message()
    
    elif command == '!add':
        if len(parts) < 2:
            response = "[USAGE] Usage: !add KXX 123Y"
        else:
            plate = ''.join(parts[1:]).upper()
            response = add_vehicle_to_fleet(plate)
    
    elif command == '!remove':
        if len(parts) < 2:
            response = "[USAGE] Usage: !remove KXX123Y"
        else:
            plate = ''.join(parts[1:]).upper()
            response = remove_vehicle_from_fleet(plate)
    
    elif command == '!help':
        response = get_admin_help_message()
    
    else:
        return  # Unknown command, ignore
    
    if response:
        await send_whatsapp_message(group_jid, response)


async def process_approved_record(record: Dict, group_jid: str) -> None:
    """Process an approved fuel record (save to DB and Sheets)."""
    config = load_config()
    normalized_plate = normalize_plate(record.get('car', ''))
    
    # Save to database
    try:
        upload_config = config.get('upload', {})
        if upload_config.get('toDatabase', True):
            table_name = upload_config.get('database', {}).get('tableName', 'fuel_records')
            db = Database(table_name=table_name)
            db.insert_fuel_record(record)
            logger.info(f"[DB] Inserted approved record for {normalized_plate}")
    except Exception as e:
        logger.error(f"[DB] Error saving approved record: {e}")
    
    # Save to Google Sheets
    try:
        upload_config = config.get('upload', {})
        if upload_config.get('toGoogleSheets', True):
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
                logger.info(f"[SHEETS] Uploaded approved record for {normalized_plate}")
    except Exception as e:
        logger.error(f"[SHEETS] Error uploading approved record: {e}")
    
    # Update car last update
    update_car_last_update(normalized_plate, record)


# ==================== Helper Functions ====================

def get_fuel_update_guide() -> str:
    """Get the fuel update guide message."""
    guide = "[GUIDE] *HOW TO SEND A FUEL UPDATE*\n"
    guide += "------------------------------------\n\n"
    guide += "Your message *MUST* start with:\n"
    guide += "*FUEL UPDATE*\n\n"
    guide += "Then include *ALL* these fields:\n\n"
    guide += "[1] *DEPARTMENT:* Your department\n"
    guide += "   _(e.g., LOGISTICS, SALES, OPERATIONS)_\n\n"
    guide += "[2] *DRIVER:* Your name\n"
    guide += "   _(e.g., John Kamau)_\n\n"
    guide += "[3] *CAR:* Vehicle registration plate\n"
    guide += "   _(e.g., KCA 542Q)_\n\n"
    guide += "[4] *LITERS:* Fuel amount in liters\n"
    guide += "   _(e.g., 45.5)_\n\n"
    guide += "[5] *AMOUNT:* Cost in KSH\n"
    guide += "   _(e.g., 7,500)_\n\n"
    guide += "[6] *TYPE:* Fuel type\n"
    guide += "   _(DIESEL, PETROL, SUPER, V-POWER, or UNLEADED)_\n\n"
    guide += "[7] *ODOMETER:* Current odometer reading\n"
    guide += "   _(e.g., 125,430)_\n\n"
    guide += "------------------------------------\n"
    guide += "[OK] *EXAMPLE MESSAGE:*\n"
    guide += "------------------------------------\n\n"
    guide += "FUEL UPDATE\n"
    guide += "DEPARTMENT: LOGISTICS\n"
    guide += "DRIVER: John Kamau\n"
    guide += "CAR: KCA 542Q\n"
    guide += "LITERS: 45.5\n"
    guide += "AMOUNT: 7,500\n"
    guide += "TYPE: DIESEL\n"
    guide += "ODOMETER: 125,430\n\n"
    guide += "_Type !how anytime to see this guide again._"
    return guide


async def get_system_status() -> str:
    """Get system status message."""
    client = get_evo_client()
    health = await client.health_check_async()
    
    status = "[STATUS] *SYSTEM STATUS*\n"
    status += "----------------------------\n\n"
    
    # Evolution API status
    if health.get('status') == 'healthy':
        status += "[OK] *Evolution API:* Connected\n"
        instance_status = health.get('instance_status', {})
        state = instance_status.get('state', 'unknown')
        status += f"[WHATSAPP] *WhatsApp:* {state.upper()}\n"
    else:
        status += "[ERROR] *Evolution API:* Disconnected\n"
        status += f"Error: {health.get('error', 'Unknown')}\n"
    
    # Pending approvals
    approvals = get_pending_approvals()
    status += f"\n[PENDING] *Pending Approvals:* {len(approvals)}\n"
    
    # Database status
    try:
        db = Database()
        count = db.get_record_count()
        status += f"[DB] *Database Records:* {count:,}\n"
    except:
        status += "[DB] *Database:* Not connected\n"
    
    status += f"\n[TIME] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    return status


def get_pending_approvals_message() -> str:
    """Get pending approvals formatted message."""
    approvals = get_pending_approvals()
    
    if not approvals:
        return "[OK] No pending approvals"
    
    msg = f"[PENDING] *PENDING APPROVALS* ({len(approvals)})\n"
    msg += "----------------------------\n\n"
    
    for approval in approvals[:10]:  # Limit to 10
        record = approval.get('record', {})
        msg += f"*ID:* {approval.get('id')}\n"
        msg += f"*Type:* {approval.get('type')}\n"
        msg += f"*Car:* {record.get('car', 'N/A')}\n"
        msg += f"*Driver:* {record.get('driver', 'N/A')}\n"
        msg += f"*Reason:* {approval.get('reason', 'N/A')}\n"
        msg += f"\n_!approve {approval.get('id')} or !reject {approval.get('id')}_\n"
        msg += "───────────────────────\n"
    
    return msg


def get_fleet_list_message() -> str:
    """Get fleet list formatted message."""
    plates = sorted(ALLOWED_PLATES)
    
    msg = f"[FLEET] *FLEET VEHICLES* ({len(plates)})\n"
    msg += "----------------------------\n\n"
    
    # Group in rows of 3
    for i in range(0, len(plates), 3):
        row = plates[i:i+3]
        msg += "  •  ".join(row) + "\n"
    
    return msg


def get_admin_help_message() -> str:
    """Get admin help message."""
    msg = "[HELP] *ADMIN COMMANDS*\n"
    msg += "----------------------------\n\n"
    msg += "*!status* - System health check\n"
    msg += "*!pending* - View pending approvals\n"
    msg += "*!approve ID* - Approve pending record\n"
    msg += "*!reject ID* - Reject pending record\n"
    msg += "*!add KXX123Y* - Add vehicle to fleet\n"
    msg += "*!remove KXX123Y* - Remove vehicle\n"
    msg += "*!list* - List all fleet vehicles\n"
    msg += "*!help* - Show this help\n\n"
    msg += "_Only group admins can use these commands._\n\n"
    msg += "----------------------------\n"
    msg += "[PUBLIC] *PUBLIC COMMANDS*\n"
    msg += "----------------------------\n\n"
    msg += "*!how* - Guide on sending fuel updates\n"
    msg += "_Available to everyone._"
    return msg


def add_vehicle_to_fleet(plate: str) -> str:
    """Add a vehicle to the fleet (modifies processor.py)."""
    normalized = normalize_plate(plate)
    
    if normalized in ALLOWED_PLATES:
        return f"[WARN] Vehicle *{normalized}* is already in the fleet list"
    
    # Add to in-memory set
    ALLOWED_PLATES.add(normalized)
    
    # Update processor.py file
    try:
        processor_path = Path(__file__).parent / 'processor.py'
        content = processor_path.read_text()
        
        import re
        match = re.search(r"ALLOWED_PLATES\s*=\s*\{([^}]+)\}", content, re.DOTALL)
        if match:
            existing = match.group(1).strip()
            new_plates = existing + f", '{normalized}'"
            content = content.replace(match.group(0), f"ALLOWED_PLATES = {{{new_plates}}}")
            processor_path.write_text(content)
            return f"[ADDED] Vehicle *{normalized}* added to fleet list"
    except Exception as e:
        logger.error(f"Error updating processor.py: {e}")
        return f"[ERROR] Added to memory but failed to persist: {e}"
    
    return f"[ERROR] Could not find ALLOWED_PLATES in processor.py"


def remove_vehicle_from_fleet(plate: str) -> str:
    """Remove a vehicle from the fleet."""
    normalized = normalize_plate(plate)
    
    if normalized not in ALLOWED_PLATES:
        return f"[WARN] Vehicle *{normalized}* is not in the fleet list"
    
    # Remove from in-memory set
    ALLOWED_PLATES.discard(normalized)
    
    # Update processor.py file
    try:
        processor_path = Path(__file__).parent / 'processor.py'
        content = processor_path.read_text()
        
        import re
        # Remove the plate from the set
        patterns = [
            f"'{normalized}',\\s*",
            f",\\s*'{normalized}'",
            f"'{normalized}'"
        ]
        for pattern in patterns:
            content = re.sub(pattern, '', content)
        
        processor_path.write_text(content)
        return f"[REMOVED] Vehicle *{normalized}* removed from fleet list"
    except Exception as e:
        logger.error(f"Error updating processor.py: {e}")
        return f"[ERROR] Removed from memory but failed to persist: {e}"


# ==================== Webhook Endpoint ====================

@router.post("/evolution")
async def evolution_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive webhook events from Evolution API.
    
    Configure Evolution API to send webhooks to:
    http://your-server:8000/webhook/evolution
    
    Features:
    - Message deduplication (prevents reprocessing)
    - Background processing for long operations
    - Admin command handling
    - Fuel report validation and storage
    """
    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"Invalid webhook payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    
    # Parse the event
    event = parse_webhook_event(payload)
    event_type = event.get('event_type')
    
    logger.info(f"[WEBHOOK] Received event: {event_type}")
    
    # Handle message events
    if event_type == "messages.upsert":
        data = event.get('data', {})
        
        # Skip messages we sent
        if data.get('from_me'):
            return {"status": "ok", "action": "skipped_own_message"}
        
        # Only process group messages
        if not data.get('is_group'):
            return {"status": "ok", "action": "skipped_non_group"}
        
        # Get message ID for deduplication
        message_id = data.get('message_id', '')
        
        # Check if message was already processed (deduplication)
        if message_id:
            sync_manager = get_sync_manager()
            if sync_manager.is_message_processed(message_id):
                logger.info(f"[WEBHOOK] Message {message_id} already processed - skipping")
                return {"status": "ok", "action": "already_processed"}
        
        message_text = data.get('text', '')
        remote_jid = data.get('remote_jid', '')
        participant = data.get('participant', '')  # Sender in group
        push_name = data.get('push_name', 'Unknown')
        timestamp = data.get('timestamp', int(datetime.now().timestamp()))
        
        # Extract sender phone from participant JID
        sender_phone = participant.split('@')[0] if '@' in participant else ''
        
        # Check if it's an admin command
        if is_admin_command(message_text):
            # Check if sender is admin
            client = get_evo_client()
            admin_phones = client.get_group_admins(remote_jid)
            is_admin = sender_phone in admin_phones
            
            # Mark message as processed
            if message_id:
                sync_manager = get_sync_manager()
                sync_manager.mark_message_processed(message_id, {
                    "type": "admin_command",
                    "command": message_text[:100],
                    "sender": push_name,
                    "processed_at": datetime.now().isoformat()
                }, folder='raw')
            
            # Process command in background
            background_tasks.add_task(
                process_admin_command,
                message_text,
                sender_phone,
                push_name,
                remote_jid,
                is_admin
            )
            return {"status": "ok", "action": "processing_command"}
        
        # Check if it's a fuel report
        if is_fuel_report(message_text):
            # Mark message as processing
            if message_id:
                sync_manager = get_sync_manager()
                sync_manager.mark_message_processed(message_id, {
                    "type": "fuel_report",
                    "sender": push_name,
                    "received_at": datetime.now().isoformat()
                }, folder='raw')
            
            # Process fuel report in background
            background_tasks.add_task(
                process_fuel_report,
                message_text,
                sender_phone,
                push_name,
                remote_jid,
                timestamp
            )
            return {"status": "ok", "action": "processing_fuel_report"}
        
        # Not a fuel report or command - still track to avoid reprocessing
        if message_id:
            sync_manager = get_sync_manager()
            sync_manager.mark_message_processed(message_id, {
                "type": "other",
                "processed_at": datetime.now().isoformat()
            }, folder='raw')
        
        return {"status": "ok", "action": "ignored_message"}
    
    # Handle connection updates
    elif event_type == "connection.update":
        data = event.get('data', {})
        state = data.get('state', '')
        logger.info(f"[CONNECTION] WhatsApp connection state: {state}")
        return {"status": "ok", "connection_state": state}
    
    # Handle QR code updates
    elif event_type == "qrcode.updated":
        logger.info("[QRCODE] QR code updated - scan required")
        return {"status": "ok", "action": "qrcode_updated"}
    
    # Other events
    return {"status": "ok", "event": event_type}


@router.get("/evolution/health")
async def webhook_health():
    """Health check for webhook receiver."""
    client = get_evo_client()
    health = await client.health_check_async()
    
    # Get sync status
    sync_manager = get_sync_manager()
    last_sync = sync_manager.get_last_processed_time()
    elapsed = sync_manager.get_time_since_last_sync()
    
    return {
        "webhook_receiver": "healthy",
        "evolution_api": health,
        "message_sync": {
            "last_sync": last_sync.isoformat() if last_sync else None,
            "hours_since_sync": round(elapsed.total_seconds() / 3600, 2) if elapsed else None,
            "processed_ids_count": len(sync_manager.processed_message_ids)
        }
    }


@router.post("/evolution/sync")
async def trigger_message_sync(background_tasks: BackgroundTasks):
    """
    Manually trigger message sync to fetch missed messages.
    
    This endpoint allows manually triggering the offline message sync,
    useful for testing or when you want to ensure all messages are processed.
    """
    try:
        from python.message_sync import fetch_missed_messages
        
        config = load_config()
        group_jid = config.get('whatsapp', {}).get('groupJid')
        
        if not group_jid:
            return {"status": "error", "message": "No target group configured"}
        
        client = get_evo_client()
        
        # Check if connected
        status = await client.get_instance_status_async()
        if not status:
            return {"status": "error", "message": "Evolution API instance not connected"}
        
        state = status.get('state', status.get('instance', {}).get('state', ''))
        if state != 'open':
            return {"status": "error", "message": f"WhatsApp not connected (state: {state})"}
        
        # Define callback for processing
        async def process_callback(event_data, is_history_sync=False):
            from python.webhook_receiver import process_fuel_report
            data = event_data.get('data', {})
            text = extract_message_text(data.get('message', {}))
            key = data.get('key', {})
            participant = key.get('participant', '')
            sender_phone = participant.split('@')[0] if '@' in participant else ''
            push_name = data.get('pushName', 'Unknown')
            timestamp = data.get('messageTimestamp', int(datetime.now().timestamp()))
            
            await process_fuel_report(
                text,
                sender_phone,
                push_name,
                key.get('remoteJid', group_jid),
                timestamp
            )
        
        # Fetch and process missed messages
        stats = await fetch_missed_messages(
            evolution_api=client,
            group_jid=group_jid,
            process_callback=process_callback,
            max_messages=50,
            max_offline_hours=24
        )
        
        return {
            "status": "ok",
            "message": "Message sync completed",
            "stats": stats
        }
        
    except Exception as e:
        logger.error(f"Error in manual message sync: {e}")
        return {"status": "error", "message": str(e)}
