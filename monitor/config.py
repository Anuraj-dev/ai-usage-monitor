"""Central paths and settings for the AI usage monitor."""
import os
from pathlib import Path

HOME = Path.home()

# Data sources (verified to exist on this machine)
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
CODEX_SESSIONS = HOME / ".codex" / "sessions"
OPENCODE_DB = HOME / ".local" / "share" / "opencode" / "opencode.db"

# Where we store our own data — derived from the package location so the
# project can live anywhere (DB always sits next to the code).
APP_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("AIUM_DB", APP_DIR / "usage.db"))

# Obsidian vault daily summary
VAULT = Path(os.environ.get("AIUM_VAULT", HOME / "Anuraj-Dev" / "Obsidian Vault"))
VAULT_NOTE_DIR = VAULT / "AI Created Stuff" / "AI Usage"
WRITE_VAULT = os.environ.get("AIUM_VAULT_OFF") != "1"

# Server
HOST = os.environ.get("AIUM_HOST", "127.0.0.1")
PORT = int(os.environ.get("AIUM_PORT", "8787"))
POLL_SECONDS = int(os.environ.get("AIUM_POLL", "60"))
