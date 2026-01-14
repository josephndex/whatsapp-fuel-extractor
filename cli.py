#!/usr/bin/env python3
"""
WhatsApp Fuel Extractor - Interactive CLI

A unified command-line interface for managing the WhatsApp Fuel Extractor.
Run without arguments for interactive menu, or use command-line options.

Usage:
    python cli.py              # Interactive menu
    python cli.py <command>    # Direct command
    
Commands:
    listen      Start the WhatsApp listener (Node.js)
    process     Start the fuel data processor
    once        Process messages once and exit
    summary     Generate fuel summary reports
    reset       Reset all data (local, database, sheets)
    status      Show system status
    web         Start the web dashboard
"""

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Try to import rich for beautiful output, fallback to basic print
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.prompt import Confirm
    from rich.text import Text
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# Try to import questionary for interactive menu
try:
    import questionary
    from questionary import Style
    QUESTIONARY_AVAILABLE = True
except ImportError:
    QUESTIONARY_AVAILABLE = False

# Project root directory
ROOT_DIR = Path(__file__).parent.absolute()
CONFIG_PATH = ROOT_DIR / 'config.json'
DATA_DIR = ROOT_DIR / 'data'

# Custom style for questionary
if QUESTIONARY_AVAILABLE:
    custom_style = Style([
        ('qmark', 'fg:green bold'),
        ('question', 'fg:white bold'),
        ('answer', 'fg:cyan bold'),
        ('pointer', 'fg:cyan bold'),
        ('highlighted', 'fg:cyan bold'),
        ('selected', 'fg:green'),
        ('separator', 'fg:gray'),
        ('instruction', 'fg:gray italic'),
    ])


class Console:
    """Fallback console if rich is not available"""
    def __init__(self):
        self.is_fallback = not RICH_AVAILABLE
        if RICH_AVAILABLE:
            from rich.console import Console as RichConsole
            self._console = RichConsole()
        
    def print(self, *args, **kwargs):
        if RICH_AVAILABLE:
            self._console.print(*args, **kwargs)
        else:
            # Strip rich markup for fallback
            text = ' '.join(str(a) for a in args)
            # Remove common rich tags
            import re
            text = re.sub(r'\[/?[a-z_ ]+\]', '', text)
            print(text)
    
    def rule(self, title="", **kwargs):
        if RICH_AVAILABLE:
            self._console.rule(title, **kwargs)
        else:
            width = 60
            if title:
                side_len = (width - len(title) - 2) // 2
                print("=" * side_len + f" {title} " + "=" * side_len)
            else:
                print("=" * width)


console = Console()


def load_config():
    """Load configuration from config.json"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    return {}


def get_conda_python():
    """Get the path to Python in the fuel-extractor conda environment"""
    # Check if we're already in the right environment
    if 'fuel-extractor' in sys.prefix:
        return sys.executable
    
    # Try to find conda
    conda_paths = [
        os.path.expanduser('~/anaconda3/bin/conda'),
        os.path.expanduser('~/miniconda3/bin/conda'),
        shutil.which('conda')
    ]
    
    for conda_path in conda_paths:
        if conda_path and os.path.exists(conda_path):
            return f"conda run -n fuel-extractor python"
    
    # Fallback to system python
    return sys.executable


def _handle_remove_readonly(func, path, exc_info):
    """
    Error handler for shutil.rmtree on Windows.
    Handles permission errors by attempting to change file permissions and retry.
    """
    # Check if it's a permission error
    if not isinstance(exc_info[1], PermissionError):
        raise exc_info[1]
    
    try:
        # Try to change file permissions and retry
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        # If still failing, wait briefly and try once more (file might be releasing)
        try:
            time.sleep(0.1)
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            # Give up silently - the outer try/except will handle it
            pass


def run_command(cmd, cwd=None, stream_output=True):
    """Run a command and optionally stream its output"""
    if cwd is None:
        cwd = ROOT_DIR
    
    if stream_output:
        process = subprocess.Popen(
            cmd,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding='utf-8',
            errors='replace'  # Replace undecodable chars instead of crashing
        )
        
        try:
            for line in process.stdout:
                print(line, end='')
            process.wait()
            return process.returncode
        except KeyboardInterrupt:
            process.terminate()
            console.print("\n[yellow]Process interrupted by user[/yellow]")
            return 1
    else:
        result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
        return result.returncode, result.stdout, result.stderr


def cmd_listen(args):
    """Start the WhatsApp listener"""
    console.rule("[bold blue]WhatsApp Fuel Extractor - Listener[/bold blue]")
    console.print()
    
    # Check if node is installed
    if not shutil.which('node'):
        console.print("[red]Error: Node.js is not installed or not in PATH[/red]")
        console.print("Please install Node.js from https://nodejs.org/")
        return 1
    
    # Check if node_modules exists
    if not (ROOT_DIR / 'node_modules').exists():
        console.print("[yellow]Installing Node.js dependencies...[/yellow]")
        run_command("npm install", cwd=ROOT_DIR)
        console.print()
    
    console.print("[green]Starting WhatsApp listener...[/green]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")
    console.print()
    
    return run_command("node node/listener.js", cwd=ROOT_DIR)


def cmd_process(args):
    """Start the fuel data processor"""
    console.rule("[bold blue]WhatsApp Fuel Extractor - Processor[/bold blue]")
    console.print()
    
    console.print("[green]Starting fuel data processor...[/green]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")
    console.print()
    
    python_cmd = get_conda_python()
    return run_command(f"{python_cmd} python/processor.py", cwd=ROOT_DIR)


def cmd_once(args):
    """Process messages once and exit"""
    console.rule("[bold blue]WhatsApp Fuel Extractor - Process Once[/bold blue]")
    console.print()
    
    console.print("[green]Processing pending messages...[/green]")
    console.print()
    
    python_cmd = get_conda_python()
    return run_command(f"{python_cmd} python/processor.py --once", cwd=ROOT_DIR)


def cmd_summary(args):
    """Generate fuel summary reports"""
    console.rule("[bold blue]WhatsApp Fuel Extractor - Summary[/bold blue]")
    console.print()
    
    period = args.period if hasattr(args, 'period') and args.period else 'weekly'
    car = args.car if hasattr(args, 'car') and args.car else None
    
    console.print(f"[green]Generating {period} summary...[/green]")
    console.print()
    
    python_cmd = get_conda_python()
    
    if car:
        cmd = f"{python_cmd} python/weekly_summary.py --car {car}"
        if hasattr(args, 'days') and args.days:
            cmd += f" --days {args.days}"
    else:
        cmd = f"{python_cmd} python/weekly_summary.py --{period}"
    
    return run_command(cmd, cwd=ROOT_DIR)


def cmd_reset(args):
    """Reset data with selective options"""
    console.rule("[bold red]WhatsApp Fuel Extractor - Reset[/bold red]")
    console.print()
    
    # Check if --all flag is passed for full reset without prompts
    if hasattr(args, 'all') and args.all:
        return _perform_full_reset(args)
    
    # Interactive mode - let user select what to reset
    if QUESTIONARY_AVAILABLE:
        reset_options = questionary.checkbox(
            "What would you like to reset?",
            choices=[
                questionary.Choice("[1] Raw Messages (unprocessed queue)", value="raw_messages", checked=False),
                questionary.Choice("[2] Processed Messages (already exported)", value="processed", checked=False),
                questionary.Choice("[3] Error Files (failed validations)", value="errors", checked=False),
                questionary.Choice("[4] Excel Output Files", value="excel", checked=False),
                questionary.Choice("[5] WhatsApp Session (requires new QR scan)", value="session", checked=False),
                questionary.Choice("[6] Tracking Data (cooldowns, history, approvals)", value="tracking", checked=False),
                questionary.Choice("[7] Log Files", value="logs", checked=False),
                questionary.Choice("[8] Backups Folder", value="backups", checked=False),
                questionary.Choice("[9] Database Records (PostgreSQL)", value="database", checked=False),
                questionary.Choice("[10] Google Sheets Data", value="sheets", checked=False),
                questionary.Choice("[!] EVERYTHING (Full Reset)", value="everything", checked=False),
            ],
            style=custom_style,
            instruction="(Space to select, Enter to confirm)"
        ).ask()
        
        if not reset_options:
            console.print("[yellow]Reset cancelled.[/yellow]")
            return 0
        
        # If "everything" selected, do full reset
        if "everything" in reset_options:
            return _perform_full_reset(args)
        
    else:
        # Fallback for no questionary
        console.print("[bold]Reset Options:[/bold]")
        console.print("  1. Raw Messages only")
        console.print("  2. Processed Messages only")
        console.print("  3. Error Files only")
        console.print("  4. Excel Output Files")
        console.print("  5. WhatsApp Session")
        console.print("  6. Tracking Data (cooldowns, history)")
        console.print("  7. Log Files")
        console.print("  8. Backups")
        console.print("  9. Database Records")
        console.print("  10. Google Sheets Data")
        console.print("  A. EVERYTHING (Full Reset)")
        console.print("  Q. Cancel")
        console.print()
        
        choice = input("Enter choices (comma-separated, e.g., 1,2,4): ").strip().upper()
        
        if choice == 'Q' or not choice:
            console.print("[yellow]Reset cancelled.[/yellow]")
            return 0
        
        if choice == 'A':
            return _perform_full_reset(args)
        
        # Map choices to options
        choice_map = {
            '1': 'raw_messages', '2': 'processed', '3': 'errors',
            '4': 'excel', '5': 'session', '6': 'tracking',
            '7': 'logs', '8': 'backups', '9': 'database', '10': 'sheets'
        }
        
        reset_options = []
        for c in choice.split(','):
            c = c.strip()
            if c in choice_map:
                reset_options.append(choice_map[c])
    
    if not reset_options:
        console.print("[yellow]No options selected. Reset cancelled.[/yellow]")
        return 0
    
    # Show confirmation
    console.print()
    console.print("[yellow]You selected to reset:[/yellow]")
    for opt in reset_options:
        console.print(f"  • {opt.replace('_', ' ').title()}")
    console.print()
    
    # Confirm
    if not (hasattr(args, 'yes') and args.yes):
        if RICH_AVAILABLE:
            from rich.prompt import Confirm
            if not Confirm.ask("[red]Proceed with reset?[/red]"):
                console.print("[yellow]Reset cancelled.[/yellow]")
                return 0
        else:
            confirm = input("Proceed with reset? (y/N): ")
            if confirm.lower() != 'y':
                console.print("Reset cancelled.")
                return 0
    
    # Perform selected resets
    console.print()
    console.print("[cyan]Resetting selected items...[/cyan]")
    console.print()
    
    reset_count = 0
    
    # Raw Messages
    if 'raw_messages' in reset_options:
        console.print("  [dim]Removing raw messages...[/dim]")
        folder_path = DATA_DIR / 'raw_messages'
        if folder_path.exists():
            count = 0
            for f in folder_path.glob('*.json'):
                f.unlink()
                count += 1
            console.print(f"    [green]✓ Removed {count} raw message files[/green]")
            reset_count += count
    
    # Processed Messages
    if 'processed' in reset_options:
        console.print("  [dim]Removing processed messages...[/dim]")
        folder_path = DATA_DIR / 'processed'
        if folder_path.exists():
            count = 0
            for f in folder_path.glob('*.json'):
                f.unlink()
                count += 1
            console.print(f"    [green]✓ Removed {count} processed files[/green]")
            reset_count += count
    
    # Error Files
    if 'errors' in reset_options:
        console.print("  [dim]Removing error files...[/dim]")
        folder_path = DATA_DIR / 'errors'
        if folder_path.exists():
            count = 0
            for f in folder_path.glob('*.json'):
                f.unlink()
                count += 1
            console.print(f"    [green]✓ Removed {count} error files[/green]")
            reset_count += count
    
    # Excel Output
    if 'excel' in reset_options:
        console.print("  [dim]Removing Excel output files...[/dim]")
        folder_path = DATA_DIR / 'output'
        if folder_path.exists():
            count = 0
            for f in folder_path.glob('*'):
                if f.name != '.gitkeep':
                    if f.is_file():
                        f.unlink()
                        count += 1
                    else:
                        shutil.rmtree(f)
                        count += 1
            console.print(f"    [green]✓ Removed {count} output files[/green]")
            reset_count += count
    
    # WhatsApp Session
    if 'session' in reset_options:
        console.print("  [dim]Removing WhatsApp session...[/dim]")
        session_path = DATA_DIR / 'session'
        if session_path.exists():
            try:
                shutil.rmtree(session_path, onerror=_handle_remove_readonly)
                session_path.mkdir(exist_ok=True)
                console.print("    [green]✓ Session removed (new QR scan required)[/green]")
                reset_count += 1
            except Exception as e:
                console.print(f"    [yellow]⚠ Could not fully remove session: {e}[/yellow]")
        
        cache_path = ROOT_DIR / '.wwebjs_cache'
        if cache_path.exists():
            try:
                shutil.rmtree(cache_path, onerror=_handle_remove_readonly)
                console.print("    [green]✓ Browser cache removed[/green]")
            except Exception as e:
                console.print(f"    [yellow]⚠ Could not remove cache: {e}[/yellow]")
    
    # Tracking Data
    if 'tracking' in reset_options:
        console.print("  [dim]Resetting tracking files...[/dim]")
        tracking_files = [
            ('car_last_update.json', '{}'),
            ('driver_history.json', '{}'),
            ('last_processed.json', '{}'),
            ('car_summary.json', '{}'),
            ('pending_approvals.json', '[]'),
            ('confirmations.json', '[]'),
            ('validation_errors.json', '[]'),
        ]
        for f, content in tracking_files:
            fp = DATA_DIR / f
            with open(fp, 'w') as file:
                file.write(content)
        console.print(f"    [green]✓ Reset {len(tracking_files)} tracking files[/green]")
        reset_count += len(tracking_files)
    
    # Log Files
    if 'logs' in reset_options:
        console.print("  [dim]Removing log files...[/dim]")
        count = 0
        for f in ['listener.log', 'processor.log']:
            fp = ROOT_DIR / f
            if fp.exists():
                fp.unlink()
                count += 1
        # Also check data folder for crash logs
        crash_log = DATA_DIR / 'crash_log.txt'
        if crash_log.exists():
            crash_log.unlink()
            count += 1
        console.print(f"    [green]✓ Removed {count} log files[/green]")
        reset_count += count
    
    # Backups
    if 'backups' in reset_options:
        console.print("  [dim]Removing backup files...[/dim]")
        backup_path = DATA_DIR / 'backups'
        if backup_path.exists():
            count = 0
            for f in backup_path.glob('*'):
                if f.is_file():
                    f.unlink()
                    count += 1
            console.print(f"    [green]✓ Removed {count} backup files[/green]")
            reset_count += count
    
    # Database
    if 'database' in reset_options:
        console.print("  [dim]Resetting database records...[/dim]")
        python_cmd = get_conda_python()
        result = run_command(f"{python_cmd} python/reset_external.py --database-only", cwd=ROOT_DIR, stream_output=False)
        if isinstance(result, tuple):
            if result[0] == 0:
                console.print("    [green]✓ Database records cleared[/green]")
                reset_count += 1
            else:
                console.print(f"    [yellow]⚠ Database reset issue: {result[2]}[/yellow]")
        else:
            console.print("    [green]✓ Database reset executed[/green]")
            reset_count += 1
    
    # Google Sheets
    if 'sheets' in reset_options:
        console.print("  [dim]Resetting Google Sheets data...[/dim]")
        python_cmd = get_conda_python()
        result = run_command(f"{python_cmd} python/reset_external.py --sheets-only", cwd=ROOT_DIR, stream_output=False)
        if isinstance(result, tuple):
            if result[0] == 0:
                console.print("    [green]✓ Google Sheets data cleared[/green]")
                reset_count += 1
            else:
                console.print(f"    [yellow]⚠ Sheets reset issue: {result[2]}[/yellow]")
        else:
            console.print("    [green]✓ Google Sheets reset executed[/green]")
            reset_count += 1
    
    console.print()
    console.rule(f"[bold green]Reset Complete! ({reset_count} items)[/bold green]")
    console.print()
    
    return 0


def _perform_full_reset(args):
    """Perform a full reset of everything"""
    console.print("[yellow]This will remove EVERYTHING:[/yellow]")
    console.print("  • All captured messages (raw, processed, errors)")
    console.print("  • Excel output files")
    console.print("  • WhatsApp session data (will need new QR scan)")
    console.print("  • Log files & notification queues")
    console.print("  • Backup files")
    console.print("  • [bold]DATABASE records[/bold] (fuel_records table)")
    console.print("  • [bold]GOOGLE SHEET data[/bold]")
    console.print()
    
    # Confirm unless --yes flag is passed
    if not (hasattr(args, 'yes') and args.yes):
        if RICH_AVAILABLE:
            from rich.prompt import Confirm
            if not Confirm.ask("[red]Are you sure you want to FULLY reset?[/red]"):
                console.print("[yellow]Reset cancelled.[/yellow]")
                return 0
        else:
            confirm = input("Are you sure you want to FULLY reset? (y/N): ")
            if confirm.lower() != 'y':
                console.print("Reset cancelled.")
                return 0
    
    console.print()
    console.print("[cyan]Performing FULL reset...[/cyan]")
    console.print()
    
    # Remove data folders content
    console.print("  [dim]Removing message data...[/dim]")
    for folder in ['raw_messages', 'processed', 'errors', 'output', 'backups']:
        folder_path = DATA_DIR / folder
        if folder_path.exists():
            for f in folder_path.glob('*'):
                if f.name != '.gitkeep':
                    if f.is_file():
                        f.unlink()
                    else:
                        shutil.rmtree(f)
    
    # Remove session
    console.print("  [dim]Removing WhatsApp session...[/dim]")
    session_path = DATA_DIR / 'session'
    if session_path.exists():
        try:
            shutil.rmtree(session_path, onerror=_handle_remove_readonly)
        except Exception as e:
            console.print(f"  [yellow]Warning: Could not fully remove session folder: {e}[/yellow]")
            console.print("  [dim]Some files may be locked by Chrome. Try closing any browser windows.[/dim]")
    session_path.mkdir(exist_ok=True)
    
    cache_path = ROOT_DIR / '.wwebjs_cache'
    if cache_path.exists():
        try:
            shutil.rmtree(cache_path, onerror=_handle_remove_readonly)
        except Exception as e:
            console.print(f"  [yellow]Warning: Could not fully remove cache folder: {e}[/yellow]")
    
    # Remove notification queues
    console.print("  [dim]Removing notification files...[/dim]")
    for f in ['confirmations.json', 'validation_errors.json', 'weekly_summary.json']:
        fp = DATA_DIR / f
        if fp.exists():
            fp.unlink()
    
    # Reset tracking JSON files
    console.print("  [dim]Resetting tracking files...[/dim]")
    for f, content in [
        ('car_last_update.json', '{}'),
        ('driver_history.json', '{}'),
        ('last_processed.json', '{}'),
        ('car_summary.json', '{}'),
        ('pending_approvals.json', '[]'),
    ]:
        with open(DATA_DIR / f, 'w') as fp:
            fp.write(content)
    
    # Remove log files
    console.print("  [dim]Removing log files...[/dim]")
    for f in ['listener.log', 'processor.log']:
        fp = ROOT_DIR / f
        if fp.exists():
            fp.unlink()
    crash_log = DATA_DIR / 'crash_log.txt'
    if crash_log.exists():
        crash_log.unlink()
    
    # Reset external data sources
    console.print()
    console.print("[cyan]Resetting external data sources...[/cyan]")
    python_cmd = get_conda_python()
    run_command(f"{python_cmd} python/reset_external.py", cwd=ROOT_DIR)
    
    # Recreate directories
    console.print()
    console.print("  [dim]Creating empty data directories...[/dim]")
    for folder in ['raw_messages', 'processed', 'errors', 'output', 'session', 'backups']:
        folder_path = DATA_DIR / folder
        folder_path.mkdir(exist_ok=True)
        gitkeep = folder_path / '.gitkeep'
        gitkeep.touch()
    
    console.print()
    console.rule("[bold green]FULL Reset Complete![/bold green]")
    console.print()
    console.print("[green]All local files, database, and Google Sheet cleared.[/green]")
    console.print("[green]Ready for fresh testing![/green]")
    console.print()
    
    return 0


def cmd_status(args):
    """Show system status"""
    console.rule("[bold blue]WhatsApp Fuel Extractor - Status[/bold blue]")
    console.print()
    
    config = load_config()
    
    # Basic info
    console.print("[bold]Configuration:[/bold]")
    console.print(f"  Group Name: {config.get('whatsapp', {}).get('groupName', 'Not set')}")
    console.print(f"  Phone: {config.get('whatsapp', {}).get('phoneNumber', 'Not set')}")
    console.print()
    
    # Data counts
    console.print("[bold]Data Status:[/bold]")
    
    raw_count = len(list((DATA_DIR / 'raw_messages').glob('*.json'))) if (DATA_DIR / 'raw_messages').exists() else 0
    processed_count = len(list((DATA_DIR / 'processed').glob('*.json'))) if (DATA_DIR / 'processed').exists() else 0
    error_count = len(list((DATA_DIR / 'errors').glob('*.json'))) if (DATA_DIR / 'errors').exists() else 0
    
    console.print(f"  Raw messages: {raw_count}")
    console.print(f"  Processed: {processed_count}")
    console.print(f"  Errors: {error_count}")
    console.print()
    
    # Pending approvals
    pending_path = DATA_DIR / 'pending_approvals.json'
    pending_count = 0
    if pending_path.exists():
        try:
            with open(pending_path, 'r') as f:
                pending = json.load(f)
                pending_count = len([p for p in pending if p.get('status') == 'pending'])
        except:
            pass
    
    console.print(f"  Pending approvals: {pending_count}")
    console.print()
    
    # Upload settings
    upload = config.get('upload', {})
    console.print("[bold]Upload Settings:[/bold]")
    console.print(f"  Google Sheets: {'[ON] Enabled' if upload.get('toGoogleSheets') else '[OFF] Disabled'}")
    console.print(f"  Database: {'[ON] Enabled' if upload.get('toDatabase') else '[OFF] Disabled'}")
    console.print()
    
    # Session status
    session_exists = (DATA_DIR / 'session' / 'session').exists()
    console.print("[bold]Session:[/bold]")
    console.print(f"  WhatsApp: {'[OK] Session exists' if session_exists else '[!] No session (need QR scan)'}")
    console.print()
    
    return 0


def cmd_web(args):
    """Start the web dashboard"""
    console.rule("[bold blue]WhatsApp Fuel Extractor - Web Dashboard[/bold blue]")
    console.print()
    
    host = args.host if hasattr(args, 'host') and args.host else '0.0.0.0'
    port = args.port if hasattr(args, 'port') and args.port else 8080
    auto_port = getattr(args, 'auto_port', True)  # Default to auto port selection
    
    console.print(f"[green]Starting web dashboard...[/green]")
    if auto_port:
        console.print(f"[dim]Preferred port: {port} (will auto-select if in use)[/dim]")
    else:
        console.print(f"[dim]Local:   http://localhost:{port}[/dim]")
        console.print(f"[dim]Network: http://{host}:{port}[/dim]")
    console.print(f"[dim]Press Ctrl+C to stop[/dim]")
    console.print()
    
    python_cmd = get_conda_python()
    auto_port_str = "True" if auto_port else "False"
    return run_command(f"{python_cmd} -c \"from python.web import run_server; run_server(host='{host}', port={port}, auto_port={auto_port_str})\"", cwd=ROOT_DIR)


def print_banner():
    """Print the application banner"""
    banner = """
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║   WhatsApp Fuel Extractor CLI                                 ║
║                                                               ║
║   Capture, validate, and export fuel reports from WhatsApp    ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
"""
    if RICH_AVAILABLE:
        console.print(banner, style="bold blue")
    else:
        print(banner)


def interactive_menu():
    """Show interactive menu for selecting commands"""
    if not QUESTIONARY_AVAILABLE:
        console.print("[yellow]Interactive menu requires 'questionary' package.[/yellow]")
        console.print("[dim]Install with: pip install questionary[/dim]")
        console.print("[dim]Or use: python cli.py <command>[/dim]")
        return None
    
    print_banner()
    
    menu_choices = [
        questionary.Choice("[1] Start WhatsApp Listener", value="listen"),
        questionary.Choice("[2] Start Fuel Processor", value="process"),
        questionary.Choice("[3] Process Messages Once", value="once"),
        questionary.Separator(),
        questionary.Choice("[4] Generate Summary Report", value="summary_menu"),
        questionary.Choice("[5] Open Web Dashboard", value="web"),
        questionary.Choice("[6] Show System Status", value="status"),
        questionary.Separator(),
        questionary.Choice("[7] Reset All Data", value="reset"),
        questionary.Choice("[Q] Exit", value="exit"),
    ]
    
    choice = questionary.select(
        "What would you like to do?",
        choices=menu_choices,
        style=custom_style,
        instruction="(Use ↑↓ arrows to navigate, Enter to select)"
    ).ask()
    
    if choice == "exit" or choice is None:
        console.print("\n[dim]Goodbye![/dim]")
        return None
    
    if choice == "summary_menu":
        return interactive_summary_menu()
    
    return choice


def interactive_summary_menu():
    """Show submenu for summary options"""
    summary_choices = [
        questionary.Choice("[1] Daily Summary (Today)", value=("summary", "daily")),
        questionary.Choice("[2] Weekly Summary (Last 7 days)", value=("summary", "weekly")),
        questionary.Choice("[3] Monthly Summary (Last 30 days)", value=("summary", "monthly")),
        questionary.Separator(),
        questionary.Choice("[4] Vehicle-Specific Summary", value=("summary", "car")),
        questionary.Separator(),
        questionary.Choice("[B] Back to Main Menu", value="back"),
    ]
    
    choice = questionary.select(
        "Select summary type:",
        choices=summary_choices,
        style=custom_style,
        instruction="(Use ↑↓ arrows to navigate, Enter to select)"
    ).ask()
    
    if choice == "back" or choice is None:
        return interactive_menu()
    
    return choice


def interactive_web_port():
    """Ask for web dashboard port, returns (port, auto_port)"""
    choice = questionary.select(
        "Port selection:",
        choices=[
            questionary.Choice("[A] Auto (find available port starting at 8080)", value="auto"),
            questionary.Choice("[M] Specify port manually", value="manual"),
        ],
        style=custom_style
    ).ask()
    
    if choice == "auto" or choice is None:
        return 8080, True
    
    port = questionary.text(
        "Enter port number:",
        default="8080",
        style=custom_style,
        validate=lambda x: x.isdigit() and 1 <= int(x) <= 65535
    ).ask()
    
    return int(port) if port else 8080, True  # Still use auto_port for manual selection


def run_interactive():
    """Run the interactive CLI"""
    while True:
        result = interactive_menu()
        
        if result is None:
            return 0
        
        # Create args-like object
        class Args:
            pass
        args = Args()
        
        if isinstance(result, tuple):
            command, option = result
            if command == "summary":
                args.period = option if option != "car" else None
                args.car = None
                args.days = None
                
                if option == "car":
                    car_plate = questionary.text(
                        "Enter vehicle plate (e.g., KCA542Q):",
                        style=custom_style
                    ).ask()
                    if car_plate:
                        args.car = car_plate.upper().replace(" ", "")
                        days = questionary.text(
                            "Number of days to look back:",
                            default="30",
                            style=custom_style
                        ).ask()
                        args.days = int(days) if days and days.isdigit() else 30
                    else:
                        continue
                
                cmd_summary(args)
        else:
            if result == "listen":
                cmd_listen(args)
            elif result == "process":
                cmd_process(args)
            elif result == "once":
                cmd_once(args)
            elif result == "status":
                cmd_status(args)
            elif result == "web":
                args.port, args.auto_port = interactive_web_port()
                args.host = "0.0.0.0"
                cmd_web(args)
            elif result == "reset":
                args.yes = False
                cmd_reset(args)
        
        # After command completes, ask if user wants to continue
        console.print()
        if not questionary.confirm(
            "Return to main menu?",
            default=True,
            style=custom_style
        ).ask():
            console.print("\n[dim]Goodbye![/dim]")
            return 0


def main():
    # Check if running in a proper terminal for interactive mode
    is_tty = sys.stdin.isatty()
    
    # If no arguments provided, show interactive menu (only if we have a TTY)
    if len(sys.argv) == 1 and QUESTIONARY_AVAILABLE and is_tty:
        return run_interactive()
    elif len(sys.argv) == 1 and not is_tty:
        # Not a TTY, show help instead of failing
        print_banner()
        console.print("[yellow][!] Interactive mode requires a terminal.[/yellow]")
        console.print("[dim]Use command-line arguments instead:[/dim]")
        console.print()
        console.print("  ./fuel.sh listen    - Start WhatsApp listener")
        console.print("  ./fuel.sh process   - Start fuel processor")
        console.print("  ./fuel.sh once      - Process messages once")
        console.print("  ./fuel.sh summary   - Generate summary report")
        console.print("  ./fuel.sh web       - Start web dashboard")
        console.print("  ./fuel.sh status    - Show system status")
        console.print("  ./fuel.sh reset     - Reset all data")
        console.print()
        console.print("[dim]For help: ./fuel.sh --help[/dim]")
        return 0
    
    parser = argparse.ArgumentParser(
        description='WhatsApp Fuel Extractor - Interactive CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py                     Interactive menu (recommended)
  python cli.py listen              Start WhatsApp listener
  python cli.py process             Start fuel processor
  python cli.py once                Process pending messages once
  python cli.py summary             Generate weekly summary
  python cli.py summary --daily     Generate daily summary
  python cli.py summary --car KCA542Q   Get vehicle summary
  python cli.py web                 Start web dashboard
  python cli.py web --port 3000     Start on custom port
  python cli.py reset               Reset all data
  python cli.py reset --yes         Reset without confirmation
  python cli.py status              Show system status
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # listen command
    listen_parser = subparsers.add_parser('listen', help='Start the WhatsApp listener')
    
    # process command
    process_parser = subparsers.add_parser('process', help='Start the fuel data processor')
    
    # once command
    once_parser = subparsers.add_parser('once', help='Process messages once and exit')
    
    # summary command
    summary_parser = subparsers.add_parser('summary', help='Generate fuel summary reports')
    summary_parser.add_argument('--daily', dest='period', action='store_const', const='daily', help='Daily summary')
    summary_parser.add_argument('--weekly', dest='period', action='store_const', const='weekly', help='Weekly summary (default)')
    summary_parser.add_argument('--monthly', dest='period', action='store_const', const='monthly', help='Monthly summary')
    summary_parser.add_argument('--car', type=str, help='Get summary for specific vehicle')
    summary_parser.add_argument('--days', type=int, help='Number of days for vehicle summary')
    
    # reset command
    reset_parser = subparsers.add_parser('reset', help='Reset data with selective options')
    reset_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation prompt')
    reset_parser.add_argument('--all', '-a', action='store_true', help='Reset everything without interactive menu')
    
    # status command
    status_parser = subparsers.add_parser('status', help='Show system status')
    
    # web command
    web_parser = subparsers.add_parser('web', help='Start the web dashboard')
    web_parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    web_parser.add_argument('--port', '-p', type=int, default=8080, help='Preferred port (default: 8080, auto-selects if in use)')
    web_parser.add_argument('--no-auto-port', dest='auto_port', action='store_false', default=True,
                           help='Disable automatic port selection (fail if port is in use)')
    
    args = parser.parse_args()
    
    print_banner()
    
    if args.command is None:
        parser.print_help()
        return 0
    
    commands = {
        'listen': cmd_listen,
        'process': cmd_process,
        'once': cmd_once,
        'summary': cmd_summary,
        'reset': cmd_reset,
        'status': cmd_status,
        'web': cmd_web,
    }
    
    if args.command in commands:
        return commands[args.command](args)
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())
