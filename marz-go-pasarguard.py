#!/usr/bin/env python3
"""
Marzban to Pasarguard Migration Menu
Version: 1.0.4
A tool to change database ports and migrate data from Marzban to Pasarguard.
"""

import re
import os
import subprocess
import sys
import time
import json
import datetime
import pymysql
from typing import Dict, Any, Optional
from dotenv import load_dotenv

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
XRAY_CONFIG_PATH = "/var/lib/marzban/xray_config.json"

# Helper: Safe ALPN cleaner
def safe_alpn(value: Optional[str]) -> Optional[str]:
    """Convert 'none', '', 'null' → NULL for Pasarguard"""
    if not value or str(value).strip().lower() in ["none", "null", ""]:
        return None
    return str(value).strip()

# Helper: Safe JSON conversion
def safe_json(value: Any) -> Optional[str]:
    """Safely convert to JSON or return NULL"""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            json.loads(value)  # validate
        return json.dumps(value) if not isinstance(value, str) else value
    except:
        return None

# Helper: Load .env file
def load_env_file(env_path: str) -> Dict[str, str]:
    """Load .env file and return key-value pairs."""
    if not os.path.exists(env_path):
        raise FileNotFoundError(f"Environment file {env_path} not found")
    load_dotenv(env_path)
    return dict(os.environ)

# Helper: Parse SQLALCHEMY_DATABASE_URL
def parse_sqlalchemy_url(url: str) -> Dict[str, Any]:
    """Parse SQLALCHEMY_DATABASE_URL to extract host, port, user, password, db."""
    # Updated regex to handle special characters in password
    pattern = r"mysql\+(asyncmy|pymysql)://([^:]+):([^@]*)@([^:]+):(\d+)/(.+)"
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

# Helper: Get DB config from .env
def get_db_config(env_path: str) -> Dict[str, Any]:
    """Get database config from .env file."""
    try:
        env = load_env_file(env_path)
        sqlalchemy_url = env.get("SQLALCHEMY_DATABASE_URL")
        if not sqlalchemy_url:
            raise ValueError(f"SQLALCHEMY_DATABASE_URL not found in {env_path}")
        config = parse_sqlalchemy_url(sqlalchemy_url)
        config["charset"] = "utf8mb4"
        config["cursorclass"] = pymysql.cursors.DictCursor
        return config
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Error: {str(e)}. Please ensure {env_path} exists.")
    except ValueError as e:
        raise ValueError(f"Error: {str(e)}. Please check SQLALCHEMY_DATABASE_URL format in {env_path}.")

# Helper: Read xray_config.json
def read_xray_config() -> Dict[str, Any]:
    """Read xray_config.json from /var/lib/marzban."""
    try:
        with open(XRAY_CONFIG_PATH, 'r', encoding='utf-8') as file:
            return json.load(file)
    except FileNotFoundError:
        raise FileNotFoundError(f"xray_config.json not found at {XRAY_CONFIG_PATH}")
    except Exception as e:
        raise Exception(f"Error reading xray_config.json: {str(e)}")

# Connection
def connect(cfg: Dict[str, Any]):
    """Connect to database."""
    try:
        conn = pymysql.connect(**cfg)
        return conn
    except Exception as e:
        raise Exception(f"Connection failed: {str(e)}. Please check database credentials and ensure the database is running.")

# Migration: Admins
def migrate_admins(marzban_conn, pasarguard_conn):
    """Migrate admins from Marzban to Pasarguard."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM admins")
        admins = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        for a in admins:
            cur.execute(
                """
                INSERT IGNORE INTO admins
                (id, username, hashed_password, created_at, is_sudo,
                 password_reset_at, telegram_id, discord_webhook, used_traffic, is_disabled)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0)
                """,
                (
                    a["id"], a["username"], a["hashed_password"],
                    a["created_at"], a["is_sudo"], a["password_reset_at"],
                    a["telegram_id"], a["discord_webhook"], a.get("users_usage", 0)
                ),
            )
    pasarguard_conn.commit()
    return len(admins)

# Migration: Default Group
def ensure_default_group(pasarguard_conn):
    """Ensure default group exists in Pasarguard."""
    with pasarguard_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM groups")
        if cur.fetchone()["cnt"] == 0:
            cur.execute("INSERT INTO groups (id, name, is_disabled) VALUES (1,'DefaultGroup',0)")
    pasarguard_conn.commit()

# Migration: Default Core Config
def ensure_default_core_config(pasarguard_conn):
    """Ensure default core config exists in Pasarguard."""
    with pasarguard_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM core_configs")
        if cur.fetchone()["cnt"] == 0:
            cfg = {
                "log": {"loglevel": "warning"},
                "inbounds": [{
                    "tag": "Shadowsocks TCP",
                    "listen": "0.0.0.0",
                    "port": 1080,
                    "protocol": "shadowsocks",
                    "settings": {"clients": [], "network": "tcp,udp"}
                }],
                "outbounds": [
                    {"protocol": "freedom", "tag": "DIRECT"},
                    {"protocol": "blackhole", "tag": "BLOCK"}
                ],
                "routing": {"rules": [{"ip": ["geoip:private"], "outboundTag": "BLOCK", "type": "field"}]}
            }
            cur.execute(
                """
                INSERT INTO core_configs
                (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
                VALUES (1, NOW(), 'Default Core Config', %s, '', '')
                """,
                json.dumps(cfg),
            )
    pasarguard_conn.commit()

# Migration: Xray Config
def migrate_xray_config(pasarguard_conn, xray_config):
    """Migrate xray_config.json to core_configs."""
    with pasarguard_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core_configs
            (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
            VALUES (%s, NOW(), %s, %s, '', '')
            """,
            (1001, "Backup_Default_Core_Config", json.dumps(xray_config)),
        )
        cur.execute(
            """
            INSERT INTO core_configs
            (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
            VALUES (%s, NOW(), %s, %s, '', '')
            """,
            (1002, "ASiS SK", json.dumps(xray_config)),
        )
    pasarguard_conn.commit()
    return 1001

# Migration: Inbounds
def migrate_inbounds_and_associate(marzban_conn, pasarguard_conn):
    """Migrate inbounds and associate with default group."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM inbounds")
        inbounds = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        for i in inbounds:
            cur.execute("INSERT IGNORE INTO inbounds (id, tag) VALUES (%s,%s)", (i["id"], i["tag"]))
        for i in inbounds:
            cur.execute("INSERT IGNORE INTO inbounds_groups_association (inbound_id, group_id) VALUES (%s,1)", (i["id"],))
    pasarguard_conn.commit()
    return len(inbounds)

# Migration: Hosts
def migrate_hosts(marzban_conn, pasarguard_conn, safe_alpn_func):
    """Migrate hosts with ALPN fix."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM hosts")
        hosts = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        for h in hosts:
            cur.execute(
                """
                INSERT IGNORE INTO hosts
                (id, remark, address, port, inbound_tag, sni, host, security, alpn,
                 fingerprint, allowinsecure, is_disabled, path, random_user_agent,
                 use_sni_as_host, priority, http_headers, transport_settings,
                 mux_settings, noise_settings, fragment_settings, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    h["id"], h["remark"], h["address"], h["port"], h["inbound_tag"],
                    h["sni"], h["host"], h["security"], safe_alpn_func(h.get("alpn")),
                    h["fingerprint"], h["allowinsecure"], h["is_disabled"], h.get("path"),
                    h.get("random_user_agent", 0), h.get("use_sni_as_host", 0), h.get("priority", 0),
                    safe_json(h.get("http_headers")), safe_json(h.get("transport_settings")),
                    safe_json(h.get("mux_settings")), safe_json(h.get("noise_settings")),
                    safe_json(h.get("fragment_settings")), h.get("status")
                ),
            )
    pasarguard_conn.commit()
    return len(hosts)

# Migration: Nodes
def migrate_nodes(marzban_conn, pasarguard_conn):
    """Migrate nodes."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM nodes")
        nodes = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        for n in nodes:
            cur.execute(
                """
                INSERT IGNORE INTO nodes
                (id, name, address, port, status, last_status_change, message,
                 created_at, uplink, downlink, xray_version, usage_coefficient,
                 node_version, connection_type, server_ca, keep_alive, max_logs,
                 core_config_id, gather_logs)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,1)
                """,
                (
                    n["id"], n["name"], n["address"], n["port"], n["status"],
                    n["last_status_change"], n["message"], n["created_at"],
                    n["uplink"], n["downlink"], n["xray_version"], n["usage_coefficient"],
                    n["node_version"], n["connection_type"], n.get("server_ca", ""),
                    n.get("keep_alive", 0), n.get("max_logs", 1000)
                ),
            )
    pasarguard_conn.commit()
    return len(nodes)

# Migration: Users and Proxies
def migrate_users_and_proxies(marzban_conn, pasarguard_conn):
    """Migrate users and their proxy settings."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM users")
        users = cur.fetchall()

    total = 0
    with pasarguard_conn.cursor() as cur:
        for u in users:
            with marzban_conn.cursor() as pcur:
                pcur.execute("SELECT * FROM proxies WHERE user_id = %s", (u["id"],))
                proxies = pcur.fetchall()

            proxy_cfg = {}
            for p in proxies:
                s = json.loads(p["settings"])
                typ = p["type"].lower()
                if typ == "vmess":
                    proxy_cfg["vmess"] = {"id": s.get("id")}
                elif typ == "vless":
                    proxy_cfg["vless"] = {"id": s.get("id"), "flow": s.get("flow", "")}
                elif typ == "trojan":
                    proxy_cfg["trojan"] = {"password": s.get("password")}
                elif typ == "shadowsocks":
                    proxy_cfg["shadowsocks"] = {"password": s.get("password"), "method": s.get("method")}

            expire_dt = None
            if u["expire"]:
                try:
                    expire_dt = datetime.datetime.fromtimestamp(u["expire"])
                except:
                    pass

            used = u["used_traffic"] or 0

            cur.execute(
                """
                INSERT IGNORE INTO users
                (id, username, status, used_traffic, data_limit, created_at,
                 admin_id, data_limit_reset_strategy, sub_revoked_at, note,
                 online_at, edit_at, on_hold_timeout, on_hold_expire_duration,
                 auto_delete_in_days, last_status_change, expire, proxy_settings)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    u["id"], u["username"], u["status"], used, u["data_limit"],
                    u["created_at"], u["admin_id"], u["data_limit_reset_strategy"],
                    u["sub_revoked_at"], u["note"], u["online_at"], u["edit_at"],
                    u["on_hold_timeout"], u["on_hold_expire_duration"], u["auto_delete_in_days"],
                    u["last_status_change"], expire_dt, json.dumps(proxy_cfg)
                ),
            )
            total += 1

    pasarguard_conn.commit()
    return total

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
    print(f"┃{YELLOW}              v1.0.4                     {CYAN}┃")
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

    default_port = "3307"
    port = input(f"What port do you want to use? (Default: {default_port}): ").strip() or default_port
    success = True

    try:
        # Validate port
        if not port.isdigit() or int(port) < 1 or int(port) > 65535:
            print(f"{RED}Error: Invalid port number. Must be between 1 and 65535.{RESET}")
            success = False
            input("Press Enter to return to the menu...")
            return success

        # Update .env file
        env_file = PASARGUARD_ENV_PATH
        if os.path.exists(env_file):
            with open(env_file, 'r', encoding='utf-8') as file:
                content = file.read()
            content = re.sub(r'DB_PORT=\d+', f'DB_PORT={port}', content)
            content = re.sub(
                r'SQLALCHEMY_DATABASE_URL="mysql\+(asyncmy|pymysql)://[^:]+:[^@]*@127\.0\.0\.1:\d+/[^"]+"',
                lambda match: match.group(0).replace(
                    re.search(r':\d+/', match.group(0)).group(0),
                    f':{port}/'
                ),
                content
            )
            with open(env_file, 'w', encoding='utf-8') as file:
                file.write(content)
            print(f"{GREEN}Updated {env_file} with port {port} ✓{RESET}")
            time.sleep(0.5)
        else:
            print(f"{RED}Error: File {env_file} not found.{RESET}")
            success = False

        # Update docker-compose.yml
        compose_file = DOCKER_COMPOSE_FILE_PATH
        if os.path.exists(compose_file):
            with open(compose_file, 'r', encoding='utf-8') as file:
                content = file.read()
            if re.search(r'--port=\d+', content):
                content = re.sub(r'--port=\d+', f'--port={port}', content)
            else:
                content = re.sub(
                    r'(command:\n\s+- --bind-address=127\.0\.0\.1)',
                    f'command:\n      - --port={port}\n      - --bind-address=127.0.0.1',
                    content
                )
            if re.search(r'PMA_PORT: \d+', content):
                content = re.sub(r'PMA_PORT: \d+', f'PMA_PORT: {port}', content)
            else:
                content = re.sub(
                    r'(environment:\n\s+PMA_HOST: 127\.0\.0\.1)',
                    f'environment:\n      PMA_HOST: 127.0.0.1\n      PMA_PORT: {port}',
                    content
                )
            with open(compose_file, 'w', encoding='utf-8') as file:
                file.write(content)
            print(f"{GREEN}Updated {compose_file} with port {port} ✓{RESET}")
            time.sleep(0.5)
        else:
            print(f"{RED}Error: File {compose_file} not found.{RESET}")
            success = False

        if success:
            print(f"{GREEN}Port change applied successfully! ✓{RESET}")
        else:
            print(f"{RED}Error: Failed to apply changes.{RESET}")

    except Exception as e:
        print(f"{RED}Error: {str(e)}{RESET}")
        success = False

    input("Press Enter to return to the menu...")
    return success

# Function to migrate Marzban to Pasarguard
def migrate_marzban_to_pasarguard():
    """Migrate data from Marzban to Pasarguard."""
    clear_screen()
    print(f"{CYAN}=== Migrate Marzban to Pasarguard ==={RESET}")

    marzban_conn = None
    pasarguard_conn = None
    try:
        print("Loading credentials from .env files...")
        marzban_config = get_db_config(MARZBAN_ENV_PATH)
        pasarguard_config = get_db_config(PASARGUARD_ENV_PATH)

        print(f"{CYAN}=== MARZBAN DATABASE SETTINGS ==={RESET}")
        print(f"Using: host={marzban_config['host']}, port={marzban_config['port']}, "
              f"user={marzban_config['user']}, db={marzban_config['db']}")
        print(f"{CYAN}=== PASARGUARD DATABASE SETTINGS ==={RESET}")
        print(f"Using: host={pasarguard_config['host']}, port={pasarguard_config['port']}, "
              f"user={pasarguard_config['user']}, db={pasarguard_config['db']}")

        # Connect to databases
        marzban_conn = connect(marzban_config)
        pasarguard_conn = connect(pasarguard_config)
        print(f"{GREEN}Connected to marzban@{marzban_config['host']}:{marzban_config['port']} ✓{RESET}")
        time.sleep(0.5)
        print(f"{GREEN}Connected to pasarguard@{pasarguard_config['host']}:{pasarguard_config['port']} ✓{RESET}")
        time.sleep(0.5)

        print(f"{CYAN}============================================================{RESET}")
        print(f"{CYAN}STARTING MIGRATION{RESET}")
        print(f"{CYAN}============================================================{RESET}")

        # Migrate admins
        print("Migrating admins...")
        admin_count = migrate_admins(marzban_conn, pasarguard_conn)
        print(f"{GREEN}{admin_count} admin(s) migrated ✓{RESET}")
        time.sleep(0.5)

        # Ensure default group
        print("Creating default group if not exists...")
        ensure_default_group(pasarguard_conn)
        print(f"{GREEN}Default group already exists ✓{RESET}")
        time.sleep(0.5)

        # Ensure default core config
        print("Creating default core config if not exists...")
        ensure_default_core_config(pasarguard_conn)
        print(f"{GREEN}Default core config already exists ✓{RESET}")
        time.sleep(0.5)

        # Migrate xray_config.json
        print("Migrating xray_config.json to core_configs...")
        xray_config = read_xray_config()
        print(f"{GREEN}Successfully read {XRAY_CONFIG_PATH} ✓{RESET}")
        time.sleep(0.5)
        print("Backing up existing core_config...")
        backup_id = migrate_xray_config(pasarguard_conn, xray_config)
        print(f"{GREEN}Backup created as 'Backup_Default_Core_Config' with ID {backup_id} ✓{RESET}")
        time.sleep(0.5)
        print(f"{GREEN}xray_config.json migrated as 'ASiS SK' in core_configs ✓{RESET}")
        time.sleep(0.5)

        # Migrate inbounds
        print("Migrating inbounds...")
        inbound_count = migrate_inbounds_and_associate(marzban_conn, pasarguard_conn)
        print(f"{GREEN}{inbound_count} inbound(s) migrated and linked ✓{RESET}")
        time.sleep(0.5)

        # Migrate hosts
        print("Migrating hosts (with smart ALPN fix)...")
        host_count = migrate_hosts(marzban_conn, pasarguard_conn, safe_alpn)
        print(f"{GREEN}{host_count} host(s) migrated (ALPN fixed) ✓{RESET}")
        time.sleep(0.5)

        # Migrate nodes
        print("Migrating nodes...")
        node_count = migrate_nodes(marzban_conn, pasarguard_conn)
        print(f"{GREEN}{node_count} node(s) migrated ✓{RESET}")
        time.sleep(0.5)

        # Migrate users and proxy settings
        print("Migrating users and proxy settings...")
        user_count = migrate_users_and_proxies(marzban_conn, pasarguard_conn)
        print(f"{GREEN}{user_count} user(s) migrated with proxy settings ✓{RESET}")
        time.sleep(0.5)

        print(f"{GREEN}MIGRATION COMPLETED SUCCESSFULLY! ✓{RESET}")

    except Exception as e:
        print(f"{RED}Error during migration: {str(e)}{RESET}")
        input("Press Enter to return to the menu...")
        return False
    finally:
        if marzban_conn:
            marzban_conn.close()
        if pasarguard_conn:
            pasarguard_conn.close()

    input("Press Enter to return to the menu...")
    return True

# Main function
def main():
    """Main function to run the menu-driven program."""
    check_dependencies()

    while True:
        display_menu()
        choice = input("Enter your choice (1-3): ").strip()

        if choice == "1":
            change_db_port()
        elif choice == "2":
            migrate_marzban_to_pasarguard()
        elif choice == "3":
            print(f"{CYAN}Exiting... Thank you for using Marz ➜ Pasarguard!{RESET}")
            sys.exit(0)
        else:
            print(f"{RED}Invalid choice. Please enter 1, 2, or 3.{RESET}")
            input("Press Enter to continue...")

if __name__ == "__main__":
    if sys.version_info < (3, 6):
        print("Python 3.6+ required.")
        sys.exit(1)
    main()
