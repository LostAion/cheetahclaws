"""
core/state_manager.py — SHA-256 registry and disk-state verification.
"""
import hashlib
import os

_state_registry = {}
_content_cache = {}

def get_hash(file_path: str) -> str:
    """Compute SHA-256 hash of a file's contents."""
    if not os.path.exists(file_path):
        return ""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

def snapshot(file_path: str) -> str:
    """Hash the file and record it in the registry."""
    h = get_hash(file_path)
    abs_path = os.path.abspath(file_path)
    _state_registry[abs_path] = h
    if os.path.exists(abs_path):
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                _content_cache[abs_path] = f.read()
        except Exception:
            _content_cache[abs_path] = ""
    return h

def get_base_content(file_path: str) -> str:
    return _content_cache.get(os.path.abspath(file_path), "")

def verify(file_path: str) -> bool:
    """Return True if the current disk hash matches the recorded snapshot."""
    recorded = _state_registry.get(os.path.abspath(file_path))
    if recorded is None:
        return True # Not tracked, so arguably no desync
    return get_hash(file_path) == recorded

class StateDesyncError(Exception):
    pass
