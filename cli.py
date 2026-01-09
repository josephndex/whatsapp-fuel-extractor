#!/usr/bin/env python3
"""
WhatsApp Fuel Extractor - Advanced CLI

A unified command-line interface for managing the WhatsApp Fuel Extractor.
Replaces all .sh and .bat files with a single, cross-platform tool.

Usage:
    python cli.py <command> [options]
    
Commands:
    listen      Start the WhatsApp listener (Node.js)
    process     Start the fuel data processor
    once        Process messages once and exit
    summary     Generate fuel summary reports
    reset       Reset all data (local, database, sheets)
    status      Show system status
    help        Show this help message
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
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

# Project root directory
ROOT_DIR = Path(__file__).parent.absolute()
CONFIG_PATH = ROOT_DIR / 'config.json'
DATA_DIR = ROOT_DIR / 'data'


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
            bufsize=1
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
        result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
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
    """Reset all data"""
    console.rule("[bold red]WhatsApp Fuel Extractor - FULL RESET[/bold red]")
    console.print()
    
    console.print("[yellow]This will remove:[/yellow]")
    console.print("  • All captured messages (raw, processed, errors)")
    console.print("  • Excel output files")
    console.print("  • WhatsApp session data (will need new QR scan)")
    console.print("  • Log files & notification queues")
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
    console.print("[cyan]Resetting project...[/cyan]")
    console.print()
    
    # Remove data folders content
    console.print("  [dim]Removing message data...[/dim]")
    for folder in ['raw_messages', 'processed', 'errors', 'output']:
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
        shutil.rmtree(session_path)
    session_path.mkdir(exist_ok=True)
    
    cache_path = ROOT_DIR / '.wwebjs_cache'
    if cache_path.exists():
        shutil.rmtree(cache_path)
    
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
    
    # Reset external data sources
    console.print()
    console.print("[cyan]Resetting external data sources...[/cyan]")
    python_cmd = get_conda_python()
    run_command(f"{python_cmd} python/reset_external.py", cwd=ROOT_DIR)
    
    # Recreate directories
    console.print()
    console.print("  [dim]Creating empty data directories...[/dim]")
    for folder in ['raw_messages', 'processed', 'errors', 'output', 'session']:
        folder_path = DATA_DIR / folder
        folder_path.mkdir(exist_ok=True)
        gitkeep = folder_path / '.gitkeep'
        gitkeep.touch()
    
    console.print()
    console.rule("[bold green]✅ FULL Reset Complete![/bold green]")
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
    console.print(f"  Google Sheets: {'✅ Enabled' if upload.get('toGoogleSheets') else '❌ Disabled'}")
    console.print(f"  Database: {'✅ Enabled' if upload.get('toDatabase') else '❌ Disabled'}")
    console.print()
    
    # Session status
    session_exists = (DATA_DIR / 'session' / 'session').exists()
    console.print("[bold]Session:[/bold]")
    console.print(f"  WhatsApp: {'✅ Session exists' if session_exists else '❌ No session (need QR scan)'}")
    console.print()
    
    return 0


def cmd_web(args):
    """Start the web dashboard"""
    console.rule("[bold blue]WhatsApp Fuel Extractor - Web Dashboard[/bold blue]")
    console.print()
    
    host = args.host if hasattr(args, 'host') and args.host else '0.0.0.0'
    port = args.port if hasattr(args, 'port') and args.port else 8080
    
    console.print(f"[green]Starting web dashboard...[/green]")
    console.print(f"[dim]Local:   http://localhost:{port}[/dim]")
    console.print(f"[dim]Network: http://{host}:{port}[/dim]")
    console.print(f"[dim]Press Ctrl+C to stop[/dim]")
    console.print()
    
    python_cmd = get_conda_python()
    return run_command(f"{python_cmd} -c \"from python.web import run_server; run_server(host='{host}', port={port})\"", cwd=ROOT_DIR)


def print_banner():
    """Print the application banner"""
    banner = """
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║   🚗 WhatsApp Fuel Extractor CLI                              ║
║                                                               ║
║   Capture, validate, and export fuel reports from WhatsApp    ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
"""
    if RICH_AVAILABLE:
        console.print(banner, style="bold blue")
    else:
        print(banner)


def main():
    parser = argparse.ArgumentParser(
        description='WhatsApp Fuel Extractor - Advanced CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
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
    reset_parser = subparsers.add_parser('reset', help='Reset all data (local, database, sheets)')
    reset_parser.add_argument('--yes', '-y', action='store_true', help='Skip confirmation prompt')
    
    # status command
    status_parser = subparsers.add_parser('status', help='Show system status')
    
    # web command
    web_parser = subparsers.add_parser('web', help='Start the web dashboard')
    web_parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    web_parser.add_argument('--port', '-p', type=int, default=8080, help='Port to listen on (default: 8080)')
    
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
