"""Security package: secure credential storage."""
from .credstore import backend_label, delete_secret, load_secret, save_secret

__all__ = ["backend_label", "delete_secret", "load_secret", "save_secret"]
