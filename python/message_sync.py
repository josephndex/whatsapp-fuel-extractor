#!/usr/bin/env python3
"""
Message Sync Module - Offline Message Recovery

Handles capturing and processing messages that were received while the system was offline.
Similar to the old Node.js fetchMissedMessages() functionality.

Features:
1. Track last processed timestamp (data/last_processed.json)
2. Fetch message history from Evolution API on startup
3. Deduplicate messages (check raw_messages, processed, errors folders)
4. Process missed fuel reports
"""

import json
import os
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Root directory
ROOT_DIR = Path(__file__).parent.parent

# Data paths
DATA_DIR = ROOT_DIR / 'data'
LAST_PROCESSED_FILE = DATA_DIR / 'last_processed.json'
RAW_MESSAGES_DIR = DATA_DIR / 'raw_messages'
PROCESSED_DIR = DATA_DIR / 'processed'
ERRORS_DIR = DATA_DIR / 'errors'

# Ensure directories exist
for dir_path in [DATA_DIR, RAW_MESSAGES_DIR, PROCESSED_DIR, ERRORS_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)


class MessageSyncManager:
    """
    Manages message synchronization and offline recovery.
    
    Tracks when the system was last active and fetches missed messages
    from Evolution API when coming back online.
    """
    
    def __init__(self):
        self.last_processed_file = LAST_PROCESSED_FILE
        self.processed_message_ids: set = set()
        self._load_processed_ids()
    
    def _load_processed_ids(self):
        """Load already processed message IDs from all folders."""
        self.processed_message_ids.clear()
        
        for folder in [RAW_MESSAGES_DIR, PROCESSED_DIR, ERRORS_DIR]:
            if folder.exists():
                for file_path in folder.glob('*.json'):
                    # Extract message ID from filename (format: timestamp_msgid.json)
                    filename = file_path.stem
                    if '_' in filename:
                        msg_id = filename.split('_', 1)[1]
                        self.processed_message_ids.add(msg_id)
                    else:
                        self.processed_message_ids.add(filename)
        
        logger.info(f"[SYNC] Loaded {len(self.processed_message_ids)} processed message IDs")
    
    def get_last_processed_time(self) -> Optional[datetime]:
        """
        Get the timestamp of the last processed message.
        
        Returns:
            datetime object of last processed time, or None if not available
        """
        try:
            if self.last_processed_file.exists():
                with open(self.last_processed_file, 'r') as f:
                    data = json.load(f)
                
                # Try timestamp first (Unix epoch seconds)
                if 'timestamp' in data:
                    timestamp = data['timestamp']
                    # Handle milliseconds vs seconds
                    if timestamp > 1e12:
                        timestamp = timestamp / 1000
                    return datetime.fromtimestamp(timestamp)
                
                # Fallback to datetime string
                if 'datetime' in data:
                    dt_str = data['datetime']
                    # Remove the 'Z' suffix and parse
                    dt_str = dt_str.replace('Z', '+00:00').replace('.000', '')
                    return datetime.fromisoformat(dt_str.replace('+00:00', ''))
                
        except Exception as e:
            logger.warning(f"[SYNC] Error reading last processed time: {e}")
        
        return None
    
    def update_last_processed_time(self, timestamp: Optional[datetime] = None):
        """
        Update the last processed timestamp.
        
        Args:
            timestamp: Optional datetime to set. Uses current time if not provided.
        """
        try:
            if timestamp is None:
                timestamp = datetime.now()
            
            data = {
                "timestamp": int(timestamp.timestamp()),
                "datetime": timestamp.strftime('%Y-%m-%dT%H:%M:%S.000Z')
            }
            
            with open(self.last_processed_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            logger.info(f"[SYNC] Updated last processed time: {data['datetime']}")
            
        except Exception as e:
            logger.error(f"[SYNC] Error updating last processed time: {e}")
    
    def is_message_processed(self, message_id: str) -> bool:
        """
        Check if a message has already been processed.
        
        Args:
            message_id: The unique message ID
            
        Returns:
            True if already processed, False otherwise
        """
        if message_id in self.processed_message_ids:
            return True
        
        # Also check file existence (in case IDs weren't loaded)
        for folder in [RAW_MESSAGES_DIR, PROCESSED_DIR, ERRORS_DIR]:
            for pattern in [f"*_{message_id}.json", f"{message_id}.json"]:
                if list(folder.glob(pattern)):
                    self.processed_message_ids.add(message_id)
                    return True
        
        return False
    
    def mark_message_processed(self, message_id: str, data: Dict, folder: str = 'raw'):
        """
        Mark a message as processed by saving to the appropriate folder.
        
        Args:
            message_id: The unique message ID
            data: Message data to save
            folder: 'raw', 'processed', or 'errors'
        """
        try:
            folder_map = {
                'raw': RAW_MESSAGES_DIR,
                'processed': PROCESSED_DIR,
                'errors': ERRORS_DIR
            }
            
            target_dir = folder_map.get(folder, RAW_MESSAGES_DIR)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{message_id}.json"
            
            file_path = target_dir / filename
            with open(file_path, 'w') as f:
                json.dump(data, f, indent=2)
            
            self.processed_message_ids.add(message_id)
            logger.debug(f"[SYNC] Saved message {message_id} to {folder}")
            
        except Exception as e:
            logger.error(f"[SYNC] Error saving message {message_id}: {e}")
    
    def get_time_since_last_sync(self) -> Optional[timedelta]:
        """
        Get the time elapsed since last sync.
        
        Returns:
            timedelta object or None if no last sync time
        """
        last_time = self.get_last_processed_time()
        if last_time:
            return datetime.now() - last_time
        return None
    
    def should_fetch_history(self, max_offline_hours: int = 24) -> Tuple[bool, Optional[str]]:
        """
        Determine if we should fetch message history.
        
        Args:
            max_offline_hours: Maximum hours to look back
            
        Returns:
            Tuple of (should_fetch, reason_message)
        """
        last_time = self.get_last_processed_time()
        
        if last_time is None:
            return True, "No last processed time found - will fetch recent messages"
        
        elapsed = datetime.now() - last_time
        hours_offline = elapsed.total_seconds() / 3600
        
        if hours_offline > max_offline_hours:
            return True, f"Offline for {hours_offline:.1f} hours (max: {max_offline_hours}h) - will fetch messages"
        
        if hours_offline > 0.1:  # More than 6 minutes
            return True, f"Offline for {hours_offline:.1f} hours - will fetch missed messages"
        
        return False, f"Only {hours_offline * 60:.1f} minutes since last sync - no history fetch needed"


async def fetch_missed_messages(
    evolution_api,
    group_jid: str,
    process_callback,
    max_messages: int = 50,
    max_offline_hours: int = 24
) -> Dict:
    """
    Fetch and process messages that were received while offline.
    
    Args:
        evolution_api: EvolutionAPI instance
        group_jid: The group JID to fetch messages from
        process_callback: Async callback function to process each message
        max_messages: Maximum number of messages to fetch
        max_offline_hours: Maximum hours to look back
        
    Returns:
        Dict with statistics: total_fetched, already_processed, newly_processed, errors
    """
    from python.evolution_api import is_fuel_report, is_admin_command, extract_message_text
    
    sync_manager = MessageSyncManager()
    
    stats = {
        "total_fetched": 0,
        "already_processed": 0,
        "newly_processed": 0,
        "fuel_reports": 0,
        "admin_commands": 0,
        "errors": 0,
        "skipped": 0
    }
    
    # Check if we should fetch
    should_fetch, reason = sync_manager.should_fetch_history(max_offline_hours)
    logger.info(f"[SYNC] {reason}")
    
    if not should_fetch:
        return stats
    
    last_time = sync_manager.get_last_processed_time()
    cutoff_time = None
    if last_time:
        # Look back to last processed time
        cutoff_time = last_time
    else:
        # Look back max hours
        cutoff_time = datetime.now() - timedelta(hours=max_offline_hours)
    
    logger.info(f"[SYNC] Fetching messages newer than {cutoff_time}")
    
    try:
        # Fetch messages from Evolution API
        messages = await evolution_api.fetch_messages_async(group_jid, count=max_messages)
        stats["total_fetched"] = len(messages)
        
        if not messages:
            logger.info("[SYNC] No messages fetched from Evolution API")
            sync_manager.update_last_processed_time()
            return stats
        
        logger.info(f"[SYNC] Processing {len(messages)} fetched messages...")
        
        for msg in messages:
            try:
                # Extract message details
                key = msg.get('key', {})
                message_id = key.get('id', '')
                from_me = key.get('fromMe', False)
                remote_jid = key.get('remoteJid', '')
                
                # Skip our own messages
                if from_me:
                    stats["skipped"] += 1
                    continue
                
                # Skip already processed
                if sync_manager.is_message_processed(message_id):
                    stats["already_processed"] += 1
                    continue
                
                # Check message timestamp
                msg_timestamp = msg.get('messageTimestamp', 0)
                if msg_timestamp:
                    # Handle milliseconds vs seconds
                    if msg_timestamp > 1e12:
                        msg_timestamp = msg_timestamp / 1000
                    msg_time = datetime.fromtimestamp(msg_timestamp)
                    
                    if msg_time < cutoff_time:
                        stats["skipped"] += 1
                        continue
                
                # Extract message text
                message_content = msg.get('message', {})
                text = extract_message_text(message_content)
                
                if not text:
                    stats["skipped"] += 1
                    continue
                
                # Determine message type and process
                if is_fuel_report(text):
                    stats["fuel_reports"] += 1
                    
                    # Build webhook-like event for the processor
                    event_data = {
                        "event": "messages.upsert",
                        "instance": evolution_api.instance_name,
                        "data": {
                            "key": key,
                            "pushName": msg.get('pushName', ''),
                            "message": message_content,
                            "messageTimestamp": msg_timestamp,
                            "messageType": msg.get('messageType', 'conversation')
                        },
                        "source": "history_sync"
                    }
                    
                    # Process using the callback
                    if process_callback:
                        try:
                            await process_callback(event_data, is_history_sync=True)
                            stats["newly_processed"] += 1
                        except Exception as e:
                            logger.error(f"[SYNC] Error processing fuel report: {e}")
                            stats["errors"] += 1
                            sync_manager.mark_message_processed(message_id, {
                                "error": str(e),
                                "message": msg,
                                "timestamp": datetime.now().isoformat()
                            }, folder='errors')
                    
                elif is_admin_command(text):
                    # Skip admin commands from history - they're time-sensitive
                    stats["admin_commands"] += 1
                    stats["skipped"] += 1
                
                else:
                    stats["skipped"] += 1
                
                # Mark as processed (even if skipped to avoid reprocessing)
                sync_manager.mark_message_processed(message_id, {
                    "text": text[:200] if text else "",
                    "type": "fuel_report" if is_fuel_report(text) else "other",
                    "processed_at": datetime.now().isoformat()
                }, folder='raw')
                    
            except Exception as e:
                logger.error(f"[SYNC] Error processing message: {e}")
                stats["errors"] += 1
        
        # Update last processed time
        sync_manager.update_last_processed_time()
        
        logger.info(f"[SYNC] Completed: {stats['newly_processed']} new, "
                   f"{stats['already_processed']} already processed, "
                   f"{stats['fuel_reports']} fuel reports, "
                   f"{stats['errors']} errors")
        
    except Exception as e:
        logger.error(f"[SYNC] Error fetching messages: {e}")
        stats["errors"] += 1
    
    return stats


def save_shutdown_timestamp():
    """
    Save current timestamp when shutting down.
    Called from signal handlers to record when the system went offline.
    """
    sync_manager = MessageSyncManager()
    sync_manager.update_last_processed_time()
    logger.info("[SYNC] Saved shutdown timestamp")


def get_sync_manager() -> MessageSyncManager:
    """Get a MessageSyncManager instance."""
    return MessageSyncManager()


# ==================== Database Fallback Logic ====================

def load_records_with_fallback() -> Tuple[List[Dict], str]:
    """
    Load fuel records with fallback priority:
    1. Try Google Sheets first (most reliable for shared data)
    2. Fall back to MySQL Database
    3. Fall back to local Excel/JSON files
    
    Returns:
        Tuple of (records_list, source_name)
    """
    logger.info("[FALLBACK] Loading records with fallback...")
    
    # 1. Try Google Sheets
    try:
        from python.google_sheets_uploader import GoogleSheetsUploader
        from python.env import get_env
        
        spreadsheet_id = get_env('GOOGLE_SHEETS_SPREADSHEET_ID')
        if spreadsheet_id:
            uploader = GoogleSheetsUploader(spreadsheet_id=spreadsheet_id)
            records = uploader.get_all_records()
            if records:
                logger.info(f"[FALLBACK] Loaded {len(records)} records from Google Sheets")
                return records, "Google Sheets"
    except Exception as e:
        logger.warning(f"[FALLBACK] Google Sheets failed: {e}")
    
    # 2. Try Database
    try:
        from python.db import Database
        db = Database()
        if db.engine:
            records = db.get_all_records()
            if records:
                logger.info(f"[FALLBACK] Loaded {len(records)} records from Database")
                return records, "Database"
    except Exception as e:
        logger.warning(f"[FALLBACK] Database failed: {e}")
    
    # 3. Try local Excel files in output folder
    try:
        output_dir = DATA_DIR / 'output'
        if output_dir.exists():
            import pandas as pd
            excel_files = sorted(output_dir.glob('*.xlsx'), reverse=True)
            for excel_file in excel_files:
                try:
                    df = pd.read_excel(excel_file)
                    records = df.to_dict('records')
                    if records:
                        logger.info(f"[FALLBACK] Loaded {len(records)} records from {excel_file.name}")
                        return records, f"Excel ({excel_file.name})"
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"[FALLBACK] Excel fallback failed: {e}")
    
    # 4. Try processed JSON files
    try:
        records = []
        if PROCESSED_DIR.exists():
            for json_file in sorted(PROCESSED_DIR.glob('*.json'), reverse=True)[:100]:
                try:
                    with open(json_file, 'r') as f:
                        data = json.load(f)
                        if isinstance(data, dict) and 'driver' in data:
                            records.append(data)
                except Exception:
                    continue
        
        if records:
            logger.info(f"[FALLBACK] Loaded {len(records)} records from processed JSON files")
            return records, "Processed JSON"
    except Exception as e:
        logger.warning(f"[FALLBACK] JSON fallback failed: {e}")
    
    logger.warning("[FALLBACK] All fallbacks failed - returning empty list")
    return [], "None"


async def save_record_with_fallback(record: Dict) -> Tuple[bool, str]:
    """
    Save a fuel record with fallback priority:
    1. Try MySQL Database first
    2. Fall back to Google Sheets
    3. Fall back to local JSON file
    
    Args:
        record: The fuel record to save
        
    Returns:
        Tuple of (success, destination_name)
    """
    logger.info("[FALLBACK] Saving record with fallback...")
    saved_to = []
    
    # 1. Try Database (primary)
    try:
        from python.db import Database
        db = Database()
        if db.engine:
            db.insert_fuel_record(record)
            saved_to.append("Database")
            logger.info("[FALLBACK] Saved to Database")
    except Exception as e:
        logger.warning(f"[FALLBACK] Database save failed: {e}")
    
    # 2. Try Google Sheets (backup)
    try:
        from python.google_sheets_uploader import GoogleSheetsUploader
        from python.env import get_env
        
        spreadsheet_id = get_env('GOOGLE_SHEETS_SPREADSHEET_ID')
        if spreadsheet_id:
            uploader = GoogleSheetsUploader(spreadsheet_id=spreadsheet_id)
            columns = ['DATETIME', 'DEPARTMENT', 'DRIVER', 'CAR', 'LITERS', 'AMOUNT', 'TYPE', 'ODOMETER', 'SENDER', 'RAW_MESSAGE']
            uploader.ensure_headers(columns)
            uploader.append_record(record, columns)
            saved_to.append("Google Sheets")
            logger.info("[FALLBACK] Saved to Google Sheets")
    except Exception as e:
        logger.warning(f"[FALLBACK] Google Sheets save failed: {e}")
    
    # 3. Always save to local JSON as final backup
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        msg_id = record.get('message_id', timestamp)
        filename = f"{timestamp}_{msg_id}.json"
        
        file_path = PROCESSED_DIR / filename
        with open(file_path, 'w') as f:
            json.dump(record, f, indent=2, default=str)
        saved_to.append("Local JSON")
        logger.info(f"[FALLBACK] Saved to local JSON: {filename}")
    except Exception as e:
        logger.error(f"[FALLBACK] Local JSON save failed: {e}")
    
    success = len(saved_to) > 0
    destinations = ", ".join(saved_to) if saved_to else "None"
    
    if success:
        logger.info(f"[FALLBACK] Record saved to: {destinations}")
    else:
        logger.error("[FALLBACK] All save methods failed!")
    
    return success, destinations
