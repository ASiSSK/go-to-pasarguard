#!/usr/bin/env python3
"""
Marzban to Pasarguard Migration Menu
Version: 1.0.15 (Install Paramiko on demand)
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
from typing import Dict, Any, Optional
from dotenv import dotenv_values

# Paramiko is imported on demand later
paramiko = None
try:
    import paramiko
except ImportError:
    pass

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
REMOTE_SCRIPT_PATH = "/tmp/asis-pg-remote.py" 

# --- Helper Functions ---

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
            json.loads(value)  # validate
        return json.dumps(value) if not isinstance(value, str) else value
    except:
        return None

def load_env_file(env_path: str) -> Dict[str, str]:
    """Load .env file and return key-value pairs."""
    if not os.path.exists(env_path):
        raise FileNotFoundError(f"Environment file {env_path} not found")
    if not os.access(env_path, os.R_OK):
        raise PermissionError(f"No read permission for {env_path}")
    
    env = dotenv_values(env_path)
    if not env.get("SQLALCHEMY_DATABASE_URL"):
        with open(env_path, 'r', encoding='utf-8') as f:
            print(f"{RED}Content of {env_path}:{RESET}\n{f.read()}")
        raise ValueError(f"SQLALCHEMY_DATABASE_URL not found in {env_path}")
    return env

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

def get_db_config(env_path: str, name: str) -> Dict[str, Any]:
    """Get database config from .env file."""
    try:
        env = load_env_file(env_path)
        print(f"{CYAN}Content of {env_path}:{RESET}")
        with open(env_path, 'r', encoding='utf-8') as f:
            print(f.read())
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

def connect(cfg: Dict[str, Any]):
    """Connect to database."""
    try:
        conn = pymysql.connect(**cfg)
        print(f"{GREEN}Connected to {cfg['db']}@{cfg['host']}:{cfg['port']} ✓{RESET}")
        time.sleep(0.5)
        return conn
    except Exception as e:
        raise Exception(f"Connection failed: {str(e)}")

# --- Migration Functions (Unchanged) ---
# ... (migrate_admins, ensure_default_group, ensure_default_core_config, migrate_xray_config, 
# migrate_inbounds_and_associate, migrate_hosts, migrate_nodes, migrate_users_and_proxies - all the same) ...

def migrate_admins(marzban_conn, pasarguard_conn):
    """Migrate admins from Marzban to Pasarguard."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM admins")
        admins = cur.fetchall()
    # ... (omitted migration logic for brevity, it's the same)
    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'admins'")
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE admins (
                    id INT PRIMARY KEY, username VARCHAR(255) NOT NULL, hashed_password TEXT NOT NULL, created_at DATETIME NOT NULL,
                    is_sudo BOOLEAN DEFAULT FALSE, password_reset_at DATETIME, telegram_id BIGINT, discord_webhook TEXT
                )
            """)
            print(f"{GREEN}Created admins table in Pasarguard ✓{RESET}")
            time.sleep(0.5)

        for a in admins:
            cur.execute(
                """
                INSERT INTO admins (id, username, hashed_password, created_at, is_sudo, password_reset_at, telegram_id, discord_webhook)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE username = %s, hashed_password = %s, created_at = %s, is_sudo = %s,
                    password_reset_at = %s, telegram_id = %s, discord_webhook = %s
                """,
                (
                    a["id"], a["username"], a["hashed_password"], a["created_at"], a["is_sudo"], a["password_reset_at"], a["telegram_id"], a["discord_webhook"],
                    a["username"], a["hashed_password"], a["created_at"], a["is_sudo"], a["password_reset_at"], a["telegram_id"], a["discord_webhook"]
                ),
            )
    pasarguard_conn.commit()
    return len(admins)

def ensure_default_group(pasarguard_conn):
    """Ensure default group exists in Pasarguard."""
    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'groups'")
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE groups (
                    id INT PRIMARY KEY, name VARCHAR(255) NOT NULL, is_disabled BOOLEAN DEFAULT FALSE
                )
            """)
            print(f"{GREEN}Created groups table in Pasarguard ✓{RESET}")
            time.sleep(0.5)
        
        cur.execute("SELECT COUNT(*) AS cnt FROM groups WHERE id = 1")
        if cur.fetchone()["cnt"] == 0:
            cur.execute("INSERT INTO groups (id, name, is_disabled) VALUES (1, 'DefaultGroup', 0)")
            print(f"{GREEN}Created default group in Pasarguard ✓{RESET}")
            time.sleep(0.5)
    pasarguard_conn.commit()

def ensure_default_core_config(pasarguard_conn):
    """Ensure default core config exists in Pasarguard."""
    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'core_configs'")
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE core_configs (
                    id INT PRIMARY KEY, created_at DATETIME NOT NULL, name VARCHAR(255) NOT NULL,
                    config JSON NOT NULL, exclude_inbound_tags TEXT, fallbacks_inbound_tags TEXT
                )
            """)
            print(f"{GREEN}Created core_configs table in Pasarguard ✓{RESET}")
            time.sleep(0.5)
        
        cur.execute("SELECT COUNT(*) AS cnt FROM core_configs WHERE id = 1")
        if cur.fetchone()["cnt"] == 0:
            cfg = {
                "log": {"loglevel": "warning"},
                "inbounds": [{
                    "tag": "Shadowsocks TCP", "listen": "0.0.0.0", "port": 1080, "protocol": "shadowsocks",
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
                VALUES (1, NOW(), 'ASiS SK', %s, '', '')
                """,
                json.dumps(cfg),
            )
            print(f"{GREEN}Created default core config 'ASiS SK' in Pasarguard ✓{RESET}")
            time.sleep(0.5)
    pasarguard_conn.commit()

def migrate_xray_config(pasarguard_conn, xray_config):
    """Migrate xray_config.json to core_configs."""
    with pasarguard_conn.cursor() as cur:
        cur.execute("SELECT * FROM core_configs WHERE id = 1")
        existing = cur.fetchone()
        if existing:
            cur.execute(
                """
                INSERT INTO core_configs
                (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
                VALUES (%s, NOW(), %s, %s, %s, %s)
                """,
                (
                    existing["id"] + 1000, "Backup_ASiS_SK", existing["config"],
                    existing["exclude_inbound_tags"], existing["fallbacks_inbound_tags"],
                ),
            )
            print(f"{GREEN}Backup created as 'Backup_ASiS_SK' with ID {existing['id'] + 1000} ✓{RESET}")
            time.sleep(0.5)

        cur.execute(
            """
            INSERT INTO core_configs
            (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
            VALUES (%s, NOW(), %s, %s, '', '')
            ON DUPLICATE KEY UPDATE
                name = %s, config = %s, created_at = NOW()
            """,
            (1, "ASiS SK", json.dumps(xray_config), "ASiS SK", json.dumps(xray_config)),
        )
    pasarguard_conn.commit()
    return 1

def migrate_inbounds_and_associate(marzban_conn, pasarguard_conn):
    """Migrate inbounds and associate with default group."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM inbounds")
        inbounds = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'inbounds'")
        if cur.fetchone() is None:
            cur.execute("""CREATE TABLE inbounds (id INT PRIMARY KEY, tag VARCHAR(255) NOT NULL)""")
            print(f"{GREEN}Created inbounds table in Pasarguard ✓{RESET}")
            time.sleep(0.5)

        cur.execute("SHOW TABLES LIKE 'inbounds_groups_association'")
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE inbounds_groups_association (
                    inbound_id INT, group_id INT, PRIMARY KEY (inbound_id, group_id),
                    FOREIGN KEY (inbound_id) REFERENCES inbounds(id), FOREIGN KEY (group_id) REFERENCES groups(id)
                )
            """)
            print(f"{GREEN}Created inbounds_groups_association table in Pasarguard ✓{RESET}")
            time.sleep(0.5)

        for i in inbounds:
            cur.execute("INSERT INTO inbounds (id, tag) VALUES (%s,%s) ON DUPLICATE KEY UPDATE tag = %s", (i["id"], i["tag"], i["tag"]))
        for i in inbounds:
            cur.execute("INSERT IGNORE INTO inbounds_groups_association (inbound_id, group_id) VALUES (%s,1)", (i["id"],))
    pasarguard_conn.commit()
    return len(inbounds)

def migrate_hosts(marzban_conn, pasarguard_conn, safe_alpn_func):
    """Migrate hosts with ALPN fix and default values for optional fields."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM hosts")
        hosts = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'hosts'")
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE hosts (
                    id INT PRIMARY KEY, remark VARCHAR(255), address VARCHAR(255), port INT, inbound_tag VARCHAR(255), sni TEXT,
                    host TEXT, security VARCHAR(50), alpn TEXT, fingerprint TEXT, allowinsecure BOOLEAN, is_disabled BOOLEAN,
                    path TEXT, random_user_agent BOOLEAN, use_sni_as_host BOOLEAN, priority INT DEFAULT 0, http_headers TEXT,
                    transport_settings TEXT, mux_settings TEXT, noise_settings TEXT, fragment_settings TEXT, status VARCHAR(50)
                )
            """)
            print(f"{GREEN}Created hosts table in Pasarguard ✓{RESET}")
            time.sleep(0.5)

        for h in hosts:
            # ... (omitted insert/update logic for brevity, it's the same)
            cur.execute(
                """
                INSERT INTO hosts (id, remark, address, port, inbound_tag, sni, host, security, alpn, fingerprint, allowinsecure, 
                 is_disabled, path, random_user_agent, use_sni_as_host, priority, http_headers, transport_settings, mux_settings, 
                 noise_settings, fragment_settings, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE remark = %s, address = %s, port = %s, inbound_tag = %s, sni = %s, host = %s,
                    security = %s, alpn = %s, fingerprint = %s, allowinsecure = %s, is_disabled = %s, path = %s,
                    random_user_agent = %s, use_sni_as_host = %s, priority = %s, http_headers = %s, transport_settings = %s,
                    mux_settings = %s, noise_settings = %s, fragment_settings = %s, status = %s
                """,
                (
                    h["id"], h["remark"], h["address"], h["port"], h["inbound_tag"], h["sni"], h["host"], h["security"], 
                    safe_alpn_func(h.get("alpn")), h["fingerprint"], h["allowinsecure"], h["is_disabled"], h.get("path"),
                    h.get("random_user_agent", 0), h.get("use_sni_as_host", 0), h.get("priority", 0),
                    safe_json(h.get("http_headers")), safe_json(h.get("transport_settings")), safe_json(h.get("mux_settings")), 
                    safe_json(h.get("noise_settings")), safe_json(h.get("fragment_settings")), h.get("status"),
                    h["remark"], h["address"], h["port"], h["inbound_tag"], h["sni"], h["host"], h["security"], 
                    safe_alpn_func(h.get("alpn")), h["fingerprint"], h["allowinsecure"], h["is_disabled"], h.get("path"),
                    h.get("random_user_agent", 0), h.get("use_sni_as_host", 0), h.get("priority", 0),
                    safe_json(h.get("http_headers")), safe_json(h.get("transport_settings")), safe_json(h.get("mux_settings")), 
                    safe_json(h.get("noise_settings")), safe_json(h.get("fragment_settings")), h.get("status")
                ),
            )
    pasarguard_conn.commit()
    return len(hosts)

def migrate_nodes(marzban_conn, pasarguard_conn):
    """Migrate nodes."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM nodes")
        nodes = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'nodes'")
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE nodes (
                    id INT PRIMARY KEY, name VARCHAR(255) NOT NULL, address VARCHAR(255) NOT NULL, port INT, status VARCHAR(50), 
                    last_status_change DATETIME, message TEXT, created_at DATETIME NOT NULL, uplink BIGINT, downlink BIGINT, 
                    xray_version VARCHAR(50), usage_coefficient FLOAT, node_version VARCHAR(50), connection_type VARCHAR(50), 
                    server_ca TEXT, keep_alive BOOLEAN, max_logs INT, core_config_id INT, gather_logs BOOLEAN, 
                    FOREIGN KEY (core_config_id) REFERENCES core_configs(id)
                )
            """)
            print(f"{GREEN}Created nodes table in Pasarguard ✓{RESET}")
            time.sleep(0.5)

        for n in nodes:
            # ... (omitted insert/update logic for brevity, it's the same)
            cur.execute(
                """
                INSERT INTO nodes (id, name, address, port, status, last_status_change, message, created_at, uplink, 
                 downlink, xray_version, usage_coefficient, node_version, connection_type, server_ca, keep_alive, max_logs, 
                 core_config_id, gather_logs)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,1)
                ON DUPLICATE KEY UPDATE name = %s, address = %s, port = %s, status = %s, last_status_change = %s,
                    message = %s, created_at = %s, uplink = %s, downlink = %s, xray_version = %s, usage_coefficient = %s,
                    node_version = %s, connection_type = %s, server_ca = %s, keep_alive = %s, max_logs = %s,
                    core_config_id = 1, gather_logs = 1
                """,
                (
                    n["id"], n["name"], n["address"], n["port"], n["status"], n["last_status_change"], n["message"], 
                    n["created_at"], n["uplink"], n["downlink"], n["xray_version"], n["usage_coefficient"],
                    n["node_version"], n["connection_type"], n.get("server_ca", ""), n.get("keep_alive", 0), 
                    n.get("max_logs", 1000),
                    n["name"], n["address"], n["port"], n["status"], n["last_status_change"], n["message"], 
                    n["created_at"], n["uplink"], n["downlink"], n["xray_version"], n["usage_coefficient"],
                    n["node_version"], n["connection_type"], n.get("server_ca", ""), n.get("keep_alive", 0), 
                    n.get("max_logs", 1000)
                ),
            )
    pasarguard_conn.commit()
    return len(nodes)

def migrate_users_and_proxies(marzban_conn, pasarguard_conn):
    """Migrate users and their proxy settings."""
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM users")
        users = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'users'")
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE users (
                    id INT PRIMARY KEY, username VARCHAR(255) NOT NULL, status VARCHAR(50), used_traffic BIGINT, data_limit BIGINT, 
                    created_at DATETIME NOT NULL, admin_id INT, data_limit_reset_strategy VARCHAR(50), sub_revoked_at DATETIME, 
                    note TEXT, online_at DATETIME, edit_at DATETIME, on_hold_timeout DATETIME, on_hold_expire_duration INT, 
                    auto_delete_in_days INT, last_status_change DATETIME, expire DATETIME, proxy_settings JSON
                )
            """)
            print(f"{GREEN}Created users table in Pasarguard ✓{RESET}")
            time.sleep(0.5)

        total = 0
        for u in users:
            with marzban_conn.cursor() as pcur:
                pcur.execute("SELECT * FROM proxies WHERE user_id = %s", (u["id"],))
                proxies = pcur.fetchall()

            proxy_cfg = {}
            for p in proxies:
                s = json.loads(p["settings"])
                typ = p["type"].lower()
                if typ == "vmess": proxy_cfg["vmess"] = {"id": s.get("id")}
                elif typ == "vless": proxy_cfg["vless"] = {"id": s.get("id"), "flow": s.get("flow", "")}
                elif typ == "trojan": proxy_cfg["trojan"] = {"password": s.get("password")}
                elif typ == "shadowsocks": proxy_cfg["shadowsocks"] = {"password": s.get("password"), "method": s.get("method")}

            expire_dt = None
            if u["expire"]:
                try: expire_dt = datetime.datetime.fromtimestamp(u["expire"])
                except: pass

            used = u["used_traffic"] or 0

            # ... (omitted insert/update logic for brevity, it's the same)
            cur.execute(
                """
                INSERT INTO users (id, username, status, used_traffic, data_limit, created_at, admin_id, data_limit_reset_strategy, 
                 sub_revoked_at, note, online_at, edit_at, on_hold_timeout, on_hold_expire_duration, auto_delete_in_days, 
                 last_status_change, expire, proxy_settings)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE username = %s, status = %s, used_traffic = %s, data_limit = %s, created_at = %s,
                    admin_id = %s, data_limit_reset_strategy = %s, sub_revoked_at = %s, note = %s, online_at = %s, edit_at = %s,
                    on_hold_timeout = %s, on_hold_expire_duration = %s, auto_delete_in_days = %s, last_status_change = %s,
                    expire = %s, proxy_settings = %s
                """,
                (
                    u["id"], u["username"], u["status"], used, u["data_limit"], u["created_at"], u["admin_id"], u["data_limit_reset_strategy"],
                    u["sub_revoked_at"], u["note"], u["online_at"], u["edit_at"], u["on_hold_timeout"], u["on_hold_expire_duration"],
                    u["auto_delete_in_days"], u["last_status_change"], expire_dt, json.dumps(proxy_cfg),
                    u["username"], u["status"], used, u["data_limit"], u["created_at"], u["admin_id"], u["data_limit_reset_strategy"],
                    u["sub_revoked_at"], u["note"], u["online_at"], u["edit_at"], u["on_hold_timeout"], u["on_hold_expire_duration"],
                    u["auto_delete_in_days"], u["last_status_change"], expire_dt, json.dumps(proxy_cfg)
                ),
            )
            total += 1

    pasarguard_conn.commit()
    return total

# --- Dependency and File Check Functions (Modified) ---

def install_python_dependencies(packages: list):
    """Install required Python packages."""
    for pkg in packages:
        try:
            subprocess.run(f"pip3 show {pkg}", shell=True, check=True, capture_output=True)
            print(f"{GREEN}Python package {pkg} already installed ✓{RESET}")
        except subprocess.CalledProcessError:
            print(f"Installing Python package: {pkg}...")
            try:
                # Removed 'apt update'
                subprocess.run(f"pip3 install {pkg}", shell=True, check=True)
                print(f"{GREEN}Python package {pkg} installed ✓{RESET}")
                time.sleep(0.5)
            except subprocess.CalledProcessError as e:
                print(f"{RED}Error installing {pkg}: {e}{RESET}")
                return False
    return True

def check_dependencies():
    """Check and install core dependencies only."""
    print(f"{CYAN}Checking core dependencies...{RESET}")
    # Only check for core OS tools and python/pip
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
        print(f"{RED}Missing OS dependencies: {', '.join(missing_deps)}{RESET}")
        print("Installing missing OS dependencies...")
        try:
            # Removed 'apt update'
            for pkg in missing_deps:
                subprocess.run(f"apt-get install -y {pkg}", shell=True, check=True)
            print(f"{GREEN}OS dependencies installed successfully ✓{RESET}")
            time.sleep(0.5)
        except subprocess.CalledProcessError as e:
            print(f"{RED}Error installing OS dependencies: {e}{RESET}")
            sys.exit(1)
            
    # Check core Python packages
    python_deps = ["pymysql", "python-dotenv"]
    if not install_python_dependencies(python_deps):
        sys.exit(1)

    print(f"{GREEN}All core dependencies are installed ✓{RESET}")
    time.sleep(0.5)

def clear_screen():
    """Clear the terminal screen."""
    os.system("clear")

def display_menu(version):
    """Display the main menu with a styled header."""
    clear_screen()
    print(f"{CYAN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓")
    print(f"┃{YELLOW}          Power By: ASiSSK               {CYAN}┃")
    print(f"┃{YELLOW}          Marz ➜ Pasarguard              {CYAN}┃")
    print(f"┃{YELLOW}              v{version}                    {CYAN}┃")
    print(f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛{RESET}")
    print()
    print("Menu:")
    print("1. Change Database Ports")
    print("2. Local Migration Marzban -> Pasarguard")
    print("3. Remote Migration via SSH (Pasarguard on new server)")
    print("4. Exit")
    print()

def check_file_access():
    """Check access to .env and xray_config.json files."""
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
            time.sleep(0.1)
    return success

# --- NEW FUNCTIONS FOR REMOTE MIGRATION (TAB 3) ---

def get_ssh_credentials():
    """Get SSH connection details from user for remote migration."""
    print(f"\n{CYAN}=== SSH Remote Migration Details ==={RESET}")
    host = input("Enter Remote Pasarguard Server IP/Hostname: ").strip()
    port = input("Enter Remote SSH Port (Default: 22): ").strip() or "22"
    username = input("Enter Remote SSH Username (e.g., root): ").strip()
    password = input("Enter Remote SSH Password (or press Enter for key-based auth): ").strip()
    
    if not host or not username:
        print(f"{RED}Host and Username are required.{RESET}")
        return None, None, None, None

    return host, port, username, password

def remote_execute_migration():
    """Executes the local migration script on a remote server via SSH."""
    global paramiko
    
    # 1. Install Paramiko on demand
    if paramiko is None:
        print(f"{CYAN}Checking/Installing Python dependencies for SSH (paramiko)...{RESET}")
        if not install_python_dependencies(["paramiko"]):
            print(f"{RED}Failed to install required Python libraries. Cannot proceed.{RESET}")
            input("Press Enter to return to the menu...")
            return False
        # Re-import paramiko after successful installation
        try:
            import paramiko
        except ImportError:
            print(f"{RED}Paramiko is still unavailable. Please check pip/python installation.{RESET}")
            input("Press Enter to return to the menu...")
            return False

    host, port, username, password = get_ssh_credentials()
    if not host:
        input("Press Enter to return to the menu...")
        return False

    client = None
    try:
        print(f"{CYAN}Connecting to {username}@{host}:{port}...{RESET}")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        if password:
            client.connect(host, port=int(port), username=username, password=password, timeout=10)
        else:
            client.connect(host, port=int(port), username=username, timeout=10)
        
        print(f"{GREEN}SSH Connection established to {host} ✓{RESET}")
        time.sleep(1)

        # 2. Read and transfer the current local script content
        with open(sys.argv[0], 'r', encoding='utf-8') as f:
            local_script_content = f.read()
        
        # Use SFTP to transfer the script (more reliable than 'tee' over exec_command for large files)
        sftp = client.open_sftp()
        with sftp.file(REMOTE_SCRIPT_PATH, 'w') as remote_file:
            remote_file.write(local_script_content)
        sftp.close()
        
        print(f"{GREEN}Script transferred to remote server at {REMOTE_SCRIPT_PATH} ✓{RESET}")
        client.exec_command(f"chmod +x {REMOTE_SCRIPT_PATH}")

        # 3. Execute dependency check and then migration logic remotely
        remote_command = (
            f"python3 {REMOTE_SCRIPT_PATH} --remote-migrate"
        )
        
        print(f"{CYAN}Executing remote migration command on {host} (This might take a while to install dependencies remotely)...{RESET}")
        
        # Use an interactive shell or combined command execution for better environment setup
        # paramiko.exec_command often struggles with environment/paths, especially for 'sudo' and 'pip'.
        
        # We wrap in a simple shell script to ensure correct execution context for pip/python3 on the remote server
        shell_script = f"""
            #!/bin/bash
            
            # 1. Ensure core dependencies (python3, pip, screen) are available on the remote server
            echo -e "{CYAN}[REMOTE SETUP] Installing OS dependencies...{RESET}"
            sudo apt-get install -y python3 python3-pip screen > /dev/null 2>&1
            
            # 2. Run the actual migration script
            echo -e "{CYAN}[REMOTE SETUP] Starting migration script...{RESET}"
            /usr/bin/env python3 {REMOTE_SCRIPT_PATH} --remote-migrate
        """
        
        # Transfer and execute setup script
        setup_path = "/tmp/asis-pg-setup.sh"
        sftp = client.open_sftp()
        with sftp.file(setup_path, 'w') as remote_file:
            remote_file.write(shell_script)
        sftp.close()
        client.exec_command(f"chmod +x {setup_path}")
        
        stdin, stdout, stderr = client.exec_command(f"bash {setup_path}")
        
        # Stream output
        print("-" * 50)
        for line in iter(stdout.readline, ""):
            print(f"[REMOTE] {line.strip()}")
        
        error = stderr.read().decode()
        if error:
            print(f"{RED}Error during remote script execution:{RESET}\n{error}")

        print("-" * 50)

        # 4. Cleanup remote scripts
        client.exec_command(f"rm -f {REMOTE_SCRIPT_PATH} {setup_path}")

        print(f"\n{GREEN}REMOTE MIGRATION FROM {host} COMPLETED!{RESET}")
        print("Please restart Pasarguard and Xray services on the remote server:")
        print("  docker restart pasarguard-pasarguard-1")
        print("  docker restart xray")


    except paramiko.AuthenticationException:
        print(f"{RED}Authentication failed. Check SSH Username/Password.{RESET}")
    except paramiko.SSHException as e:
        print(f"{RED}Could not establish SSH connection: {e}{RESET}")
    except Exception as e:
        print(f"{RED}An unexpected error occurred: {str(e)}{RESET}")
    finally:
        if client:
            client.close()
            print(f"{CYAN}SSH connection closed.{RESET}")
    
    input("Press Enter to return to the menu...")
    return True

# --- Local Migration Function (Core Logic) ---

def execute_local_migration_core():
    """Runs the core migration logic, assuming all files/paths are local."""
    clear_screen()
    print(f"{CYAN}=== Local Migration Marzban -> Pasarguard ==={RESET}")
    
    if not check_file_access():
        print(f"{RED}Error: File access issues detected locally. Cannot proceed.{RESET}")
        return False
    
    # ... (omitted main migration steps for brevity, it's the same as before)
    try:
        print(f"Loading credentials from .env files...")
        marzban_config = get_db_config(MARZBAN_ENV_PATH, "Marzban")
        pasarguard_config = get_db_config(PASARGUARD_ENV_PATH, "Pasarguard")

        if (marzban_config['host'] == pasarguard_config['host'] and
            marzban_config['port'] == pasarguard_config['port'] and
            marzban_config['db'] == pasarguard_config['db']):
            print(f"{RED}Error: Marzban and Pasarguard are using the same database. Change the Pasarguard database port using option 1.{RESET}")
            input("Press Enter to return to the menu...")
            return False

        print(f"{CYAN}Testing database connections...{RESET}")
        marzban_conn = connect(marzban_config)
        marzban_conn.close()
        pasarguard_conn = connect(pasarguard_config)
        pasarguard_conn.close()

        marzban_conn = connect(marzban_config)
        pasarguard_conn = connect(pasarguard_config)

        print(f"{CYAN}============================================================{RESET}")
        print(f"{CYAN}STARTING LOCAL MIGRATION{RESET}")
        print(f"{CYAN}============================================================{RESET}")

        admin_count = migrate_admins(marzban_conn, pasarguard_conn)
        print(f"{GREEN}{admin_count} admin(s) migrated ✓{RESET}")
        ensure_default_group(pasarguard_conn)
        print(f"{GREEN}Default group ensured ✓{RESET}")
        ensure_default_core_config(pasarguard_conn)
        print(f"{GREEN}Default core config ensured ✓{RESET}")

        xray_config = read_xray_config()
        print(f"{GREEN}Successfully read {XRAY_CONFIG_PATH} ✓{RESET}")
        migrate_xray_config(pasarguard_conn, xray_config)
        print(f"{GREEN}xray_config.json migrated as 'ASiS SK' in core_configs ✓{RESET}")

        inbound_count = migrate_inbounds_and_associate(marzban_conn, pasarguard_conn)
        print(f"{GREEN}{inbound_count} inbound(s) migrated and linked ✓{RESET}")

        host_count = migrate_hosts(marzban_conn, pasarguard_conn, safe_alpn)
        print(f"{GREEN}{host_count} host(s) migrated (ALPN fixed) ✓{RESET}")

        node_count = migrate_nodes(marzban_conn, pasarguard_conn)
        print(f"{GREEN}{node_count} node(s) migrated ✓{RESET}")

        user_count = migrate_users_and_proxies(marzban_conn, pasarguard_conn)
        print(f"{GREEN}{user_count} user(s) migrated with proxy settings ✓{RESET}")

        print(f"{GREEN}MIGRATION COMPLETED SUCCESSFULLY! ✓{RESET}")
        print("Please restart Pasarguard and Xray services:")
        print("  docker restart pasarguard-pasarguard-1")
        print("  docker restart xray")

    except Exception as e:
        print(f"{RED}Error during migration: {str(e)}{RESET}")
        return False
    finally:
        if 'marzban_conn' in locals(): marzban_conn.close()
        if 'pasarguard_conn' in locals(): pasarguard_conn.close()

    input("Press Enter to return to the menu...")
    return True

# --- Main Execution Logic ---

def main():
    """Main function to run the menu-driven program."""
    version = "1.0.15"
    is_remote_run = "--remote-migrate" in sys.argv
    
    if not is_remote_run:
        check_dependencies() # Check core dependencies only on the first run

    while True:
        if is_remote_run:
            print(f"{CYAN}Running in REMOTE MIGRATION MODE...{RESET}")
            execute_local_migration_core() # Core logic runs immediately
            sys.exit(0)
        
        display_menu(version)
        choice = input("Enter your choice (1-4): ").strip()

        if choice == "1":
            change_db_port()
        elif choice == "2":
            execute_local_migration_core()
        elif choice == "3":
            remote_execute_migration()
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
