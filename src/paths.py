import os
from pathlib import Path

APP_NAME = "SpeakrFlow"

# Store user data in ~/Library/Application Support so it survives reinstalls
# and doesn't clutter the repo when running from source.
DATA_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA_DIR / "config.json"
HISTORY_FILE = DATA_DIR / "history.json"

# When running from source, .env lives at the project root.
# When running from a py2app bundle, the source path is inside the .app, so
# we fall back to ~/Library/Application Support/SpeakrFlow/.env — drop your
# .env there for the bundled version.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_source_env = PROJECT_ROOT / ".env"
_data_env = DATA_DIR / ".env"

if _source_env.exists():
    ENV_FILE = _source_env
else:
    ENV_FILE = _data_env
