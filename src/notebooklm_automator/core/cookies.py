"""Cookie parsing utilities for NotebookLM Automator."""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def parse_cookies_txt(file_path: str) -> List[Dict[str, Any]]:
    """
    Parse Netscape cookies.txt format and return Playwright-compatible cookies.

    Args:
        file_path: Path to the cookies.txt file.

    Returns:
        List of cookie dicts in Playwright format.

    Netscape cookies.txt format (tab-separated):
        domain, include_subdomains, path, secure, expiration, name, value
    """
    cookies = []
    path = Path(file_path)

    if not path.exists():
        logger.warning(f"Cookies file not found: {file_path}")
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith("#"):
                    continue

                parts = line.split("\t")
                if len(parts) < 7:
                    continue

                domain, _flag, cookie_path, secure, expiration, name, value = parts[:7]

                # Only include Google-related cookies
                # Include all google.com subdomains (accounts, notebooklm, etc.)
                if not (
                    "google.com" in domain
                    or "google." in domain
                    or "gstatic.com" in domain
                    or "googleapis.com" in domain
                    or "youtube.com" in domain
                ):
                    continue

                cookie = {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": cookie_path,
                    "secure": secure.upper() == "TRUE",
                    "httpOnly": False,  # Cannot be determined from cookies.txt
                }

                # Add expiration if valid (0 means session cookie)
                try:
                    exp = int(expiration)
                    if exp > 0:
                        cookie["expires"] = exp
                except ValueError:
                    pass

                cookies.append(cookie)

        logger.info(f"Parsed {len(cookies)} cookies from {file_path}")
        return cookies

    except Exception as e:
        logger.warning(f"Failed to parse cookies file: {e}")
        return []


def has_chrome_login_state() -> bool:
    """
    Check if Chrome user data directory already has login state.

    Returns:
        True if Chrome Cookies database exists, False otherwise.
    """
    user_data_dir = os.getenv("NOTEBOOKLM_CHROME_USER_DATA_DIR")
    if not user_data_dir:
        user_data_dir = str(Path.home() / ".notebooklm-chrome")

    # Chrome stores cookies in Default/Cookies (SQLite database)
    cookies_db = Path(user_data_dir) / "Default" / "Cookies"
    if cookies_db.exists():
        logger.debug(f"Chrome login state found: {cookies_db}")
        return True

    return False


def get_default_cookies_dir() -> Path:
    """Get the default cookies directory path (project_root/local/cookies)."""
    # Navigate from this file to project root: core -> notebooklm_automator -> src -> project_root
    project_root = Path(__file__).parent.parent.parent.parent
    return project_root / "local" / "cookies"


def find_cookies_file() -> Optional[str]:
    """
    Find cookies file with priority:
    1. NOTEBOOKLM_COOKIES_FILE env var (--cookies-file argument)
    2. Default directory (local/cookies/cookies.txt)

    Returns:
        Path to cookies file or None if not found.
    """
    # Priority 1: env var (from --cookies-file)
    cookies_file = os.getenv("NOTEBOOKLM_COOKIES_FILE")
    if cookies_file:
        if Path(cookies_file).exists():
            logger.info(f"Using cookies file from argument: {cookies_file}")
            return cookies_file
        else:
            logger.warning(f"Cookies file from argument not found: {cookies_file}")

    # Priority 2: default directory
    default_file = get_default_cookies_dir() / "cookies.txt"
    if default_file.exists():
        logger.info(f"Using cookies file from default location: {default_file}")
        return str(default_file)

    return None


def get_cookies_from_env() -> Optional[List[Dict[str, Any]]]:
    """
    Get cookies from file with fallback priority.

    Priority:
    1. --cookies-file argument (NOTEBOOKLM_COOKIES_FILE env var)
    2. Default location (local/cookies/cookies.txt)
    3. Manual login (returns None)

    Returns:
        List of cookies or None if not found.
    """
    cookies_file = find_cookies_file()
    if not cookies_file:
        return None

    cookies = parse_cookies_txt(cookies_file)
    if not cookies:
        return None

    return cookies
