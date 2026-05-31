import os
import re
import json
import sys
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn, TextColumn
from rich.box import ROUNDED
from requests import Session, exceptions
try:
    import pycurl
    HAS_PYCURL = True
except ImportError:
    HAS_PYCURL = False

# Nexus Mods API endpoints (rate-limited)
API_BASE_URL = "https://api.nexusmods.com/v1"
MOD_DETAILS_URL = API_BASE_URL + "/games/{game_id}/mods/{mod_id}.json"
FILE_LIST_URL = API_BASE_URL + "/games/{game_id}/mods/{mod_id}/files.json"
DOWNLOAD_LINK_URL = API_BASE_URL + "/games/{game_id}/mods/{mod_id}/files/{file_id}/download_link.json"

# Config file path for local development
CONFIG_FILE = ".nexus_config.json"

# User-Agent to mimic browser requests (for legitimate API use)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"

console = Console()

def get_api_key():
    """Retrieve Nexus Mods API Key from env, local config, or interactive prompt."""
    # 1. Try Environment Variable
    api_key = os.environ.get("NEXUS_API_KEY")
    if api_key:
        console.print("[green]API Key loaded from environment variable NEXUS_API_KEY.[/green]")
        return api_key

    # 2. Try Local Config File
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                api_key = config.get("api_key")
                if api_key:
                    console.print(f"[green]API Key loaded from config file: {CONFIG_FILE}[/green]")
                    return api_key
        except Exception as e:
            console.print(f"[yellow]Warning: Could not read config file: {e}[/yellow]")

    # 3. Interactive Prompt
    console.print(Panel(
        "[bold yellow]Nexus Mods API Key Required[/bold yellow]\n\n"
        "To fetch mod data and request downloads, this script requires a personal API Key.\n"
        "You can obtain one from your Nexus Mods account:\n"
        "-> [cyan]https://www.nexusmods.com/users/myaccount?tab=api[/cyan]",
        title="Authentication", border_style="yellow"
    ))
    api_key = Prompt.ask("Enter your Nexus Mods API Key", password=True)
    
    if not api_key.strip():
        console.print("[red]API Key cannot be empty.[/red]")
        sys.exit(1)

    # Offer to save the key locally
    save_key = Confirm.ask("Would you like to save this key to '.nexus_config.json' for future use?", default=True)
    if save_key:
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump({"api_key": api_key}, f, indent=4)
            console.print(f"[green]API Key saved successfully to {CONFIG_FILE}[/green]")
        except Exception as e:
            console.print(f"[red]Error saving API Key config: {e}[/red]")

    return api_key

def parse_mod_url(mod_url):
    """Extract game_id and mod_id from standard Nexus Mods URLs."""
    # Matches URLs like: https://www.nexusmods.com/skyrimspecialedition/mods/12345
    # or: https://www.nexusmods.com/skyrimspecialedition/mods/12345/?tab=files
    match = re.search(r"nexusmods\.com/([^/]+)/mods/(\d+)", mod_url)
    if match:
        return match.group(1), match.group(2)
    return None, None

def get_mod_info(session, game_id, mod_id):
    """Extract mod details for research metadata analysis."""
    try:
        response = session.get(MOD_DETAILS_URL.format(game_id=game_id, mod_id=mod_id))
        response.raise_for_status()
        return response.json()
    except exceptions.RequestException as e:
        console.print(f"[red]Error fetching mod details: {e}[/red]")
        sys.exit(1)

def fetch_file_info(session, game_id, mod_id):
    """Get file IDs, names, and lists for a given mod."""
    try:
        response = session.get(FILE_LIST_URL.format(game_id=game_id, mod_id=mod_id))
        response.raise_for_status()
        
        # Nexus Mods files endpoint returns a JSON containing "files" and "file_updates"
        # We need the list under "files"
        data = response.json()
        if "files" in data:
            return data["files"]
        return []
    except exceptions.RequestException as e:
        console.print(f"[red]Error fetching file list: {e}[/red]")
        sys.exit(1)

def get_download_link(session, game_id, mod_id, file_id):
    """Request the download link API to generate an authenticated download URL."""
    try:
        response = session.get(DOWNLOAD_LINK_URL.format(game_id=game_id, mod_id=mod_id, file_id=file_id))
        response.raise_for_status()
        links = response.json()
        
        # The response is an array of mirrors, e.g. [{"name": "Premium CDN", "URI": "..."}]
        if isinstance(links, list) and len(links) > 0:
            # Prefer the first URI (usually the best CDN location)
            return links[0]["URI"]
        return None
    except exceptions.RequestException as e:
        console.print(f"[red]Error fetching download link for file ID {file_id}: {e}[/red]")
        return None

def format_size(bytes_size):
    """Format file size nicely in KB, MB, or GB."""
    if bytes_size is None:
        return "Unknown"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"

def download_file(curl, file_url, output_path):
    """Download a single file using pycurl (if available) or urllib (as a fallback) with a Rich progress bar."""
    import urllib.request
    
    # Define a clean layout progress bar
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        transient=True
    ) as progress:
        
        # Add a new progress task
        filename = os.path.basename(output_path)
        task_id = progress.add_task(f"Downloading {filename}", total=None)

        if HAS_PYCURL and curl is not None:
            def progress_callback(download_t, download_d, upload_t, upload_d):
                # If the download total size is known, set the total
                if download_t > 0:
                    progress.update(task_id, total=download_t, completed=download_d)
                elif download_d > 0:
                    progress.update(task_id, completed=download_d)
                return 0  # 0 indicates pycurl should continue

            # Set up pycurl options
            with open(output_path, 'wb') as f:
                curl.setopt(pycurl.URL, file_url)
                curl.setopt(pycurl.WRITEDATA, f)
                curl.setopt(pycurl.NOPROGRESS, False)
                curl.setopt(pycurl.XFERINFOFUNCTION, progress_callback)
                curl.setopt(pycurl.FOLLOWLOCATION, True)  # Follow redirect
                curl.setopt(pycurl.USERAGENT, USER_AGENT)
                
                try:
                    curl.perform()
                except pycurl.error as e:
                    console.print(f"[red]pycurl error downloading file: {e}[/red]")
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    return False
        else:
            # Fallback to urllib.request (Standard Library)
            try:
                req = urllib.request.Request(file_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req) as response:
                    total_size = int(response.info().get('Content-Length', 0))
                    if total_size > 0:
                        progress.update(task_id, total=total_size)
                    
                    downloaded = 0
                    block_size = 1024 * 64
                    with open(output_path, 'wb') as f:
                        while True:
                            buffer = response.read(block_size)
                            if not buffer:
                                break
                            f.write(buffer)
                            downloaded += len(buffer)
                            progress.update(task_id, completed=downloaded)
            except Exception as e:
                console.print(f"[red]Error downloading file: {e}[/red]")
                if os.path.exists(output_path):
                    os.remove(output_path)
                return False
                
    return True

def main():
    # Print clean and premium welcome banner
    console.print(Panel.fit(
        "     [bold cyan]NEXUS MODS RESEARCH DOWNLOADER[/bold cyan]     \n"
        " [dim]Automated Client for Modding Ecosystem Research[/dim]",
        border_style="cyan"
    ))

    # Retrieve API key
    api_key = get_api_key()

    # Create the HTTP session with headers
    session = Session()
    session.headers.update({
        "apikey": api_key,
        "User-Agent": USER_AGENT
    })

    # 1. Get the mod URL from the user
    url_input = ""
    while True:
        url_input = Prompt.ask("Enter the Nexus Mods page URL for your desired collection/mod").strip()
        
        if not url_input.startswith('http'):
            console.print("[red]Invalid input. Must be an absolute URL starting with http/https.[/red]")
            continue
        
        game_id, mod_id = parse_mod_url(url_input)
        if not game_id or not mod_id:
            console.print("[red]Could not parse Game ID or Mod ID from URL. Make sure it follows the standard pattern.[/red]")
            continue
            
        break

    console.print(f"\n[cyan]Target Game Domain:[/cyan] [bold]{game_id}[/bold]")
    console.print(f"[cyan]Target Mod ID:[/cyan]      [bold]{mod_id}[/bold]")
    
    # 2. Fetch mod details and display the research metadata profile
    with console.status("[cyan]Fetching mod details...[/cyan]") as status:
        mod_details = get_mod_info(session, game_id, mod_id)

    mod_name = mod_details.get("name", "Unknown Mod")
    author = mod_details.get("author", "Unknown Author")
    version = mod_details.get("version", "1.0.0")
    downloads = mod_details.get("downloads", 0)
    endorsements = mod_details.get("endorsement_count", 0)
    summary = mod_details.get("summary", "No summary provided.")

    # Render a premium mod summary box
    console.print(Panel(
        f"[bold white]{mod_name}[/bold white]\n"
        f"[dim]Author: {author} | Version: {version}[/dim]\n\n"
        f"[cyan]Downloads:[/cyan] {downloads:,} | [cyan]Endorsements:[/cyan] {endorsements:,}\n\n"
        f"[bold]Summary:[/bold]\n{summary}",
        title="Mod Metadata Profile",
        border_style="blue",
        box=ROUNDED
    ))

    # 3. Fetch mod files list
    with console.status("[cyan]Retrieving available files...[/cyan]") as status:
        files = fetch_file_info(session, game_id, mod_id)

    if not files:
        console.print("[yellow]No files found for this mod or files are hidden.[/yellow]")
        sys.exit(0)

    # 4. Display files in a gorgeous, informative table
    table = Table(title="Available Mod Files", box=ROUNDED, border_style="cyan")
    table.add_column("File ID", style="cyan", justify="right")
    table.add_column("Name", style="white")
    table.add_column("Version", style="magenta")
    table.add_column("Size", style="green", justify="right")
    table.add_column("Category", style="yellow")
    table.add_column("Description", style="dim", max_width=40)

    # Keep a map of file_id -> file object for quick retrieval
    file_map = {}
    for f in files:
        file_id = str(f.get("file_id"))
        file_name = f.get("name", "Unknown")
        file_version = f.get("version", "N/A")
        file_size_bytes = f.get("size_in_bytes", 0)
        file_size = format_size(file_size_bytes)
        category = f.get("category_name", "Main")
        description = f.get("description", "")
        # Strip HTML tags from description if any
        description = re.sub(r'<[^>]*>', '', description).strip()
        # Truncate description if too long
        if len(description) > 80:
            description = description[:77] + "..."

        table.add_row(
            file_id,
            file_name,
            file_version,
            file_size,
            category,
            description
        )
        file_map[file_id] = {
            "name": file_name,
            "size_in_bytes": file_size_bytes,
            "category": category
        }

    console.print(table)

    # 5. Let the user select files to download
    selected_ids = []
    while True:
        selection_input = Prompt.ask(
            "\nEnter File ID(s) to download (comma-separated, e.g. [cyan]10001,10002[/cyan]) or type '[bold yellow]all[/bold yellow]'"
        ).strip().lower()

        if selection_input == 'all':
            selected_ids = list(file_map.keys())
            break
        
        # Parse comma-separated list
        parts = [p.strip() for p in selection_input.split(",") if p.strip()]
        invalid_parts = [p for p in parts if p not in file_map]
        
        if not parts:
            console.print("[red]Please enter a valid selection.[/red]")
            continue
            
        if invalid_parts:
            console.print(f"[red]The following file ID(s) are invalid: {', '.join(invalid_parts)}[/red]")
            continue
            
        selected_ids = parts
        break

    # 6. Ask for the download directory
    current_dir = os.getcwd()
    default_download_dir = os.path.join(current_dir, "downloads")
    
    download_dir = Prompt.ask(
        f"\nSelect download directory",
        default=default_download_dir
    ).strip()

    # Create directory if it doesn't exist
    if not os.path.exists(download_dir):
        try:
            os.makedirs(download_dir, exist_ok=True)
            console.print(f"[green]Created directory: {download_dir}[/green]")
        except Exception as e:
            console.print(f"[red]Failed to create download directory: {e}[/red]")
            sys.exit(1)

    console.print(f"\n[bold yellow]Queueing {len(selected_ids)} file(s) for download...[/bold yellow]\n")

    # 7. Initialize pycurl and process download queue
    curl = pycurl.Curl() if HAS_PYCURL else None
    try:
        for idx, file_id in enumerate(selected_ids, start=1):
            file_meta = file_map[file_id]
            clean_filename = re.sub(r'[\\/*?:"<>|]', "_", file_meta["name"]) # make sure filename is safe for OS
            output_path = os.path.join(download_dir, clean_filename)
            
            console.print(f"[bold cyan][{idx}/{len(selected_ids)}][/bold cyan] Resolving download link for [bold]{file_meta['name']}[/bold]...")
            
            # Fetch download link
            download_url = get_download_link(session, game_id, mod_id, file_id)
            if not download_url:
                console.print(f"[red]Could not retrieve a download link for file ID {file_id}. Skipping.[/red]")
                continue
                
            console.print(f"[dim]Download URL resolved. Fetching data...[/dim]")
            
            # Download file using pycurl
            success = download_file(curl, download_url, output_path)
            if success:
                console.print(f"[bold green]✓ Downloaded successfully:[/bold green] [cyan]{clean_filename}[/cyan]\n")
            else:
                console.print(f"[bold red]✗ Download failed:[/bold red] [cyan]{clean_filename}[/cyan]\n")
                
    finally:
        if curl is not None:
            curl.close()

    console.print(Panel(
        "[bold green]All operations completed![/bold green]\n"
        f"Downloaded files are saved in: [cyan]{download_dir}[/cyan]",
        border_style="green",
        box=ROUNDED
    ))

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation cancelled by user.[/yellow]")
        sys.exit(0)
