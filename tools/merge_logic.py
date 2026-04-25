"""
tools/merge_logic.py — Interactive merge resolution pipeline (git merge-file + $EDITOR).
"""
import os
import subprocess
import tempfile
from cli.ui import TerminalSession
from core.state_manager import get_base_content

def handle_desync(file_path: str, agent_intent: str, is_headless: bool = False) -> str:
    """Prompt and handle hybrid merge.
    Returns the resolved content string, or raises ValueError if aborted.
    """
    choice = TerminalSession.prompt_desync(file_path, is_headless)
    if choice == 'A':
        raise ValueError("ERROR_STATE_DESYNC: Operation aborted by user.")
    if choice == 'O':
        return agent_intent
        
    # Choice is 'M'
    base_content = get_base_content(file_path)
    if not base_content:
        # No base content available, fallback to Abort
        # We can't merge without base.
        raise ValueError("ERROR_STATE_DESYNC: Cannot merge because base content is missing. Aborted.")
        
    with tempfile.TemporaryDirectory() as td:
        base_path = os.path.join(td, "base.txt")
        agent_path = os.path.join(td, "agent.txt")
        with open(base_path, "w", encoding="utf-8") as f:
            f.write(base_content)
        with open(agent_path, "w", encoding="utf-8") as f:
            f.write(agent_intent)
            
        print("Attempting git merge-file...")
        # git merge-file <current> <base> <other>
        result = subprocess.run(
            ["git", "merge-file", "-p", file_path, base_path, agent_path],
            capture_output=True, text=True
        )
        
        if result.returncode == 0:
            print("Context Auto-Merged seamlessly.")
            return result.stdout
            
        # Conflict!
        print("Merge conflicts detected. Launching Tier 2 Escape Hatch...")
        # Write the conflicted output to file_path
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(result.stdout)
            
        import shlex
        editor_str = os.environ.get("EDITOR", "notepad" if os.name == "nt" else "nano")
        editor_args = shlex.split(editor_str)
        subprocess.run(editor_args + [file_path])
        
        # After editor closes, we assume the user resolved it on disk.
        # We just read it back.
        with open(file_path, "r", encoding="utf-8") as f:
            resolved_content = f.read()
            
        return resolved_content
