"""Config utility functions for Game Sentry."""
import json
from typing import Any, Dict, List

CONFIG_FILE = 'config.json'

def load_config() -> Dict[str, Any]:
    """Loads configuration from config.json and returns the config dict."""
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
        return config
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_config(config: Dict[str, Any]):
    """Saves the given configuration dict to config.json."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)

def save_email_config(email_enabled: bool, email_address: str, email_password: str, email_recipients: List[str]):
    """Saves email configuration to config.json."""
    config = load_config()
    config.update({
        'email_enabled': email_enabled,
        'email_address': email_address,
        'email_password': email_password,
        'email_recipients': email_recipients
    })
    save_config(config)

def get_email_config() -> Dict[str, Any]:
    """Gets email configuration from config.json."""
    config = load_config()
    return {
        'email_enabled': config.get('email_enabled', False),
        'email_address': config.get('email_address', ''),
        'email_password': config.get('email_password', ''),
        'email_recipients': config.get('email_recipients', [])
    } 