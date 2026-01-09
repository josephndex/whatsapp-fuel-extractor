import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).parent.parent


def load_env(dotenv_path: Optional[str] = None) -> None:
    """
    Load environment variables from a .env file if present.
    Defaults to project root .env if path not provided.
    """
    if dotenv_path is None:
        dotenv_path = ROOT_DIR / '.env'
    else:
        dotenv_path = Path(dotenv_path)

    if dotenv_path.exists():
        load_dotenv(dotenv_path=str(dotenv_path))


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.getenv(name, default)
