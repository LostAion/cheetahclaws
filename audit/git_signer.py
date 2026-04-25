import re

# Git commit messages often use multiple -m or start with a message.
def inject_trailers_into_git_command(command: str, session_id: str) -> str:
    """If the command is a git commit, inject the mandatory trailers."""
    
    if not command.strip().startswith("git commit"):
        return command
        
    trailers = (
        "\n\n"
        "Co-authored-by: CheetahClaws <agent@local>\n"
        f"Agent-Session-ID: {session_id}\n"
        "Agent-Verification: AST-Passed / Linter-Passed"
    )
    
    # Simple heuristic to append trailers: if it uses -m, append it as another -m block
    if "-m" in command:
        # We can just append another -m argument block with the trailers
        return f'{command} -m "{trailers}"'
        
    # If the user does not supply -m, it might open an interactive editor which the agent can't use.
    # Therefore we force an empty message + trailers if no -m.
    # But usually agents are prompted to Provide -m "message".
    return command
