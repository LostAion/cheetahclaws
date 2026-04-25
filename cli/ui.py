import os
from prompt_toolkit import prompt
from rich.console import Console

console = Console()

class TerminalSession:
    @staticmethod
    def prompt_desync(file_path: str, is_headless: bool = False) -> str:
        """Prompt user for resolution on state desync. Returns 'O' (Overwrite), 'M' (Merge), or 'A' (Abort)."""
        if is_headless:
            # Force Abort in Headless mode
            console.print(f"[red]Headless Mode:[/red] Auto-aborting due to state desync in {file_path}")
            return "A"
            
        console.print(f"\n[bold yellow]State Desync Detected![/bold yellow] The file [cyan]{file_path}[/cyan] has changed on disk.")
        console.print("How do you want to resolve this?")
        console.print("  [bold]O[/bold]verwrite  : The agent's new changes will completely overwrite the file.")
        console.print("  [bold]M[/bold]erge      : Attempt auto-merge via git merge-file, fallback to $EDITOR.")
        console.print("  [bold]A[/bold]bort      : Reject the agent's edit and abort the operation.")
        
        while True:
            choice = prompt("Choose resolution [O/M/A]: ").strip().upper()
            if choice in ["O", "M", "A"]:
                return choice
            console.print("[red]Invalid choice. Please enter O, M, or A.[/red]")
