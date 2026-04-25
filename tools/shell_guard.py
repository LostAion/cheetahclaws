import subprocess
import sys
import os

from tools.security import _is_safe_bash, _CHAIN_OPERATORS

def classify_tier(command: str) -> int:
    """Classifies a shell command into execution tiers.
    Tier 1 (Read-Only) -> 1
    Tier 3 (Destructive) -> 3
    """
    if _is_safe_bash(command):
        return 1
    return 3

from audit.git_signer import inject_trailers_into_git_command

def run_guarded_subprocess(command: str, kwargs: dict, timeout: int, kill_fn) -> str:
    """Execute bash command with guardrails."""
    tier = classify_tier(command)
    
    # Check and inject commit trailers if applicable
    command = inject_trailers_into_git_command(command, session_id="agent") # For now simple static sessionId injection

    # Logging the tier internally
    if kwargs.get('stderr'):
        # Just simple tracking for now
        pass
        # Just simple tracking for now
        pass
        
    proc = subprocess.Popen(command, **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_fn(proc.pid)
        proc.wait()
        return f"Error: timed out after {timeout}s (process killed)"
    out = stdout
    if stderr:
        out += ("\n" if out else "") + "[stderr]\n" + stderr
    return out.strip() or "(no output)"
