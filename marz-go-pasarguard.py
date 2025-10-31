#!/usr/bin/env python3
"""
Marzban to Pasarguard Migration Tool (Data-Only Migration)
Version: 1.0.14 (Alembic-Compatible)
A tool to change database/phpMyAdmin ports and migrate data from Marzban to Pasarguard.
NOTE: This version assumes Pasarguard database schema is already created by Alembic.
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
from typing import Dict, Any, Optional
from dotenv import dotenv_values

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
    if not os.access(env_path, os.R_OK):
        raise PermissionError(f"No read permission for {env_path}")
    
    env = dotenv_values(env_path)
    if not env.get("SQLALCHEMY_DATABASE_URL"):
        raise ValueError(f"SQLALCHEMY_DATABASE_URL not found in {env_path}")
    return env

# Helper: Parse SQLALCHEMY_DATABASE_URL
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

# Helper: Get DB config from .env
def get_db_config(env_path: str, name: str) -> Dict[str, Any]:
    """Get database config from .env file."""
    try:
        env = load_env_file(env_path)
        sqlalchemy_url = env.get("SQLALCHEMY_DATABASE_URL")
        config = parse_sqlalchemy_url(sqlalchemy_url)
        config["charset"] = "utf8mb4"
        config["cursorclass"] = pymysql.cursors.DictCursor
        print(f"{CYAN}=== {name.upper()} DATABASE SETTINGS ==={RESET}")
        print(f"Using: host={config['host']}, port={config['port']}, user={config['user']}, db={config['db']}")
        return config
    except Exception as e:
        print(f"{RED}Error loading {name} config: {str(e)}{RESET}")
        sys.exit(1)

# Helper: Read xray_config.json
def read_xray_config() -> Dict[str, Any]:
    """Read xray_config.json from /var/lib/marzban."""
    if not os.path.exists(XRAY_CONFIG_PATH):
        raise FileNotFoundError(f"xray_config.json not found at {XRAY_CONFIG_PATH}")
    if not os.access(XRAY_CONFIG_PATH, os.R_OK):
        raise PermissionError(f"No read permission for {XRAY_CONFIG_PATH}")
    try:
        with open(XRAY_CONFIG_PATH, 'r', encoding='utf-8') as file:
            return json.load(file)
    except Exception as e:
        raise Exception(f"Error reading xray_config.json: {str(e)}")

# Connection
def connect(cfg: Dict[str, Any]):
    """Connect to database."""
    try:
        conn = pymysql.connect(**cfg)
        print(f"{GREEN}Connected to {cfg['db']}@{cfg['host']}:{cfg['port']} ✓{RESET}")
        time.sleep(0.5)
        return conn
    except Exception as e:
        raise Exception(f"Connection failed: {str(e)}")

# Migration: Admins (DATA ONLY)
def migrate_admins(marzban_conn, pasarguard_conn):
    """Migrate admins from Marzban to Pasarguard."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM admins")
        admins = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        # NOTE: CREATE TABLE check removed. Assumes 'admins' table exists (Alembic).
        for a in admins:
            cur.execute(
                """
                INSERT INTO admins
                (id, username, hashed_password, created_at, is_sudo,
                 password_reset_at, telegram_id, discord_webhook)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    username = %s,
                    hashed_password = %s,
                    created_at = %s,
                    is_sudo = %s,
                    password_reset_at = %s,
                    telegram_id = %s,
                    discord_webhook = %s
                """,
                (
                    a["id"], a["username"], a["hashed_password"],
                    a["created_at"], a["is_sudo"], a["password_reset_at"],
                    a["telegram_id"], a["discord_webhook"],
                    a["username"], a["hashed_password"],
                    a["created_at"], a["is_sudo"], a["password_reset_at"],
                    a["telegram_id"], a["discord_webhook"]
                ),
            )
    pasarguard_conn.commit()
    return len(admins)

# Migration: Default Group (DATA ONLY)
def ensure_default_group(pasarguard_conn):
    """Ensure default group (ID=1) exists in Pasarguard. (Pasarguard Alembic should create it)"""
    with pasarguard_conn.cursor() as cur:
        # NOTE: CREATE TABLE check removed. Assumes 'groups' table exists (Alembic).
        
        # Check if default group exists (If Pasarguard's initial migration failed to create it, we insert it)
        cur.execute("SELECT COUNT(*) AS cnt FROM groups WHERE id = 1")
        if cur.fetchone()["cnt"] == 0:
            cur.execute("INSERT INTO groups (id, name, is_disabled) VALUES (1, 'DefaultGroup', 0)")
            print(f"{GREEN}Created missing default group in Pasarguard (ID=1) ✓{RESET}")
            time.sleep(0.5)
        else:
            print(f"{YELLOW}Default group (ID=1) already exists (OK) ✓{RESET}")
    pasarguard_conn.commit()

# Migration: Default Core Config (DATA ONLY)
def ensure_default_core_config(pasarguard_conn):
    """Ensure default core config (ID=1) exists in Pasarguard. (Pasarguard Alembic should create it)"""
    with pasarguard_conn.cursor() as cur:
        # NOTE: CREATE TABLE check removed. Assumes 'core_configs' table exists (Alembic).
        
        # Check if default core config exists
        cur.execute("SELECT COUNT(*) AS cnt FROM core_configs WHERE id = 1")
        if cur.fetchone()["cnt"] == 0:
            # We insert a minimal default config if Pasarguard Alembic failed to do so.
            cfg = {
                "log": {"loglevel": "warning"},
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
                VALUES (1, NOW(), 'Default Pasarguard', %s, NULL, NULL)
                """,
                json.dumps(cfg),
            )
            print(f"{GREEN}Created missing default core config in Pasarguard (ID=1) ✓{RESET}")
            time.sleep(0.5)
        else:
            print(f"{YELLOW}Default core config (ID=1) already exists (OK) ✓{RESET}")
    pasarguard_conn.commit()

# Migration: Xray Config (DATA ONLY)
def migrate_xray_config(pasarguard_conn, xray_config):
    """Migrate Marzban's xray_config.json to Pasarguard's core_configs (ID=1)."""
    with pasarguard_conn.cursor() as cur:
        # NOTE: Backup is now less aggressive, but still useful for safety.
        # Backup existing core_config (using ID=2, as Pasarguard should reserve ID=1 for itself or the main config)
        cur.execute("SELECT * FROM core_configs WHERE id = 1")
        existing = cur.fetchone()
        
        if existing:
            # Check if a backup ID exists, if so, we update/insert the new Marzban config with ID=1.
            
            # 1. Insert Marzban's config as ID=1 (overwriting the default Pasarguard config for migration)
            # The core configuration of Marzban must replace the primary core config of Pasarguard (ID=1)
            # to keep the services running as before.
            cur.execute(
                """
                INSERT INTO core_configs
                (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
                VALUES (%s, NOW(), %s, %s, NULL, NULL)
                ON DUPLICATE KEY UPDATE
                    name = %s, config = %s, created_at = NOW()
                """,
                (1, "Marzban Migrated", json.dumps(xray_config), "Marzban Migrated", json.dumps(xray_config)),
            )
            print(f"{GREEN}Marzban's xray_config.json migrated as 'Marzban Migrated' (ID=1) ✓{RESET}")
            
            # 2. Check if a backup config (ID=2) exists. If ID=1 was the Pasarguard default, we back it up to ID=2.
            cur.execute("SELECT * FROM core_configs WHERE id = 2")
            if cur.fetchone() is None and existing["name"] != "Marzban Migrated":
                 cur.execute(
                    """
                    INSERT INTO core_configs
                    (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
                    VALUES (%s, NOW(), %s, %s, %s, %s)
                    """,
                    (
                        2,
                        "Backup_Pasarguard_Default",
                        existing["config"],
                        existing["exclude_inbound_tags"],
                        existing["fallbacks_inbound_tags"],
                    ),
                )
                 print(f"{GREEN}Backup of original Pasarguard config created as 'Backup_Pasarguard_Default' (ID=2) ✓{RESET}")
            
    pasarguard_conn.commit()
    return 1

# Migration: Inbounds (DATA ONLY)
def migrate_inbounds_and_associate(marzban_conn, pasarguard_conn):
    """Migrate inbounds and associate with default group."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM inbounds")
        inbounds = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        # NOTE: CREATE TABLE check removed. Assumes 'inbounds' and 'inbounds_groups_association' tables exist (Alembic).

        for i in inbounds:
            cur.execute(
                """
                INSERT INTO inbounds (id, tag)
                VALUES (%s,%s)
                ON DUPLICATE KEY UPDATE
                    tag = %s
                """,
                (i["id"], i["tag"], i["tag"])
            )
        for i in inbounds:
            # We assume the default group (ID=1) is present
            cur.execute(
                """
                INSERT IGNORE INTO inbounds_groups_association (inbound_id, group_id)
                VALUES (%s,1)
                """,
                (i["id"],)
            )
    pasarguard_conn.commit()
    return len(inbounds)

# Migration: Hosts (DATA ONLY)
def migrate_hosts(marzban_conn, pasarguard_conn, safe_alpn_func):
    """Migrate hosts with ALPN fix and default values for optional fields."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM hosts")
        hosts = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        # NOTE: CREATE TABLE check removed. Assumes 'hosts' table exists (Alembic).

        for h in hosts:
            cur.execute(
                """
                INSERT INTO hosts
                (id, remark, address, port, inbound_tag, sni, host, security, alpn,
                 fingerprint, allowinsecure, is_disabled, path, random_user_agent,
                 use_sni_as_host, priority, http_headers, transport_settings,
                 mux_settings, noise_settings, fragment_settings, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    remark = %s,
                    address = %s,
                    port = %s,
                    inbound_tag = %s,
                    sni = %s,
                    host = %s,
                    security = %s,
                    alpn = %s,
                    fingerprint = %s,
                    allowinsecure = %s,
                    is_disabled = %s,
                    path = %s,
                    random_user_agent = %s,
                    use_sni_as_host = %s,
                    priority = %s,
                    http_headers = %s,
                    transport_settings = %s,
                    mux_settings = %s,
                    noise_settings = %s,
                    fragment_settings = %s,
                    status = %s
                """,
                (
                    h["id"], h["remark"], h["address"], h["port"], h["inbound_tag"],
                    h["sni"], h["host"], h["security"], safe_alpn_func(h.get("alpn")),
                    h["fingerprint"], h["allowinsecure"], h["is_disabled"], h.get("path"),
                    h.get("random_user_agent", 0), h.get("use_sni_as_host", 0), h.get("priority", 0),
                    safe_json(h.get("http_headers")), safe_json(h.get("transport_settings")),
                    safe_json(h.get("mux_settings")), safe_json(h.get("noise_settings")),
                    safe_json(h.get("fragment_settings")), h.get("status"),
                    h["remark"], h["address"], h["port"], h["inbound_tag"],
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

# Migration: Nodes (DATA ONLY)
def migrate_nodes(marzban_conn, pasarguard_conn):
    """Migrate nodes."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM nodes")
        nodes = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        # NOTE: CREATE TABLE check removed. Assumes 'nodes' table exists (Alembic).

        for n in nodes:
            # Pasarguard node config should point to the main core config (ID=1)
            # which we have replaced with the Marzban config in migrate_xray_config.
            cur.execute(
                """
                INSERT INTO nodes
                (id, name, address, port, status, last_status_change, message,
                 created_at, uplink, downlink, xray_version, usage_coefficient,
                 node_version, connection_type, server_ca, keep_alive, max_logs,
                 core_config_id, gather_logs)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,1)
                ON DUPLICATE KEY UPDATE
                    name = %s,
                    address = %s,
                    port = %s,
                    status = %s,
                    last_status_change = %s,
                    message = %s,
                    created_at = %s,
                    uplink = %s,
                    downlink = %s,
                    xray_version = %s,
                    usage_coefficient = %s,
                    node_version = %s,
                    connection_type = %s,
                    server_ca = %s,
                    keep_alive = %s,
                    max_logs = %s,
                    core_config_id = 1,
                    gather_logs = 1
                """,
                (
                    n["id"], n["name"], n["address"], n["port"], n["status"],
                    n["last_status_change"], n["message"], n["created_at"],
                    n["uplink"], n["downlink"], n["xray_version"], n["usage_coefficient"],
                    n["node_version"], n["connection_type"], n.get("server_ca", ""),
                    n.get("keep_alive", 0), n.get("max_logs", 1000),
                    n["name"], n["address"], n["port"], n["status"],
                    n["last_status_change"], n["message"], n["created_at"],
                    n["uplink"], n["downlink"], n["xray_version"], n["usage_coefficient"],
                    n["node_version"], n["connection_type"], n.get("server_ca", ""),
                    n.get("keep_alive", 0), n.get("max_logs", 1000)
                ),
            )
    pasarguard_conn.commit()
    return len(nodes)

# Migration: Users and Proxies (DATA ONLY)
def migrate_users_and_proxies(marzban_conn, pasarguard_conn):
    """Migrate users and their proxy settings."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM users")
        users = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        # NOTE: CREATE TABLE check removed. Assumes 'users' table exists (Alembic).

        total = 0
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
                INSERT INTO users
                (id, username, status, used_traffic, data_limit, created_at,
                 admin_id, data_limit_reset_strategy, sub_revoked_at, note,
                 online_at, edit_at, on_hold_timeout, on_hold_expire_duration,
                 auto_delete_in_days, last_status_change, expire, proxy_settings)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    username = %s,
                    status = %s,
                    used_traffic = %s,
                    data_limit = %s,
                    created_at = %s,
                    admin_id = %s,
                    data_limit_reset_strategy = %s,
                    sub_revoked_at = %s,
                    note = %s,
                    online_at = %s,
                    edit_at = %s,
                    on_hold_timeout = %s,
                    on_hold_expire_duration = %s,
                    auto_delete_in_days = %s,
                    last_status_change = %s,
                    expire = %s,
                    proxy_settings = %s
                """,
                (
                    u["id"], u["username"], u["status"], used, u["data_limit"],
                    u["created_at"], u["admin_id"], u["data_limit_reset_strategy"],
                    u["sub_revoked_at"], u["note"], u["online_at"], u["edit_at"],
                    u["on_hold_timeout"], u["on_hold_expire_duration"], u["auto_delete_in_days"],
                    u["last_status_change"], expire_dt, json.dumps(proxy_cfg),
                    u["username"], u["status"], used, u["data_limit"],
                    u["created_at"], u["admin_id"], u["data_limit_reset_strategy"],
                    u["sub_revoked_at"], u["note"], u["online_at"], u["edit_at"],
                    u["on_hold_timeout"], u["on_hold_expire_duration"], u["auto_delete_in_days"],
                    u["last_status_change"], expire_dt, json.dumps(proxy_cfg)
                ),
            )
            total += 1

    pasarguard_conn.commit()
    return total

# Function to check and install dependencies (UNMODIFIED)
def check_dependencies():
    """Check and install required dependencies."""
    # ... (function body remains the same)
    print(f"{CYAN}Checking dependencies...{RESET}")
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

# Function to clear the screen (UNMODIFIED)
def clear_screen():
    """Clear the terminal screen."""
    os.system("clear")

# Function to display the menu (UNMODIFIED)
def display_menu():
    """Display the main menu with a styled header."""
    clear_screen()
    print(f"{CYAN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓")
    print(f"┃{YELLOW}          Power By: ASiSSK               {CYAN}┃")
    print(f"┃{YELLOW}          Marz ➜ Pasarguard              {CYAN}┃")
    print(f"┃{YELLOW}              v1.0.14                    {CYAN}┃")
    print(f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛{RESET}")
    print()
    print("Menu:")
    print("1. Change Database and phpMyAdmin Ports")
    print("2. Migrate Marzban to Pasarguard (Data Only)")
    print("3. Exit")
    print()

# Function to change database and phpMyAdmin ports (UNMODIFIED)
def change_db_port():
    """Change the database and phpMyAdmin ports in .env and docker-compose.yml files."""
    clear_screen()
    print(f"{CYAN}=== Change Database and phpMyAdmin Ports ==={RESET}")

    default_db_port = "3307"
    default_apache_port = "8020"
    db_port = input(f"Enter database port (Default: {default_db_port}): ").strip() or default_db_port
    apache_port = input(f"Enter phpMyAdmin APACHE_PORT (Default: {default_apache_port}): ").strip() or default_apache_port
    success = True

    try:
        # Validate ports
        for port, name in [(db_port, "Database port"), (apache_port, "phpMyAdmin APACHE_PORT")]:
            if not port.isdigit() or int(port) < 1 or int(port) > 65535:
                print(f"{RED}Error: Invalid {name}. Must be between 1 and 65535.{RESET}")
                success = False
                input("Press Enter to return to the menu...")
                return success

        # Update .env file
        env_file = PASARGUARD_ENV_PATH
        if os.path.exists(env_file):
            with open(env_file, 'r', encoding='utf-8') as file:
                content = file.read()
            content = re.sub(r'DB_PORT=\d+', f'DB_PORT={db_port}', content, 1) if re.search(r'DB_PORT=\d+', content) else content + f'\nDB_PORT={db_port}\n'
            content = re.sub(
                r'SQLALCHEMY_DATABASE_URL="mysql\+(asyncmy|pymysql)://[^:]+:[^@]+@127\.0\.0\.1:\d+/[^"]+"',
                lambda match: match.group(0).replace(
                    re.search(r':\d+/', match.group(0)).group(0),
                    f':{db_port}/'
                ),
                content
            )
            with open(env_file, 'w', encoding='utf-8') as file:
                file.write(content)
            print(f"{GREEN}Updated {env_file} with database port {db_port} ✓{RESET}")
            time.sleep(0.5)
        else:
            print(f"{RED}Error: File {env_file} not found.{RESET}")
            success = False

        # Update docker-compose.yml
        compose_file = DOCKER_COMPOSE_FILE_PATH
        if os.path.exists(compose_file):
            with open(compose_file, 'r', encoding='utf-8') as file:
                content = file.read()
            # Update database port
            if re.search(r'--port=\d+', content):
                content = re.sub(r'--port=\d+', f'--port={db_port}', content)
            else:
                content = re.sub(
                    r'(command:\n\s+- --bind-address=127\.0\.0\.1)',
                    f'command:\n      - --port={db_port}\n      - --bind-address=127.0.0.1',
                    content
                )
            if re.search(r'PMA_PORT: \d+', content):
                content = re.sub(r'PMA_PORT: \d+', f'PMA_PORT: {db_port}', content)
            else:
                content = re.sub(
                    r'(environment:\n\s+PMA_HOST: 127\.0\.0\.1)',
                    f'environment:\n      PMA_HOST: 127.0.0.1\n      PMA_PORT: {db_port}',
                    content
                )
            # Update phpMyAdmin APACHE_PORT
            if re.search(r'APACHE_PORT: \d+', content):
                content = re.sub(r'APACHE_PORT: \d+', f'APACHE_PORT: {apache_port}', content)
            else:
                content = re.sub(
                    r'(environment:\n\s+PMA_HOST: 127\.0\.0\.1\n\s+PMA_PORT: \d+)',
                    f'environment:\n      PMA_HOST: 127.0.0.1\n      PMA_PORT: {db_port}\n      APACHE_PORT: {apache_port}',
                    content
                )
            with open(compose_file, 'w', encoding='utf-8') as file:
                file.write(content)
            print(f"{GREEN}Updated {compose_file} with database port {db_port} and phpMyAdmin APACHE_PORT {apache_port} ✓{RESET}")
            time.sleep(0.5)
        else:
            print(f"{RED}Error: File {compose_file} not found.{RESET}")
            success = False

        if success:
            print(f"{GREEN}Port changes applied successfully! Please restart services:{RESET}")
            print(f"  docker restart pasarguard-pasarguard-1")
            print(f"  docker restart pasarguard-mariadb-1")
            print(f"  docker restart pasarguard-phpmyadmin-1")
        else:
            print(f"{RED}Error: Failed to apply changes.{RESET}")

    except Exception as e:
        print(f"{RED}Error: {str(e)}{RESET}")
        success = False

    input("Press Enter to return to the menu...")
    return success

# Function to check file access (UNMODIFIED)
def check_file_access():
    """Check access to .env and xray_config.json files."""
    # ... (function body remains the same)
    print(f"{CYAN}Checking file access...{RESET}")
    files = [
        (MARZBAN_ENV_PATH, "Marzban .env"),
        (PASARGUARD_ENV_PATH, "Pasarguard .env"),
        (XRAY_CONFIG_PATH, "xray_config.json"),
        (DOCKER_COMPOSE_FILE_PATH, "docker-compose.yml")
    ]
    success = True
    for file_path, file_name in files:
        if not os.path.exists(file_path):
            print(f"{RED}Error: {file_name} not found at {file_path}{RESET}")
            success = False
        elif not os.access(file_path, os.R_OK):
            print(f"{RED}Error: No read permission for {file_name} at {file_path}{RESET}")
            success = False
        else:
            print(f"{GREEN}Access to {file_name} OK ✓{RESET}")
            time.sleep(0.5)
    return success

# Function to migrate Marzban to Pasarguard (MODIFIED)
def migrate_marzban_to_pasarguard():
    """Migrate data from Marzban to Pasarguard. Assumes Pasarguard schema is ready."""
    clear_screen()
    print(f"{CYAN}=== Migrate Marzban to Pasarguard (Data Only) ==={RESET}")
    print(f"{YELLOW}WARNING: Ensure Pasarguard is installed and running, and its database schema is created by Alembic before running this option.{RESET}")

    # Check file access
    if not check_file_access():
        print(f"{RED}Error: File access issues detected. Please fix and try again.{RESET}")
        input("Press Enter to return to the menu...")
        return False

    try:
        print(f"Loading credentials from .env files...")
        marzban_config = get_db_config(MARZBAN_ENV_PATH, "Marzban")
        pasarguard_config = get_db_config(PASARGUARD_ENV_PATH, "Pasarguard")

        # Check if databases are the same
        if (marzban_config['host'] == pasarguard_config['host'] and
            marzban_config['port'] == pasarguard_config['port'] and
            marzban_config['db'] == pasarguard_config['db']):
            print(f"{RED}Error: Marzban and Pasarguard are using the same database. "
                  f"Please use option 1 to change the Pasarguard database port.{RESET}")
            input("Press Enter to return to the menu...")
            return False

        # Connect to databases
        print(f"{CYAN}Testing database connections...{RESET}")
        marzban_conn = connect(marzban_config)
        pasarguard_conn = connect(pasarguard_config)

        print(f"{CYAN}============================================================{RESET}")
        print(f"{CYAN}STARTING DATA MIGRATION (Alembic Schema assumed ready){RESET}")
        print(f"{CYAN}============================================================{RESET}")

        # 1. Migrate admins
        print("Migrating admins...")
        admin_count = migrate_admins(marzban_conn, pasarguard_conn)
        print(f"{GREEN}{admin_count} admin(s) migrated (data only) ✓{RESET}")
        time.sleep(0.5)

        # 2. Ensure default group (in case Alembic missed it or Pasarguard migration failed)
        print("Ensuring default group (ID=1) exists...")
        ensure_default_group(pasarguard_conn)
        time.sleep(0.5)

        # 3. Ensure default core config (in case Alembic missed it)
        print("Ensuring default core config (ID=1) exists...")
        ensure_default_core_config(pasarguard_conn)
        time.sleep(0.5)

        # 4. Migrate xray_config.json to core_configs (ID=1)
        print("Migrating xray_config.json to core_configs (ID=1)...")
        xray_config = read_xray_config()
        migrate_xray_config(pasarguard_conn, xray_config)
        print(f"{GREEN}xray_config.json migrated and old ID=1 backed up (ID=2) ✓{RESET}")
        time.sleep(0.5)

        # 5. Migrate inbounds
        print("Migrating inbounds and linking to default group (ID=1)...")
        inbound_count = migrate_inbounds_and_associate(marzban_conn, pasarguard_conn)
        print(f"{GREEN}{inbound_count} inbound(s) migrated and linked ✓{RESET}")
        time.sleep(0.5)

        # 6. Migrate hosts
        print("Migrating hosts...")
        host_count = migrate_hosts(marzban_conn, pasarguard_conn, safe_alpn)
        print(f"{GREEN}{host_count} host(s) migrated ✓{RESET}")
        time.sleep(0.5)

        # 7. Migrate nodes
        print("Migrating nodes (linked to core_config_id=1)...")
        node_count = migrate_nodes(marzban_conn, pasarguard_conn)
        print(f"{GREEN}{node_count} node(s) migrated ✓{RESET}")
        time.sleep(0.5)

        # 8. Migrate users and proxy settings
        print("Migrating users and proxy settings...")
        user_count = migrate_users_and_proxies(marzban_conn, pasarguard_conn)
        print(f"{GREEN}{user_count} user(s) migrated with proxy settings ✓{RESET}")
        time.sleep(0.5)

        print(f"{GREEN}MIGRATION COMPLETED SUCCESSFULLY! (v1.0.14) ✓{RESET}")
        print("ACTION REQUIRED:")
        print("  1. Check Pasarguard logs for any startup errors.")
        print("  2. Restart Pasarguard and Xray services:")
        print("  docker restart pasarguard-pasarguard-1")
        print("  docker restart xray")

    except Exception as e:
        print(f"{RED}Error during migration: {str(e)}{RESET}")
        input("Press Enter to return to the menu...")
        return False
    finally:
        if 'marzban_conn' in locals():
            marzban_conn.close()
        if 'pasarguard_conn' in locals():
            pasarguard_conn.close()

    input("Press Enter to return to the menu...")
    return True

# Main function (UNMODIFIED)
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
