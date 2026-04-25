import os
import tempfile
import subprocess
from pathlib import Path

from core import state_manager
from core.state_manager import StateDesyncError, get_base_content
from tools.fs import generate_unified_diff
from tools.merge_logic import handle_desync

def verify_and_snapshot(file_path: str):
    """Pre-flight check: Re-hash disk before execution and abort on mismatch."""
    if not state_manager.verify(file_path):
        raise StateDesyncError(f"ERROR_STATE_DESYNC: The file {file_path} on disk has changed directly. Agent state is out of sync.")
    return state_manager.snapshot(file_path)

def atomic_write_and_check(file_path: str, new_content: str, is_python: bool = False):
    """Write to tmp, run syntax check (if python), replace."""
    p = Path(file_path)
    tmp_path = p.with_suffix(".tmp")
    
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    
    if is_python:
        try:
            # LibCST parsing acts as our primary syntax validation.
            import libcst as cst
            cst.parse_module(new_content)
        except Exception as e:
            os.remove(tmp_path)
            raise ValueError(f"Syntax compile error: {e}")
            
    os.replace(tmp_path, file_path)
    # Update snapshot after a successful write
    state_manager.snapshot(file_path)

def _ast_edit(params: dict, config: dict) -> str:
    """AST-based Python editor tool."""
    file_path = params["file_path"]
    target_type = params["target_type"]
    target_name = params["target_name"]
    new_source = params["new_source"]
    
    p = Path(file_path)
    if not p.exists():
        return f"Error: file not found: {file_path}"
    
    try:
        verify_and_snapshot(file_path)
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
            
        from tools.ast_editor import mutate_ast
        new_content = mutate_ast(source, target_type, target_name, new_source)
        
        atomic_write_and_check(file_path, new_content, is_python=True)
        
        diff = generate_unified_diff(source, new_content, p.name)
        return f"Changes applied to {p.name}:\n\n{diff}"
        
    except StateDesyncError as e:
        is_headless = config.get("_headless", False)
        try:
            base_source = get_base_content(file_path)
            if not base_source:
                 return "Error: " + str(e) + " (No base content to merge against)"
            from tools.ast_editor import mutate_ast
            agent_intent = mutate_ast(base_source, target_type, target_name, new_source)
            resolved_content = handle_desync(file_path, agent_intent, is_headless)
            atomic_write_and_check(file_path, resolved_content, is_python=True)
            return "Changes applied after interactive merge conflict resolution."
        except Exception as merge_err:
            return f"Merge Failed: {merge_err}"
            
    except Exception as e:
        return f"Error applying AST mutation: {e}"

def _degraded_edit(params: dict, config: dict) -> str:
    """Degraded edit using git apply --check on unified diffs for non-Python assets."""
    file_path = params["file_path"]
    unified_diff = params["unified_diff"]
    
    p = Path(file_path)
    if not p.exists():
        return f"Error: file not found: {file_path}"
        
    try:
        verify_and_snapshot(file_path)
        
        # Write diff to a temp file
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".diff") as temp_diff:
            temp_diff.write(unified_diff)
            temp_diff_path = temp_diff.name
            
        try:
            # Run git apply --check
            result = subprocess.run(
                ["git", "apply", "--check", temp_diff_path],
                cwd=str(p.parent),
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                return f"Error applying diff (git check failed):\n{result.stderr}"
                
            # If check passes, apply it
            subprocess.run(
                ["git", "apply", temp_diff_path],
                cwd=str(p.parent),
                check=True
            )
            state_manager.snapshot(file_path)
            return f"Diff applied successfully to {p.name}."
        finally:
            os.remove(temp_diff_path)
            
    except StateDesyncError as e:
        is_headless = config.get("_headless", False)
        try:
            base_source = get_base_content(file_path)
            if not base_source:
                 return "Error: " + str(e) + " (No base content to merge against)"
            
            with tempfile.TemporaryDirectory() as td:
                # To get agent_intent for degraded edit, we apply the patch to base.txt
                base_path = os.path.join(td, "base.txt")
                with open(base_path, "w", encoding="utf-8") as f:
                    f.write(base_source)
                with tempfile.NamedTemporaryFile("w", delete=False, suffix=".diff") as temp_diff:
                    temp_diff.write(unified_diff)
                    t_diff_path = temp_diff.name
                subprocess.run(["git", "apply", t_diff_path], cwd=td, check=True)
                os.remove(t_diff_path)
                with open(base_path, "r", encoding="utf-8") as f:
                    agent_intent = f.read()

            resolved_content = handle_desync(file_path, agent_intent, is_headless)
            atomic_write_and_check(file_path, resolved_content, is_python=False)
            return "Changes applied after interactive merge conflict resolution."
        except Exception as merge_err:
            return f"Merge Failed: {merge_err}"
            
    except Exception as e:
        return f"Error in degraded edit: {e}"

# Tool schemas and registration will be done in tools/__init__.py
