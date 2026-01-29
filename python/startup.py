#!/usr/bin/env python3
"""
WhatsApp Fuel Extractor - Startup Script

Initializes all components:
1. Load environment variables
2. Test database connection
3. Test Google Sheets connection
4. Initialize Evolution API instance
5. Configure webhook URL
6. Fetch missed messages (if offline)
7. Start the FastAPI web server

Usage:
    python -m python.startup
    # or
    python python/startup.py
"""

import asyncio
import json
import os
import sys
import signal
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Load environment from .env file
from python.env import load_env, get_env

load_env(str(ROOT_DIR / '.env'))


def print_banner():
    """Print startup banner"""
    print("""
╔═══════════════════════════════════════════════════════════════╗
║           WhatsApp Fuel Extractor - Evolution API             ║
║                     Startup Initializer                       ║
╚═══════════════════════════════════════════════════════════════╝
    """)


def load_config() -> dict:
    """Load configuration from config.json"""
    config_path = ROOT_DIR / 'config.json'
    if config_path.exists():
        with open(config_path) as f:
            return json.load(f)
    return {}


def check_environment():
    """Check and display environment configuration"""
    print("\n[1/5] Checking Environment...")
    
    # Check if DATABASE_URL or individual DB params are set
    db_url = get_env('DATABASE_URL')
    db_host = get_env('DB_HOST')
    db_configured = bool(db_url) or bool(db_host and get_env('DB_NAME') and get_env('DB_USER'))
    
    required_vars = {
        'Database': 'Configured via DB_* params' if db_configured else None,
        'GOOGLE_SHEETS_SPREADSHEET_ID': get_env('GOOGLE_SHEETS_SPREADSHEET_ID'),
    }
    
    optional_vars = {
        'EVOLUTION_API_URL': get_env('EVOLUTION_API_URL'),
        'EVOLUTION_API_KEY': get_env('EVOLUTION_API_KEY'),
        'EVOLUTION_INSTANCE_NAME': get_env('EVOLUTION_INSTANCE_NAME'),
    }
    
    config = load_config()
    
    # Check Evolution config from config.json
    evo_config = config.get('evolution', {})
    if not optional_vars['EVOLUTION_API_URL']:
        optional_vars['EVOLUTION_API_URL'] = evo_config.get('apiUrl')
    if not optional_vars['EVOLUTION_API_KEY']:
        optional_vars['EVOLUTION_API_KEY'] = evo_config.get('apiKey')
    if not optional_vars['EVOLUTION_INSTANCE_NAME']:
        optional_vars['EVOLUTION_INSTANCE_NAME'] = evo_config.get('instanceName', 'fuel-extractor')
    
    print("\n  Required Variables:")
    all_required = True
    for name, value in required_vars.items():
        status = "✓" if value else "✗"
        display = value[:30] + "..." if value and len(value) > 30 else (value or "NOT SET")
        print(f"    {status} {name}: {display}")
        if not value:
            all_required = False
    
    print("\n  Optional Variables (Evolution API):")
    for name, value in optional_vars.items():
        status = "✓" if value else "-"
        display = value[:30] + "..." if value and len(value) > 30 else (value or "not configured")
        print(f"    {status} {name}: {display}")
    
    return all_required, optional_vars


def check_database():
    """Test database connection"""
    print("\n[2/5] Testing Database Connection...")
    
    try:
        from python.db import Database
        db = Database()
        
        if not db.engine:
            print("    ✗ Database not configured (no DATABASE_URL)")
            return False
        
        # Try to get record count
        count = db.get_record_count()
        print(f"    ✓ Database connected successfully")
        print(f"    ✓ Current records: {count}")
        return True
        
    except Exception as e:
        print(f"    ✗ Database connection failed: {e}")
        return False


def check_google_sheets():
    """Test Google Sheets connection"""
    print("\n[3/5] Testing Google Sheets Connection...")
    
    try:
        from python.google_sheets_uploader import GoogleSheetsUploader
        
        config = load_config()
        spreadsheet_id = config.get('upload', {}).get('google', {}).get('spreadsheetId') or \
                        get_env('GOOGLE_SHEETS_SPREADSHEET_ID')
        worksheet_name = config.get('upload', {}).get('google', {}).get('sheetName', 'SYSTEM FUEL TRACKER')
        
        if not spreadsheet_id:
            print("    - Google Sheets not configured (no spreadsheet ID)")
            return False
        
        uploader = GoogleSheetsUploader(spreadsheet_id=spreadsheet_id, worksheet_name=worksheet_name)
        
        # Access worksheet directly from uploader.worksheet
        worksheet = uploader.worksheet
        if worksheet:
            row_count = worksheet.row_count
            print(f"    ✓ Google Sheets connected successfully")
            print(f"    ✓ Worksheet: {worksheet_name}")
            print(f"    ✓ Total rows: {row_count}")
            return True
        else:
            print("    ✗ Could not access worksheet")
            return False
            
    except Exception as e:
        print(f"    ✗ Google Sheets connection failed: {e}")
        return False


async def check_evolution_api(evo_config: dict):
    """Test and initialize Evolution API"""
    print("\n[4/5] Testing Evolution API Connection...")
    
    api_url = evo_config.get('EVOLUTION_API_URL')
    api_key = evo_config.get('EVOLUTION_API_KEY')
    instance_name = evo_config.get('EVOLUTION_INSTANCE_NAME', 'fuel-extractor')
    
    if not api_url or not api_key:
        print("    - Evolution API not configured")
        print("    - Set EVOLUTION_API_URL and EVOLUTION_API_KEY in .env or config.json")
        return False
    
    try:
        from python.evolution_api import EvolutionAPI
        
        api = EvolutionAPI(
            base_url=api_url,
            api_key=api_key,
            instance_name=instance_name
        )
        
        # Health check (async)
        health = await api.health_check_async()
        if not health:
            print(f"    ✗ Evolution API not responding at {api_url}")
            return False
        
        # health can be a dict with 'status' key or just return data
        if isinstance(health, dict) and health.get('status') == 'unhealthy':
            print(f"    ✗ Evolution API unhealthy: {health.get('error', 'unknown')}")
            return False
        
        print(f"    ✓ Evolution API connected: {api_url}")
        
        # Check instance status (async) - may return None if instance doesn't exist
        status = await api.get_instance_status_async()
        if status:
            state = status.get('state', status.get('instance', {}).get('state', 'unknown'))
            print(f"    ✓ Instance '{instance_name}' state: {state}")
            
            if state == 'open':
                print("    ✓ WhatsApp connected!")
                return True
            elif state == 'close':
                print("    ! WhatsApp disconnected - scan QR code to connect")
            else:
                print(f"    ! Instance state: {state}")
        else:
            print(f"    ! Instance '{instance_name}' not found")
            print("    → Will create during initialization")
        
        return True
        
    except Exception as e:
        print(f"    ✗ Evolution API check failed: {e}")
        return False


async def initialize_evolution_instance(evo_config: dict):
    """Initialize Evolution API instance with webhook"""
    print("\n[5/6] Initializing Evolution API Instance...")
    
    api_url = evo_config.get('EVOLUTION_API_URL')
    api_key = evo_config.get('EVOLUTION_API_KEY')
    instance_name = evo_config.get('EVOLUTION_INSTANCE_NAME', 'fuel-extractor')
    
    if not api_url or not api_key:
        print("    - Skipping (Evolution API not configured)")
        return False
    
    try:
        from python.evolution_api import EvolutionAPI
        
        config = load_config()
        evo_json_config = config.get('evolution', {})
        
        # Get webhook URL
        webhook_url = evo_json_config.get('webhookUrl') or get_env('EVOLUTION_WEBHOOK_URL')
        if not webhook_url:
            host = get_env('WEB_HOST', 'localhost')
            port = get_env('WEB_PORT', '8000')
            webhook_url = f"http://{host}:{port}/webhook/evolution"
        
        api = EvolutionAPI(
            base_url=api_url,
            api_key=api_key,
            instance_name=instance_name
        )
        
        # Check if instance exists (async)
        status = await api.get_instance_status_async()
        
        if not status:
            # Create new instance (sync method - runs fine in async context)
            print(f"    → Creating instance '{instance_name}'...")
            result = api.create_instance(webhook_url=webhook_url)
            if result:
                print(f"    ✓ Instance created successfully")
                
                # Check for QR code
                qr = result.get('qrcode', result.get('qr'))
                if qr and qr.get('base64'):
                    print("\n    ╔════════════════════════════════════════╗")
                    print("    ║     SCAN QR CODE IN EVOLUTION API      ║")
                    print(f"    ║  Visit: {api_url}/manager              ║")
                    print("    ╚════════════════════════════════════════╝\n")
            else:
                print(f"    ✗ Failed to create instance")
                return False
        else:
            print(f"    ✓ Instance '{instance_name}' exists")
        
        # Configure webhook (sync method)
        print(f"    → Configuring webhook: {webhook_url}")
        webhook_events = evo_json_config.get('webhookEvents', ['MESSAGES_UPSERT', 'CONNECTION_UPDATE'])
        
        webhook_result = api.set_webhook(
            webhook_url=webhook_url,
            events=webhook_events
        )
        
        if webhook_result:
            print(f"    ✓ Webhook configured successfully")
        else:
            print(f"    ! Webhook configuration may have failed")
        
        # Get target group info
        group_jid = config.get('whatsapp', {}).get('groupJid')
        group_name = config.get('whatsapp', {}).get('groupName', 'Fuel Reports')
        
        if group_jid:
            print(f"    ✓ Target group: {group_name} ({group_jid})")
        else:
            print(f"    ! No target group configured")
        
        return True
        
    except Exception as e:
        print(f"    ✗ Initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def sync_missed_messages(evo_config: dict) -> dict:
    """
    Fetch and process messages that were received while the system was offline.
    This is the Python equivalent of the Node.js fetchMissedMessages() function.
    """
    print("\n[6/6] Syncing Missed Messages...")
    
    api_url = evo_config.get('EVOLUTION_API_URL')
    api_key = evo_config.get('EVOLUTION_API_KEY')
    instance_name = evo_config.get('EVOLUTION_INSTANCE_NAME', 'fuel-extractor')
    
    if not api_url or not api_key:
        print("    - Skipping (Evolution API not configured)")
        return {}
    
    try:
        from python.evolution_api import EvolutionAPI, extract_message_text
        from python.message_sync import fetch_missed_messages, MessageSyncManager
        
        config = load_config()
        group_jid = config.get('whatsapp', {}).get('groupJid')
        
        if not group_jid:
            print("    - Skipping (no target group configured)")
            return {}
        
        # Check time since last sync
        sync_manager = MessageSyncManager()
        elapsed = sync_manager.get_time_since_last_sync()
        
        if elapsed:
            hours = elapsed.total_seconds() / 3600
            print(f"    → Time since last sync: {hours:.2f} hours")
        else:
            print("    → No previous sync timestamp found")
        
        api = EvolutionAPI(
            base_url=api_url,
            api_key=api_key,
            instance_name=instance_name
        )
        
        # Check if instance is connected (async)
        status = await api.get_instance_status_async()
        if not status:
            print("    ! Instance not connected - skipping message sync")
            return {}
        
        state = status.get('state', status.get('instance', {}).get('state', ''))
        if state != 'open':
            print(f"    ! WhatsApp not connected (state: {state}) - skipping sync")
            return {}
        
        print(f"    → Fetching messages from group: {group_jid}")
        
        # Define the callback to process fuel reports
        async def process_fuel_report_callback(event_data, is_history_sync=False):
            """Process a fuel report from history sync."""
            from python.webhook_receiver import process_fuel_report
            
            # Extract message details from event_data
            data = event_data.get('data', {})
            key = data.get('key', {})
            message_content = data.get('message', {})
            
            # Extract text
            text = extract_message_text(message_content)
            
            # Extract sender info
            participant = key.get('participant', '')
            sender_phone = participant.split('@')[0] if '@' in participant else ''
            push_name = data.get('pushName', 'Unknown')
            timestamp = data.get('messageTimestamp', int(datetime.now().timestamp()))
            remote_jid = key.get('remoteJid', group_jid)
            
            # Call the process_fuel_report with correct parameters
            await process_fuel_report(
                text,
                sender_phone,
                push_name,
                remote_jid,
                timestamp
            )
        
        # Fetch and process missed messages
        stats = await fetch_missed_messages(
            evolution_api=api,
            group_jid=group_jid,
            process_callback=process_fuel_report_callback,
            max_messages=50,
            max_offline_hours=24
        )
        
        # Print results
        if stats.get('total_fetched', 0) > 0:
            print(f"    ✓ Fetched {stats['total_fetched']} messages")
            print(f"    ✓ Processed {stats['newly_processed']} new fuel reports")
            print(f"    ✓ Skipped {stats['already_processed']} already processed")
            if stats['errors'] > 0:
                print(f"    ! {stats['errors']} errors occurred")
        else:
            print("    ✓ No missed messages to process")
        
        return stats
        
    except Exception as e:
        print(f"    ✗ Message sync failed: {e}")
        import traceback
        traceback.print_exc()
        return {}


def setup_shutdown_handler():
    """
    Set up signal handlers to save timestamp on shutdown.
    This allows us to know when the system went offline.
    """
    from python.message_sync import save_shutdown_timestamp
    
    def shutdown_handler(signum, frame):
        print("\n[SHUTDOWN] Saving state before exit...")
        save_shutdown_timestamp()
        print("[SHUTDOWN] Goodbye!")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)


def print_summary(results: dict):
    """Print startup summary"""
    print("\n" + "=" * 60)
    print("                    STARTUP SUMMARY")
    print("=" * 60)
    
    for component, status in results.items():
        icon = "✓" if status else "✗"
        status_text = "Ready" if status else "Failed/Not Configured"
        print(f"  {icon} {component}: {status_text}")
    
    all_ok = all(results.values())
    
    if all_ok:
        print("\n  ✓ All components ready!")
    else:
        print("\n  ! Some components need attention")
        print("    The system will still start, but with limited functionality")
    
    print("\n" + "=" * 60)
    
    return all_ok


def start_server():
    """Start the FastAPI server"""
    print("\n[START] Starting Web Server...")
    
    host = get_env('WEB_HOST') or '0.0.0.0'
    port_str = get_env('WEB_PORT') or '8000'
    port = int(port_str)
    
    print(f"    → Host: {host}")
    print(f"    → Port: {port}")
    print(f"    → Dashboard: http://localhost:{port}")
    print(f"    → Webhook: http://localhost:{port}/webhook/evolution")
    print(f"    → API Health: http://localhost:{port}/api/health")
    print("\n" + "-" * 60)
    
    import uvicorn
    uvicorn.run(
        "python.web:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )


async def main():
    """Main startup routine"""
    print_banner()
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Root directory: {ROOT_DIR}")
    
    # Set up shutdown handler to save timestamp on exit
    setup_shutdown_handler()
    
    results = {}
    
    # Check environment
    env_ok, evo_config = check_environment()
    results['Environment'] = env_ok
    
    # Check database
    results['Database'] = check_database()
    
    # Check Google Sheets
    results['Google Sheets'] = check_google_sheets()
    
    # Check Evolution API
    results['Evolution API'] = await check_evolution_api(evo_config)
    
    # Initialize Evolution instance if API is available
    if results['Evolution API']:
        results['Evolution Instance'] = await initialize_evolution_instance(evo_config)
        
        # Sync missed messages if instance is connected
        if results['Evolution Instance']:
            sync_stats = await sync_missed_messages(evo_config)
            results['Message Sync'] = sync_stats.get('errors', 0) == 0 if sync_stats else True
    else:
        results['Evolution Instance'] = False
        results['Message Sync'] = False
    
    # Print summary
    print_summary(results)
    
    # Ask to continue (only if interactive)
    if not all(results.values()):
        try:
            import sys
            if sys.stdin.isatty():
                response = input("\nContinue starting the server? [Y/n]: ").strip().lower()
                if response == 'n':
                    print("Startup cancelled.")
                    return False
            else:
                print("\n[AUTO] Non-interactive mode - continuing with available components...")
        except (EOFError, KeyboardInterrupt):
            print("\n[AUTO] Continuing with available components...")
    
    return True  # Signal to start server


if __name__ == '__main__':
    try:
        should_start = asyncio.run(main())
        if should_start:
            start_server()
    except KeyboardInterrupt:
        print("\nStartup interrupted.")
        sys.exit(0)
