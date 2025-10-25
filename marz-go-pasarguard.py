#!/usr/bin/env python3
"""
Marzban to Pasarguard Migration Menu
Version: 1.0.2
A tool to change database ports and migrate data from Marzban to Pasarguard.
"""

import re
import os
import subprocess
import sys
import time
from migration_utils import (
    load_env_file, parse_sqlalchemy_url, get_db_config, safe_alpn, safe_json,
    read_xray_config, connect, migrate_admins, ensure_default_group,
    migrate_xray_config, migrate_inbounds_and_associate, migrate_hosts,
    migrate_nodes, migrate_users_and_proxies
)

# ANSI color codes
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"

# Default paths
MARZBAN_ENV_PATH = "/opt/marzban/.env"
PASARGUARD_ENV_PATH = "/opt/pasarguard/.env"
DOCKER_COMPOSE_FILE_PATH = "/opt/pasarguard/docker-compose.yml"

# Function to check and install dependencies
def check_dependencies():
    """Check and install required dependencies."""
    print("Checking dependencies...")
    dependencies = {
        "screen": "screen",
        "python3": "python3",
        "pip": "python3-pip",
    }
    missing_deps = []

    for cmd, pkg in dependencies.items():
        result = subprocess.run(f"command -v {cmd}", shell=True, capture_output=True)
        if result.returncode != 0:
            missing_deps.append(pkg)

    if missing_deps:
        print(f"{RED}Missing dependencies: {', '.join(missing_deps)}{RESET}")
        print("Installing missing dependencies...")
        try:
            subprocess.run("apt-get update", shell=True, check=True)
            for pkg in missing_deps:
                subprocess.run(f"apt-get install -y {pkg}", shell=True, check=True)
            print(f"{GREEN}Dependencies installed successfully ✓{RESET}")
            time.sleep(0.5)
        except subprocess.CalledProcessError as e:
            print(f"{RED}Error installing dependencies: {e}{RESET}")
            sys.exit(1)

    # Check Python packages
    python_deps = ["pymysql", "python-dotenv"]
    for pkg in python_deps:
        result = subprocess.run(f"pip3 show {pkg}", shell=True, capture_output=True)
        if result.returncode != 0:
            print(f"Installing Python package: {pkg}")
            try:
                subprocess.run(f"pip3 install {pkg}", shell=True, check=True)
                print(f"{GREEN}Python package {pkg} installed ✓{RESET}")
                time.sleep(0.5)
            except subprocess.CalledProcessError as e:
                print(f"{RED}Error installing {pkg}: {e}{RESET}")
                sys.exit(1)
    print(f"{GREEN}All dependencies are installed ✓{RESET}")
    time.sleep(0.5)

# Function to clear the screen
def clear_screen():
    """Clear the terminal screen."""
    os.system("clear")

# Function to display the menu
def display_menu():
    """Display the main menu with a styled header."""
    clear_screen()
    print(f"{CYAN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓")
    print(f"┃{YELLOW}          Marz ➜ Pasarguard              {CYAN}┃")
    print(f"┃{YELLOW}              v1.0.2                     {CYAN}┃")
    print(f"┃{YELLOW}         Powered by: ASiS SK             {CYAN}┃")
    print(f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛{RESET}")
    print()
    print("Menu:")
    print("1. Change Database Port")
    print("2. Migrate Marzban to Pasarguard")
    print("3. Exit")
    print()

# Function to change database port
def change_db_port():
    """Change the database port in .env and docker-compose.yml files."""
    clear_screen()
    print(f"{CYAN}=== Change Database Port ==={RESET}")

    def get_port():
        default_port = "3307"
        port = input(f"What port do you want to use? (Default: {default_port}): ").strip()
        return port if port else default_port

    def update_env_file(port):
        try:
            with open(PASARGUARD_ENV_PATH, 'r', encoding='utf-8') as file:
                content = file.read()

            content = re.sub(r'DB_PORT=\d+', f'DB_PORT={port}', content)
            content = re.sub(
                r'SQLALCHEMY_DATABASE_URL="mysql\+asyncmy://[^:]+:[^@]+@[^:]+:\d+/[^"]+"',
                lambda match: match.group(0).replace(
                    re.search(r':\d+/', match.group(0)).group(0),
                    f':{port}/'
                ),
                content
            )

            with open(PASARGUARD_ENV_PATH, 'w', encoding='utf-8') as file:
                file.write(content)
            print(f"{GREEN}Updated {PASARGUARD_ENV_PATH} with port {port} ✓{RESET}")
            time.sleep(0.5)
            return True

        except FileNotFoundError:
            print(f"{RED}Error: File {PASARGUARD_ENV_PATH} not found.{RESET}")
            return False
        except Exception as e:
            print(f"{RED}Error updating {PASARGUARD_ENV_PATH}: {str(e)}{RESET}")
            return False

    def update_docker_compose_file(port):
        try:
            with open(DOCKER_COMPOSE_FILE_PATH, 'r', encoding='utf-8') as file:
                content = file.read()

            if re.search(r'--port=\d+', content):
                content = re.sub(r'--port=\d+', f'--port={port}', content)
            else:
                content = re.sub(
                    r'(command:\n\s+- --bind-address=[^\n]+)',
                    f'command:\n      - --port={port}\n      - --bind-address=127.0.0.1',
                    content
                )

            if re.search(r'PMA_PORT: \d+', content):
                content = re.sub(r'PMA_PORT: \d+', f'PMA_PORT: {port}', content)
            else:
                content = re.sub(
                    r'(environment:\n\s+PMA_HOST: [^\n]+)',
                    f'environment:\n      PMA_HOST: 127.0.0.1\n      PMA_PORT: {port}',
                    content
                )

            with open(DOCKER_COMPOSE_FILE_PATH, 'w', encoding='utf-8') as file:
                file.write(content)
            print(f"{GREEN}Updated {DOCKER_COMPOSE_FILE_PATH} with port {port} ✓{RESET}")
            time.sleep(0.5)
            return True

        except FileNotFoundError:
            print(f"{RED}Error: File {DOCKER_COMPOSE_FILE_PATH} not found.{RESET}")
            return False
        except Exception as e:
            print(f"{RED}Error updating {DOCKER_COMPOSE_FILE_PATH}: {str(e)}{RESET}")
            return False

    if not os.path.exists(os.path.dirname(PASARGUARD_ENV_PATH)):
        print(f"{RED}Error: Directory {os.path.dirname(PASARGUARD_ENV_PATH)} does not exist.{RESET}")
        input("Press Enter to return to the menu...")
        return

    port = get_port()

    update_success = True
    if not update_env_file(port):
        update_success = False
    if not update_docker_compose_file(port):
        update_success = False

    if update_success:
        print(f"{GREEN}Port changed to {port} successfully ✓{RESET}")
    else:
        print(f"{RED}Error: Failed to apply changes.{RESET}")

    input("Press Enter to return to the menu...")

# Function to migrate Marzban to Pasarguard
def migrate_marzban_to_pasarguard():
    """Migrate data from Marzban to Pasarguard."""
    clear_screen()
    print(f"{CYAN}=== Migrate Marzban to Pasarguard ==={RESET}")

    print("Loading credentials from .env files...")
    marzban_env = load_env_file(MARZBAN_ENV_PATH)
    MARZBAN_DB = get_db_config("Marzban", marzban_env, MARZBAN_ENV_PATH)
    pasarguard_env = load_env_file(PASARGUARD_ENV_PATH)
    PASARGUARD_DB = get_db_config("Pasarguard", pasarguard_env, PASARGUARD_ENV_PATH)

    marzban_conn = connect(MARZBAN_DB)
    pasarguard_conn = connect(PASARGUARD_DB)

    try:
        print("\n" + "="*60)
        print("STARTING MIGRATION")
        print("="*60)

        migrate_admins(marzban_conn, pasarguard_conn)
        time.sleep(0.5)
        ensure_default_group(pasarguard_conn)
        time.sleep(0.5)
        migrate_xray_config(pasarguard_conn)
        time.sleep(0.5)
        migrate_inbounds_and_associate(marzban_conn, pasarguard_conn)
        time.sleep(0.5)
        migrate_hosts(marzban_conn, pasarguard_conn)
        time.sleep(0.5)
        migrate_nodes(marzban_conn, pasarguard_conn)
        time.sleep(0.5)
        migrate_users_and_proxies(marzban_conn, pasarguard_conn)
        time.sleep(0.5)

        print(f"\n{GREEN}MIGRATION COMPLETED SUCCESSFULLY! ✓{RESET}")
        print("All data transferred safely.")
        print("ALPN 'none' → NULL (fixed automatically)")
        print("xray_config.json → core_configs as 'ASiS SK'")
        print("Restart Pasarguard & Xray now.")
        print("\nNext steps:")
        print("1. Run: docker restart pasarguard-pasarguard-1")
        print("2. Run: docker restart xray")
        print("3. Test user subscriptions at your Pasarguard panel URL")

    except Exception as e:
        print(f"{RED}ERROR: {e}{RESET}")
        sys.exit(1)
    finally:
        marzban_conn.close()
        pasarguard_conn.close()

    input("Press Enter to return to the menu...")

# Main menu loop
def main():
    """Main function to run the menu."""
    check_dependencies()
    while True:
        display_menu()
        choice = input("Enter your choice (1-3): ").strip()
        if choice == "1":
            change_db_port()
        elif choice == "2":
            migrate_marzban_to_pasarguard()
        elif choice == "3":
            clear_screen()
            print(f"{CYAN}Exiting... Goodbye!{RESET}")
            sys.exit(0)
        else:
            print(f"{RED}Invalid choice. Please enter 1, 2, or 3.{RESET}")
            input("Press Enter to continue...")

if __name__ == "__main__":
    main()
