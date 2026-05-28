import os
from pathlib import Path

APP_NAME = "SpeakrFlow"

# Store user data in ~/Library/Application Support so it survives reinstalls
# and doesn't clutter the repo when running from source.
DATA_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA_DIR / "config.json"
HISTORY_FILE = DATA_DIR / "history.json"

# When running from source, load .env from the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
