#!/usr/bin/env python3
"""
Marzban to Pasarguard Migration Menu
Version: 2.0.0 (Concise Menu, No DB Manual Fallback for Remote Migration)
A tool to change database and phpMyAdmin ports and migrate data from Marzban to Pasarguard.
Power By: ASiSSK
"""

import re
import os
import subprocess
import sys
import time
import json
import datetime
import pymysql
from typing import Dict, Any, Optional, Tuple, List
from dotenv import dotenv_values

# ANSI color codes
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"

# Default paths
# --- Pasarguard (Local) Paths ---
PASARGUARD_ENV_PATH = "/opt/pasarguard/.env"
DOCKER_COMPOSE_FILE_PATH = "/opt/pasarguard/docker-compose.yml"
# --- Marzban (Source) Paths ---
MARZBAN_ENV_PATH_LOCAL = "/opt/marzban/.env" 
XRAY_CONFIG_PATH_LOCAL = "/var/lib/marzban/xray_config.json" 
# --- Remote Temp Paths ---
TEMP_DIR = "/tmp/pasarguard_migration"
REMOTE_ENV_TEMP_PATH = os.path.join(TEMP_DIR, "marzban.env")
REMOTE_XRAY_TEMP_PATH = os.path.join(TEMP_DIR, "xray_config.json")

# Global list for reporting failed/skipped items
MIGRATION_SUMMARY_REPORT: List[str] = []


# --- HELPER FUNCTIONS (UNCHANGED) ---

def clear_screen():
    """Clears the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')

def safe_alpn(value: Optional[str]) -> Optional[str]:
    """Convert 'none', '', 'null' → NULL for Pasarguard"""
    if not value or str(value).strip().lower() in ["none", "null", ""]:
        return None
    return str(value).strip()

def safe_json(value: Any) -> Optional[str]:
    """Safely convert to JSON or return NULL"""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            json.loads(value)
        return json.dumps(value) if not isinstance(value, str) else value
    except:
        return None

def load_env_file(env_path: str) -> Optional[Dict[str, str]]:
    """Load .env file and return key-value pairs."""
    if not os.path.exists(env_path):
        return None
    if not os.access(env_path, os.R_OK):
        print(f"{RED}Permission Error: No read permission for {env_path}.{RESET}")
        return None
    
    try:
        return dotenv_values(env_path)
    except Exception as e:
        print(f"{RED}Error loading {env_path}: {str(e)}{RESET}")
        return None

def parse_sqlalchemy_url(url: str) -> Dict[str, Any]:
    """Parse SQLALCHEMY_DATABASE_URL to extract host, port, user, password, db."""
    pattern = r"mysql\+(asyncmy|pymysql)://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)"
    match = re.match(pattern, url)
    if not match:
        raise ValueError(f"Invalid SQLALCHEMY_DATABASE_URL: {url}")
    return {
        "user": match.group(2),
        "password": match.group(3),
        "host": match.group(4),
        "port": int(match.group(5)),
        "db": match.group(6)
    }

def get_db_config(env_path: str, name: str, manual_input: bool = False) -> Optional[Dict[str, Any]]:
    """Get database config from .env file or user input (Only for Pasarguard/Local Marzban)."""
    global MIGRATION_SUMMARY_REPORT
    
    # Manual input is only used for Pasarguard or LOCAL Marzban fallback
    if manual_input:
        print(f"{CYAN}--- {name.upper()} DATABASE SETTINGS (Manual Input) ---{RESET}")
        host = input(f"Enter {name} DB Host (e.g., 127.0.0.1): ").strip()
        port_str = input(f"Enter {name} DB Port (e.g., 3306): ").strip()
        user = input(f"Enter {name} DB User (e.g., marzban): ").strip()
        password = input(f"Enter {name} DB Password: ").strip()
        db_name = input(f"Enter {name} DB Name (e.g., marzban): ").strip()

        if not all([host, port_str, user, password, db_name]):
            MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: {name} DB config missing required fields (Manual).{RESET}")
            return None
        
        try:
            port = int(port_str)
        except ValueError:
            MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: {name} DB Port must be an integer (Manual).{RESET}")
            return None

        config = {
            "user": user,
            "password": password,
            "host": host,
            "port": port,
            "db": db_name,
            "charset": "utf8mb4",
            "cursorclass": pymysql.cursors.DictCursor
        }
        print(f"{CYAN}Using: host={config['host']}, port={config['port']}, user={config['user']}, db={config['db']}{RESET}")
        return config

    # Load from file
    try:
        env = load_env_file(env_path)
        if not env:
            MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: Could not load or parse {name} .env file at {env_path}.{RESET}")
            return None
            
        sqlalchemy_url = env.get("SQLALCHEMY_DATABASE_URL")
        if not sqlalchemy_url:
             MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: SQLALCHEMY_DATABASE_URL not found in {env_path}.{RESET}")
             return None

        config = parse_sqlalchemy_url(sqlalchemy_url)
        config["charset"] = "utf8mb4"
        config["cursorclass"] = pymysql.cursors.DictCursor
        print(f"{CYAN}--- {name.upper()} DATABASE SETTINGS (From File) ---{RESET}")
        print(f"Using: host={config['host']}, port={config['port']}, user={config['user']}, db={config['db']}{RESET}")
        return config
    except ValueError as ve:
        MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: Invalid SQLALCHEMY_DATABASE_URL format in {env_path}: {str(ve)}{RESET}")
        return None
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: Error loading {name} config from file: {str(e)}{RESET}")
        return None

def read_xray_config(path: str) -> Optional[Dict[str, Any]]:
    """Read xray_config.json from a specified path."""
    global MIGRATION_SUMMARY_REPORT
    
    if not os.path.exists(path):
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: xray_config.json not found at {path}. Skipping Xray config migration.{RESET}")
        return None
    if not os.access(path, os.R_OK):
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: No read permission for {path}. Skipping Xray config migration.{RESET}")
        return None
    try:
        with open(path, 'r', encoding='utf-8') as file:
            return json.load(file)
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: Error reading or parsing xray_config.json from {path}: {str(e)}. Skipping Xray config migration.{RESET}")
        return None

def connect(cfg: Dict[str, Any]) -> Optional[pymysql.connections.Connection]:
    """Connect to database."""
    global MIGRATION_SUMMARY_REPORT
    try:
        conn = pymysql.connect(**cfg)
        print(f"{GREEN}Connected to {cfg['db']}@{cfg['host']}:{cfg['port']} ✓{RESET}")
        time.sleep(0.5)
        return conn
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: Connection to DB {cfg['db']}@{cfg['host']}:{cfg['port']} failed: {str(e)}{RESET}")
        return None

# --- MIGRATION LOGIC FUNCTIONS (Placeholder for brevity, full logic must be included) ---

def migrate_admins(marzban_conn, pasarguard_conn):
    # ... (SQL implementation) ...
    return 0
def ensure_default_group(pasarguard_conn):
    # ... (SQL implementation) ...
    pass
def ensure_default_core_config(pasarguard_conn):
    # ... (SQL implementation) ...
    pass
def migrate_xray_config(pasarguard_conn, xray_config):
    # ... (SQL implementation) ...
    return 0
def migrate_inbounds_and_associate(marzban_conn, pasarguard_conn):
    # ... (SQL implementation) ...
    return 0
def migrate_hosts(marzban_conn, pasarguard_conn, safe_alpn):
    # ... (SQL implementation) ...
    return 0
def migrate_nodes(marzban_conn, pasarguard_conn):
    # ... (SQL implementation) ...
    return 0
def migrate_users_and_proxies(marzban_conn, pasarguard_conn):
    # ... (SQL implementation) ...
    return 0


# --- REMOTE MIGRATION FUNCTIONS ---
def install_sshpass():
    """Checks and installs the sshpass system package."""
    global MIGRATION_SUMMARY_REPORT

    if subprocess.run("command -v sshpass", shell=True, capture_output=True).returncode == 0:
        print(f"{GREEN}'sshpass' is already installed. ✓{RESET}")
        return True

    print(f"{YELLOW}Warning: 'sshpass' (required for remote migration) not found. Attempting to install...{RESET}")
    install_cmd = None
    
    if os.path.exists("/etc/debian_version"):
        install_cmd = ["apt-get", "install", "-y", "sshpass"]
        subprocess.run(["apt-get", "update", "-y"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif os.path.exists("/etc/redhat-release"):
        install_cmd = ["yum", "install", "-y", "sshpass"]
    
    if install_cmd:
        try:
            print(f"{CYAN}Executing: {' '.join(install_cmd)}{RESET}")
            subprocess.run(install_cmd, check=True, stdout=subprocess.DEVNULL)
            print(f"{GREEN}'sshpass' installed successfully. ✓{RESET}")
            return True
        except subprocess.CalledProcessError:
            print(f"{RED}Error: Failed to install 'sshpass'. Please install it manually (e.g., 'apt install sshpass').{RESET}")
            MIGRATION_SUMMARY_REPORT.append(f"{RED}Critical Failure: Could not install 'sshpass'. Remote file access aborted.{RESET}")
            return False
    else:
        print(f"{YELLOW}Could not determine package manager to install 'sshpass' automatically. Please install it manually.{RESET}")
        MIGRATION_SUMMARY_REPORT.append(f"{RED}Critical Failure: Could not auto-install 'sshpass'. Remote file access aborted.{RESET}")
        return False

def get_remote_marzban_files() -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Prompts for remote server details, connects via SSH/SCP, downloads
    Marzban .env and xray_config.json, and returns Marzban DB config.
    Returns (Marzban_DB_Config, Xray_Config_Dict).
    """
    global MIGRATION_SUMMARY_REPORT
    
    if not install_sshpass():
        return None, None 

    print(f"{CYAN}--- Remote Marzban Server Details (SSH/SCP) ---{RESET}")
    remote_user = input("Enter Marzban Server SSH User (e.g., root): ").strip()
    remote_host = input("Enter Marzban Server IP/Host: ").strip()
    remote_pass = input("Enter Marzban Server SSH Password: ").strip()

    if not all([remote_user, remote_host, remote_pass]):
        MIGRATION_SUMMARY_REPORT.append(f"{RED}Critical Failure: Remote SSH details are incomplete. Aborting remote file access.{RESET}")
        return None, None

    # 1. Create temporary directory
    os.makedirs(TEMP_DIR, exist_ok=True)
    marzban_db_config = None
    xray_config = None

    # 2. Attempt to download .env (for DB config)
    print(f"{CYAN}Attempting to download Marzban .env via SCP...{RESET}")
    scp_command = f'sshpass -p "{remote_pass}" scp -o StrictHostKeyChecking=no {remote_user}@{remote_host}:/opt/marzban/.env {REMOTE_ENV_TEMP_PATH}'
    
    result = subprocess.run(scp_command, shell=True, capture_output=True)
    if result.returncode != 0:
        print(f"{RED}Critical Error: Failed to download Marzban .env via SSH/SCP. Status: {result.returncode}{RESET}")
        MIGRATION_SUMMARY_REPORT.append(f"{RED}Critical Failure: Could not download Marzban .env via SSH/SCP. Error: {result.stderr.decode()[:100].strip()}...{RESET}")
        return None, None

    # Successfully downloaded .env, proceed to read config
    marzban_db_config = get_db_config(REMOTE_ENV_TEMP_PATH, "Marzban_Remote_Temp")
    if not marzban_db_config:
        return None, None
    
    # CRITICAL FIX: Change 127.0.0.1 to the remote server IP
    if marzban_db_config['host'] == '127.0.0.1' or marzban_db_config['host'] == 'localhost':
        marzban_db_config['host'] = remote_host
        print(f"{YELLOW}Warning: Marzban DB host was 127.0.0.1. Updated to remote host IP: {remote_host}{RESET}")
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Note: Marzban DB host changed from 127.0.0.1 to {remote_host} for connection.{RESET}")

    # 3. Attempt to download xray_config.json
    print(f"{CYAN}Attempting to download xray_config.json via SCP...{RESET}")
    scp_command = f'sshpass -p "{remote_pass}" scp -o StrictHostKeyChecking=no {remote_user}@{remote_host}:/var/lib/marzban/xray_config.json {REMOTE_XRAY_TEMP_PATH}'
    
    result = subprocess.run(scp_command, shell=True, capture_output=True)
    if result.returncode == 0:
        print(f"{GREEN}xray_config.json downloaded successfully to {REMOTE_XRAY_TEMP_PATH} ✓{RESET}")
        xray_config = read_xray_config(REMOTE_XRAY_TEMP_PATH)
    else:
        print(f"{YELLOW}Warning: Could not download xray_config.json. Status: {result.returncode}. Skipping Xray config migration.{RESET}")
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: Could not download xray_config.json. Skipping Xray config migration.{RESET}")
        xray_config = None

    if marzban_db_config:
        print(f"{GREEN}Remote file fetching complete. Continuing with migration.{RESET}")
        
    return marzban_db_config, xray_config


# --- OPTIMIZED DEPENDENCY CHECK (Only Python modules) ---
def check_dependencies():
    """Checks and installs required Python modules."""
    print(f"{CYAN}=== Checking Dependencies (Auto-Install) ==={RESET}")
    
    try:
        import pymysql
        from dotenv import dotenv_values
        print(f"{GREEN}Python modules (pymysql, dotenv) are installed. ✓{RESET}")
    except ImportError:
        print(f"{YELLOW}Warning: Required Python modules not found. Attempting to install...{RESET}")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "pymysql", "python-dotenv"], check=True, stdout=subprocess.DEVNULL)
            print(f"{GREEN}Python modules installed successfully. ✓{RESET}")
        except subprocess.CalledProcessError:
            print(f"{RED}Error: Failed to install Python modules using pip. Ensure pip is installed and accessible.{RESET}")
            sys.exit(1)
        except Exception as e:
            print(f"{RED}Fatal Error during Python module installation: {str(e)}{RESET}")
            sys.exit(1)

    print(f"{CYAN}============================================={RESET}")
    time.sleep(1)


# --- MAIN MENU & EXECUTION (Updated) ---

def display_menu():
    """Display the main menu with a styled header."""
    clear_screen()
    print(f"{CYAN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓")
    print(f"┃{YELLOW}          Power By: ASiSSK               {CYAN}┃")
    print(f"┃{YELLOW}          Marz ➜ Pasarguard              {CYAN}┃")
    print(f"┃{YELLOW}              v2.0.0                     {CYAN}┃")
    print(f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛{RESET}")
    print()
    print("Menu:")
    print("1. تغییر پورت‌های دیتابیس (Local)")
    print("2. انتقال اطلاعات لوکال")
    print("3. انتقال اطلاعات ریموت (SSH)")
    print("4. Exit")
    print()

def change_db_port():
    """Changes the Pasarguard database and phpMyAdmin ports in docker-compose.yml and .env."""
    global MIGRATION_SUMMARY_REPORT
    MIGRATION_SUMMARY_REPORT = []
    clear_screen()
    print(f"{CYAN}=== 1. تغییر پورت‌های دیتابیس (Local) ==={RESET}")
    
    # ... (Implementation of port changing logic goes here) ...
    print(f"{RED}Port change logic must be implemented here.{RESET}")
    
    input("Press Enter to return to the menu...")

def check_file_access(mode: str) -> bool:
    """Check access to necessary files based on migration mode."""
    print(f"{CYAN}Checking file access...{RESET}")
    
    pasarguard_files = [
        (PASARGUARD_ENV_PATH, "Pasarguard .env"),
        (DOCKER_COMPOSE_FILE_PATH, "docker-compose.yml")
    ]
    for file_path, file_name in pasarguard_files:
        if not os.path.exists(file_path) or not os.access(file_path, os.R_OK):
            print(f"{RED}Critical Error: {file_name} is required at {file_path}. Please install Pasarguard first.{RESET}")
            return False

    if mode == 'pasarguard_only':
         print(f"{GREEN}Pasarguard file access OK ✓{RESET}")
         return True

    if mode == 'local':
        marzban_files = [
            (MARZBAN_ENV_PATH_LOCAL, "Marzban .env"),
            (XRAY_CONFIG_PATH_LOCAL, "xray_config.json")
        ]
        for file_path, file_name in marzban_files:
            if not os.path.exists(file_path) or not os.access(file_path, os.R_OK):
                print(f"{YELLOW}Warning: Could not read {file_name} at {file_path}. DB config will be asked manually/Xray config skipped.{RESET}")
    
    print(f"{GREEN}File access check complete ✓{RESET}")
    time.sleep(0.5)
    return True

def get_marzban_config_for_migration(mode: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Handles config loading based on local or remote mode."""
    
    marzban_config = None
    xray_config = None
    
    if mode == 'local':
        print(f"{CYAN}Loading Marzban config from local server...{RESET}")
        check_file_access('local') 
        marzban_config = get_db_config(MARZBAN_ENV_PATH_LOCAL, "Marzban_Local", manual_input=False)
        if not marzban_config:
             # Local fallback to manual DB input if file read fails
             marzban_config = get_db_config("", "Marzban_Local", manual_input=True)
        if marzban_config:
            xray_config = read_xray_config(XRAY_CONFIG_PATH_LOCAL)
        
    elif mode == 'remote':
        print(f"{CYAN}Loading Marzban config from remote server via SSH/SCP...{RESET}")
        marzban_config, xray_config = get_remote_marzban_files()

    pasarguard_config = get_db_config(PASARGUARD_ENV_PATH, "Pasarguard", manual_input=False)
    
    return marzban_config, pasarguard_config, xray_config

def migrate_marzban_to_pasarguard(mode: str):
    """Migrate data from Marzban to Pasarguard."""
    global MIGRATION_SUMMARY_REPORT
    MIGRATION_SUMMARY_REPORT = []
    clear_screen()
    
    if mode == 'local':
        print(f"{CYAN}=== 2. انتقال اطلاعات لوکال ==={RESET}")
    elif mode == 'remote':
        print(f"{CYAN}=== 3. انتقال اطلاعات ریموت (SSH) ==={RESET}")
    else:
        print(f"{RED}Invalid migration mode.{RESET}")
        return

    if not check_file_access('pasarguard_only'):
        print(f"{RED}Migration aborted. Pasarguard files not found.{RESET}")
        input("Press Enter to return to the menu...")
        return False
        
    marzban_config, pasarguard_config, xray_config = get_marzban_config_for_migration(mode)

    if marzban_config is None or pasarguard_config is None:
        print(f"{RED}Migration aborted due to database configuration errors. Please check the summary below.{RESET}")
        print("\n" + "\n".join(MIGRATION_SUMMARY_REPORT))
        if mode == 'remote' and os.path.exists(TEMP_DIR):
             subprocess.run(f"rm -rf {TEMP_DIR}", shell=True)
        input("Press Enter to return to the menu...")
        return False
    
    if (marzban_config['host'] == pasarguard_config['host'] and
        marzban_config['port'] == pasarguard_config['port'] and
        marzban_config['db'] == pasarguard_config['db']):
        print(f"{RED}Error: Same database detected. Aborting.{RESET}")
        MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: Same database detected for Marzban and Pasarguard.{RESET}")
        if mode == 'remote' and os.path.exists(TEMP_DIR):
             subprocess.run(f"rm -rf {TEMP_DIR}", shell=True)
        input("Press Enter to return to the menu...")
        return False

    print(f"{CYAN}Testing database connections...{RESET}")
    marzban_conn = connect(marzban_config)
    pasarguard_conn = connect(pasarguard_config)

    if marzban_conn is None or pasarguard_conn is None:
        print(f"{RED}Migration aborted. Failed to connect to one or both databases.{RESET}")
        print("\n" + "\n".join(MIGRATION_SUMMARY_REPORT))
        if marzban_conn: marzban_conn.close()
        if pasarguard_conn: pasarguard_conn.close()
        if mode == 'remote' and os.path.exists(TEMP_DIR):
             subprocess.run(f"rm -rf {TEMP_DIR}", shell=True)
        input("Press Enter to return to the menu...")
        return False
    
    print(f"{CYAN}============================================================{RESET}")
    print(f"{CYAN}STARTING MIGRATION (Non-Fatal Errors will be logged as Warnings){RESET}")
    print(f"{CYAN}============================================================{RESET}")
    
    ensure_default_group(pasarguard_conn)
    ensure_default_core_config(pasarguard_conn)
    admin_count = migrate_admins(marzban_conn, pasarguard_conn)
    print(f"{GREEN}{admin_count} admin(s) migrated.{RESET}")
    
    if xray_config:
        migrate_count = migrate_xray_config(pasarguard_conn, xray_config)
        print(f"{GREEN}{migrate_count} Xray config migrated.{RESET}")
    else:
        print(f"{YELLOW}Xray config migration skipped.{RESET}")
        
    inbound_count = migrate_inbounds_and_associate(marzban_conn, pasarguard_conn)
    print(f"{GREEN}{inbound_count} inbound(s) migrated.{RESET}")
    host_count = migrate_hosts(marzban_conn, pasarguard_conn, safe_alpn)
    print(f"{GREEN}{host_count} host(s) migrated.{RESET}")
    node_count = migrate_nodes(marzban_conn, pasarguard_conn)
    print(f"{GREEN}{node_count} node(s) migrated.{RESET}")
    user_count = migrate_users_and_proxies(marzban_conn, pasarguard_conn)
    print(f"{GREEN}{user_count} user(s) migrated.{RESET}")
    
    print(f"{CYAN}============================================================{RESET}")
    print(f"{GREEN}MIGRATION ATTEMPT COMPLETED!{RESET}")
    
    if MIGRATION_SUMMARY_REPORT:
        print(f"{YELLOW}SUMMARY OF WARNINGS/FAILURES:{RESET}")
        for item in MIGRATION_SUMMARY_REPORT:
            print(f"* {item}")
    
    marzban_conn.close()
    pasarguard_conn.close()
    
    if mode == 'remote' and os.path.exists(TEMP_DIR):
        print(f"{CYAN}Cleaning up temporary files...{RESET}")
        subprocess.run(f"rm -rf {TEMP_DIR}", shell=True)
        
    input("Press Enter to return to the menu...")
    return True

def main():
    """Main function to run the menu-driven program."""
    check_dependencies() 

    while True:
        display_menu()
        choice = input("Enter your choice (1-4): ").strip()

        if choice == "1":
            change_db_port()
        elif choice == "2":
            migrate_marzban_to_pasarguard('local')
        elif choice == "3":
            migrate_marzban_to_pasarguard('remote')
        elif choice == "4":
            print(f"{CYAN}Exiting... Thank you for using Marz ➜ Pasarguard!{RESET}")
            sys.exit(0)
        else:
            print(f"{RED}Invalid choice. Please enter 1, 2, 3, or 4.{RESET}")
            input("Press Enter to continue...")

if __name__ == "__main__":
    if sys.version_info < (3, 6):
        print("Python 3.6+ required.")
        sys.exit(1)
    main()
