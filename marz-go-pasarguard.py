#!/usr/bin/env python3
"""
Marzban to Pasarguard Migration Menu
Version: 1.0.15 (Final Fix: Re-implementing Backticks for Reserved Keywords)
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
MARZBAN_ENV_PATH = "/opt/marzban/.env"
PASARGUARD_ENV_PATH = "/opt/pasarguard/.env"
DOCKER_COMPOSE_FILE_PATH = "/opt/pasarguard/docker-compose.yml"
XRAY_CONFIG_PATH = "/var/lib/marzban/xray_config.json"

# Global list for reporting failed/skipped items
MIGRATION_SUMMARY_REPORT: List[str] = []

# --- UI & SYSTEM FUNCTIONS ---
def clear_screen():
    # Attempt to clear the screen for better readability
    os.system("clear")

def display_menu():
    clear_screen()
    print(f"{CYAN}╔═════════════════════════════════════════════╗")
    print(f"║{YELLOW}          Power By: ASiSSK                     {CYAN}║")
    print(f"║{YELLOW}          Marz ➔ Pasarguard                  {CYAN}║")
    print(f"║{YELLOW}              v1.0.15 (Final Fixed)          {CYAN}║")
    print(f"╚═════════════════════════════════════════════╝{RESET}")
    print()
    print("Menu:")
    print("1. Change Database and phpMyAdmin Ports (Pasarguard)")
    print("2. Migrate Marzban to Pasarguard")
    print("3. Exit")
    print()

def check_dependencies():
    """Checks if critical dependencies are imported."""
    try:
        import pymysql
        import dotenv
    except ImportError as e:
        print(f"{RED}Critical Dependency Error: {str(e)}.{RESET}")
        print(f"{RED}Please ensure all packages are installed (pymysql, python-dotenv).{RESET}")
        sys.exit(1)
    
# --- HELPER FUNCTIONS ---
def safe_alpn(value: Optional[str]) -> Optional[str]:
    if not value or str(value).strip().lower() in ["none", "null", ""]:
        return None
    return str(value).strip()

def safe_json(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            json.loads(value)
        return json.dumps(value) if not isinstance(value, str) else value
    except:
        return None

def load_env_file(env_path: str) -> Optional[Dict[str, str]]:
    if not os.path.exists(env_path):
        return None
    if not os.access(env_path, os.R_OK):
        print(f"{RED}Permission Error: No read permission for {env_path}.{RESET}")
        return None
    try:
        env = dotenv_values(env_path)
        return env
    except Exception as e:
        print(f"{RED}Error loading {env_path}: {str(e)}{RESET}")
        return None

def parse_sqlalchemy_url(url: str) -> Dict[str, Any]:
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
    global MIGRATION_SUMMARY_REPORT
    
    if manual_input:
        print(f"{CYAN}--- {name.upper()} DATABASE SETTINGS (Manual Input) ---{RESET}")
        host = input(f"Enter {name} DB Host (e.g., 127.0.0.1): ").strip()
        port_str = input(f"Enter {name} DB Port (e.g., 3306): ").strip()
        user = input(f"Enter {name} DB User (e.g., marzban): ").strip()
        password = input(f"Enter {name} DB Password: ").strip()
        db_name = input(f"Enter {name} DB Name (e.g., marzban): ").strip()

        if not all([host, port_str, user, password, db_name]):
            MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: {name} DB config missing required fields.{RESET}")
            return None
        
        try:
            port = int(port_str)
        except ValueError:
            MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: {name} DB Port must be an integer.{RESET}")
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

    try:
        env = load_env_file(env_path)
        if not env:
            MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: Could not load {name} .env file at {env_path}.{RESET}")
            return None
            
        print(f"{CYAN}Attempting to read SQLALCHEMY_DATABASE_URL from {env_path}...{RESET}")
        
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
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: Error loading {name} config from file: {str(e)}{RESET}")
        return None

def read_xray_config() -> Optional[Dict[str, Any]]:
    global MIGRATION_SUMMARY_REPORT
    if not os.path.exists(XRAY_CONFIG_PATH):
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: xray_config.json not found at {XRAY_CONFIG_PATH}. Skipping Xray config migration.{RESET}")
        return None
    if not os.access(XRAY_CONFIG_PATH, os.R_OK):
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: No read permission for {XRAY_CONFIG_PATH}. Skipping Xray config migration.{RESET}")
        return None
    try:
        with open(XRAY_CONFIG_PATH, 'r', encoding='utf-8') as file:
            return json.load(file)
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: Error reading or parsing xray_config.json: {str(e)}. Skipping Xray config migration.{RESET}")
        return None

def connect(cfg: Dict[str, Any]) -> Optional[pymysql.connections.Connection]:
    global MIGRATION_SUMMARY_REPORT
    try:
        conn = pymysql.connect(**cfg)
        print(f"{GREEN}Connected to {cfg['db']}@{cfg['host']}:{cfg['port']} ✓{RESET}")
        time.sleep(0.5)
        return conn
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: Connection to DB {cfg['db']}@{cfg['host']}:{cfg['port']} failed: {str(e)}{RESET}")
        return None

# --- MIGRATION FUNCTIONS ---

def migrate_admins(marzban_conn, pasarguard_conn) -> int:
    """Migrate admins from Marzban to Pasarguard."""
    global MIGRATION_SUMMARY_REPORT
    count = 0
    try:
        with marzban_conn.cursor() as cur:
            cur.execute("SELECT * FROM admins")
            admins = cur.fetchall()

        with pasarguard_conn.cursor() as cur:
            # Table creation logic is only executed if table does not exist
            cur.execute("SHOW TABLES LIKE 'admins'")
            if cur.fetchone() is None:
                cur.execute("""
                    CREATE TABLE admins (
                        id INT PRIMARY KEY, username VARCHAR(255) NOT NULL, hashed_password TEXT NOT NULL,
                        created_at DATETIME NOT NULL, is_sudo BOOLEAN DEFAULT FALSE,
                        password_reset_at DATETIME, telegram_id BIGINT, discord_webhook TEXT
                    )
                """)
                print(f"{GREEN}Created admins table in Pasarguard ✓{RESET}")
                time.sleep(0.5)

            for a in admins:
                cur.execute(
                    """
                    INSERT INTO admins (id, username, hashed_password, created_at, is_sudo, password_reset_at, telegram_id, discord_webhook)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        username = %s, hashed_password = %s, created_at = %s, is_sudo = %s,
                        password_reset_at = %s, telegram_id = %s, discord_webhook = %s
                    """,
                    (
                        a["id"], a["username"], a["hashed_password"], a["created_at"], a["is_sudo"],
                        a["password_reset_at"], a["telegram_id"], a["discord_webhook"],
                        a["username"], a["hashed_password"], a["created_at"], a["is_sudo"],
                        a["password_reset_at"], a["telegram_id"], a["discord_webhook"]
                    ),
                )
                count += 1
        pasarguard_conn.commit()
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: Failed to migrate admins: {str(e)}. Skipping this table.{RESET}")
    return count

def ensure_default_group(pasarguard_conn):
    """Ensure default group exists in Pasarguard."""
    global MIGRATION_SUMMARY_REPORT
    try:
        with pasarguard_conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'groups'")
            if cur.fetchone() is None:
                # FIX: Use backticks for 'groups' table to avoid SQL reserved keyword issues
                cur.execute("""
                    CREATE TABLE `groups` (
                        id INT PRIMARY KEY, name VARCHAR(255) NOT NULL, is_disabled BOOLEAN DEFAULT FALSE
                    )
                """)
                print(f"{GREEN}Created `groups` table in Pasarguard ✓{RESET}")
                time.sleep(0.5)
            
            # FIX: Use backticks for 'groups' table in SELECT
            cur.execute("SELECT COUNT(*) AS cnt FROM `groups` WHERE id = 1")
            if cur.fetchone()["cnt"] == 0:
                cur.execute("INSERT INTO `groups` (id, name, is_disabled) VALUES (1, 'DefaultGroup', 0)")
                print(f"{GREEN}Created default group in Pasarguard ✓{RESET}")
                time.sleep(0.5)
        pasarguard_conn.commit()
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: Failed to ensure default group: {str(e)}. This may cause issues.{RESET}")

def ensure_default_core_config(pasarguard_conn):
    """Ensure default core config exists in Pasarguard."""
    global MIGRATION_SUMMARY_REPORT
    try:
        with pasarguard_conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'core_configs'")
            if cur.fetchone() is None:
                # FIX: Use backticks for 'core_configs' table
                cur.execute("""
                    CREATE TABLE `core_configs` (
                        id INT PRIMARY KEY, created_at DATETIME NOT NULL, name VARCHAR(255) NOT NULL,
                        config JSON NOT NULL, exclude_inbound_tags TEXT, fallbacks_inbound_tags TEXT
                    )
                """)
                print(f"{GREEN}Created `core_configs` table in Pasarguard ✓{RESET}")
                time.sleep(0.5)
            
            # FIX: Use backticks for 'core_configs' table in SELECT
            cur.execute("SELECT COUNT(*) AS cnt FROM `core_configs` WHERE id = 1")
            if cur.fetchone()["cnt"] == 0:
                cfg = {
                    "log": {"loglevel": "warning"}, "inbounds": [{"tag": "Shadowsocks TCP", "listen": "0.0.0.0", "port": 1080, "protocol": "shadowsocks", "settings": {"clients": [], "network": "tcp,udp"}}],
                    "outbounds": [{"protocol": "freedom", "tag": "DIRECT"}, {"protocol": "blackhole", "tag": "BLOCK"}],
                    "routing": {"rules": [{"ip": ["geoip:private"], "outboundTag": "BLOCK", "type": "field"}]}
                }
                cur.execute(
                    """
                    INSERT INTO `core_configs` (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
                    VALUES (1, NOW(), 'ASiS SK', %s, '', '')
                    """,
                    json.dumps(cfg),
                )
                print(f"{GREEN}Created default core config 'ASiS SK' in Pasarguard ✓{RESET}")
                time.sleep(0.5)
        pasarguard_conn.commit()
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: Failed to ensure default core config: {str(e)}. This may cause issues.{RESET}")

def migrate_xray_config(pasarguard_conn, xray_config) -> int:
    """Migrate xray_config.json to core_configs."""
    global MIGRATION_SUMMARY_REPORT
    if not xray_config: return 0

    try:
        with pasarguard_conn.cursor() as cur:
            cur.execute("SELECT * FROM `core_configs` WHERE id = 1")
            existing = cur.fetchone()
            backup_id = 0
            if existing:
                cur.execute("SELECT MAX(id) AS max_id FROM `core_configs`")
                max_id = cur.fetchone()["max_id"] or 1
                backup_id = max_id + 1
                
                cur.execute(
                    """
                    INSERT INTO `core_configs` (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
                    VALUES (%s, NOW(), %s, %s, %s, %s)
                    """,
                    (
                        backup_id,
                        f"Backup_ASiS_SK_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}",
                        existing["config"], existing["exclude_inbound_tags"], existing["fallbacks_inbound_tags"],
                    ),
                )
                print(f"{GREEN}Backup created as ID {backup_id} ✓{RESET}")
                time.sleep(0.5)

            cur.execute(
                """
                INSERT INTO `core_configs` (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
                VALUES (%s, NOW(), %s, %s, '', '')
                ON DUPLICATE KEY UPDATE
                    name = %s, config = %s, created_at = NOW()
                """,
                (1, "ASiS SK", json.dumps(xray_config), "ASiS SK", json.dumps(xray_config)),
            )
        pasarguard_conn.commit()
        return 1
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: Failed to migrate Xray config to core_configs: {str(e)}. Skipping this step.{RESET}")
        return 0

def migrate_inbounds_and_associate(marzban_conn, pasarguard_conn) -> int:
    """Migrate inbounds and associate with default group."""
    global MIGRATION_SUMMARY_REPORT
    count = 0
    try:
        with marzban_conn.cursor() as cur:
            cur.execute("SELECT * FROM inbounds")
            inbounds = cur.fetchall()

        with pasarguard_conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'inbounds'")
            if cur.fetchone() is None:
                cur.execute("CREATE TABLE inbounds (id INT PRIMARY KEY, tag VARCHAR(255) NOT NULL)")
                print(f"{GREEN}Created inbounds table in Pasarguard ✓{RESET}")
                time.sleep(0.5)

            cur.execute("SHOW TABLES LIKE 'inbounds_groups_association'")
            if cur.fetchone() is None:
                # FIX: Use backticks for 'groups' table reference
                cur.execute("""
                    CREATE TABLE inbounds_groups_association (
                        inbound_id INT, group_id INT, PRIMARY KEY (inbound_id, group_id),
                        FOREIGN KEY (inbound_id) REFERENCES inbounds(id),
                        FOREIGN KEY (group_id) REFERENCES `groups`(id)
                    )
                """)
                print(f"{GREEN}Created inbounds_groups_association table in Pasarguard ✓{RESET}")
                time.sleep(0.5)

            for i in inbounds:
                cur.execute("INSERT INTO inbounds (id, tag) VALUES (%s,%s) ON DUPLICATE KEY UPDATE tag = %s", (i["id"], i["tag"], i["tag"]))
                cur.execute("INSERT IGNORE INTO inbounds_groups_association (inbound_id, group_id) VALUES (%s,1)", (i["id"],))
                count += 1
        pasarguard_conn.commit()
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: Failed to migrate inbounds: {str(e)}. Skipping this table.{RESET}")
    return count

def migrate_hosts(marzban_conn, pasarguard_conn, safe_alpn_func) -> int:
    """Migrate hosts with ALPN fix and default values for optional fields."""
    global MIGRATION_SUMMARY_REPORT
    count = 0
    try:
        with marzban_conn.cursor() as cur:
            cur.execute("SELECT * FROM hosts")
            hosts = cur.fetchall()

        with pasarguard_conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'hosts'")
            if cur.fetchone() is None:
                cur.execute("""
                    CREATE TABLE hosts (
                        id INT PRIMARY KEY, remark VARCHAR(255), address VARCHAR(255), port INT,
                        inbound_tag VARCHAR(255), sni TEXT, host TEXT, security VARCHAR(50), alpn TEXT,
                        fingerprint TEXT, allowinsecure BOOLEAN, is_disabled BOOLEAN, path TEXT,
                        random_user_agent BOOLEAN, use_sni_as_host BOOLEAN, priority INT DEFAULT 0,
                        http_headers TEXT, transport_settings TEXT, mux_settings TEXT,
                        noise_settings TEXT, fragment_settings TEXT, status VARCHAR(50)
                    )
                """)
                print(f"{GREEN}Created hosts table in Pasarguard ✓{RESET}")
                time.sleep(0.5)

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
                        remark = %s, address = %s, port = %s, inbound_tag = %s, sni = %s, host = %s,
                        security = %s, alpn = %s, fingerprint = %s, allowinsecure = %s, is_disabled = %s,
                        path = %s, random_user_agent = %s, use_sni_as_host = %s, priority = %s,
                        http_headers = %s, transport_settings = %s, mux_settings = %s,
                        noise_settings = %s, fragment_settings = %s, status = %s
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
                count += 1
        pasarguard_conn.commit()
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: Failed to migrate hosts: {str(e)}. Skipping this table.{RESET}")
    return count

def migrate_nodes(marzban_conn, pasarguard_conn) -> int:
    """Migrate nodes."""
    global MIGRATION_SUMMARY_REPORT
    count = 0
    try:
        with marzban_conn.cursor() as cur:
            cur.execute("SELECT * FROM nodes")
            nodes = cur.fetchall()

        with pasarguard_conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'nodes'")
            if cur.fetchone() is None:
                # FIX: Use backticks for 'core_configs' table reference
                cur.execute("""
                    CREATE TABLE nodes (
                        id INT PRIMARY KEY, name VARCHAR(255) NOT NULL, address VARCHAR(255) NOT NULL,
                        port INT, status VARCHAR(50), last_status_change DATETIME, message TEXT,
                        created_at DATETIME NOT NULL, uplink BIGINT, downlink BIGINT,
                        xray_version VARCHAR(50), usage_coefficient FLOAT, node_version VARCHAR(50),
                        connection_type VARCHAR(50), server_ca TEXT, keep_alive BOOLEAN,
                        max_logs INT, core_config_id INT, gather_logs BOOLEAN,
                        FOREIGN KEY (core_config_id) REFERENCES `core_configs`(id)
                    )
                """)
                print(f"{GREEN}Created nodes table in Pasarguard ✓{RESET}")
                time.sleep(0.5)

            for n in nodes:
                cur.execute(
                    """
                    INSERT INTO nodes
                    (id, name, address, port, status, last_status_change, message,
                     created_at, uplink, downlink, xray_version, usage_coefficient,
                     node_version, connection_type, server_ca, keep_alive, max_logs,
                     core_config_id, gather_logs)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,1)
                    ON DUPLICATE KEY UPDATE
                        name = %s, address = %s, port = %s, status = %s, last_status_change = %s,
                        message = %s, created_at = %s, uplink = %s, downlink = %s,
                        xray_version = %s, usage_coefficient = %s, node_version = %s,
                        connection_type = %s, server_ca = %s, keep_alive = %s, max_logs = %s,
                        core_config_id = 1, gather_logs = 1
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
                count += 1
        pasarguard_conn.commit()
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: Failed to migrate nodes: {str(e)}. Skipping this table.{RESET}")
    return count

def migrate_users_and_proxies(marzban_conn, pasarguard_conn) -> int:
    """Migrate users and their proxy settings."""
    global MIGRATION_SUMMARY_REPORT
    total_users = 0
    
    try:
        with marzban_conn.cursor() as cur:
            cur.execute("SELECT * FROM users")
            users = cur.fetchall()

        with pasarguard_conn.cursor() as cur:
            cur.execute("SHOW TABLES LIKE 'users'")
            if cur.fetchone() is None:
                cur.execute("""
                    CREATE TABLE users (
                        id INT PRIMARY KEY, username VARCHAR(255) NOT NULL, status VARCHAR(50),
                        used_traffic BIGINT, data_limit BIGINT, created_at DATETIME NOT NULL,
                        admin_id INT, data_limit_reset_strategy VARCHAR(50), sub_revoked_at DATETIME,
                        note TEXT, online_at DATETIME, edit_at DATETIME, on_hold_timeout DATETIME,
                        on_hold_expire_duration INT, auto_delete_in_days INT,
                        last_status_change DATETIME, expire DATETIME, proxy_settings JSON
                    )
                """)
                print(f"{GREEN}Created users table in Pasarguard ✓{RESET}")
                time.sleep(0.5)

            for u in users:
                try:
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
                            username = %s, status = %s, used_traffic = %s, data_limit = %s,
                            created_at = %s, admin_id = %s, data_limit_reset_strategy = %s,
                            sub_revoked_at = %s, note = %s, online_at = %s, edit_at = %s,
                            on_hold_timeout = %s, on_hold_expire_duration = %s,
                            auto_delete_in_days = %s, last_status_change = %s,
                            expire = %s, proxy_settings = %s
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
                    total_users += 1
                except Exception as user_e:
                    MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: Failed to migrate user ID {u.get('id', 'Unknown')}: {str(user_e)}. Skipping this user.{RESET}")

        pasarguard_conn.commit()
    except Exception as e:
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Warning: Failed to migrate users table: {str(e)}. Skipping this table.{RESET}")
        
    return total_users

# --- MENU LOGIC ---
def change_db_port() -> bool:
    clear_screen()
    print(f"{CYAN}=== Change Database and phpMyAdmin Ports (Pasarguard) ==={RESET}")

    default_db_port = "3307"
    default_apache_port = "8020"
    db_port = input(f"Enter database port (Default: {default_db_port}): ").strip() or default_db_port
    apache_port = input(f"Enter phpMyAdmin APACHE_PORT (Default: {default_apache_port}): ").strip() or default_apache_port
    success = True

    try:
        for port, name in [(db_port, "Database port"), (apache_port, "phpMyAdmin APACHE_PORT")]:
            if not port.isdigit() or int(port) < 1 or int(port) > 65535:
                print(f"{RED}Error: Invalid {name}. Must be between 1 and 65535.{RESET}")
                success = False
                input("Press Enter to return to the menu...")
                return success

        env_file = PASARGUARD_ENV_PATH
        if os.path.exists(env_file):
            with open(env_file, 'r', encoding='utf-8') as file:
                content = file.read()
            content = re.sub(r'DB_PORT=\d+', f'DB_PORT={db_port}', content, 1) if re.search(r'DB_PORT=\d+', content) else content + f'\nDB_PORT={db_port}\n'
            
            # Smartly update SQLALCHEMY_DATABASE_URL port
            def replace_db_port(match):
                # Replace the port number in the matched URL string with the new db_port
                return re.sub(r':\d+/', f':{db_port}/', match.group(0))

            content = re.sub(
                r'SQLALCHEMY_DATABASE_URL="mysql\+(asyncmy|pymysql)://([^:]+):([^@]+)@127\.0\.0\.1:\d+/[^"]+"',
                replace_db_port,
                content
            )

            with open(env_file, 'w', encoding='utf-8') as file:
                file.write(content)
            print(f"{GREEN}Updated {env_file} with database port {db_port} ✓{RESET}")
            time.sleep(0.5)
        else:
            print(f"{RED}Error: File {env_file} not found. Pasarguard must be installed.{RESET}")
            success = False

        compose_file = DOCKER_COMPOSE_FILE_PATH
        if os.path.exists(compose_file):
            with open(compose_file, 'r', encoding='utf-8') as file:
                content = file.read()
            
            # Update MariaDB/DB port in docker-compose.yml
            if re.search(r'--port=\d+', content):
                content = re.sub(r'--port=\d+', f'--port={db_port}', content)
            else:
                # Fallback insertion (less robust, but attempts to ensure the command is correct)
                content = re.sub(
                    r'(command:\n\s+- --bind-address=127\.0\.0\.1)',
                    f'command:\n      - --port={db_port}\n      - --bind-address=127.0.0.1',
                    content
                )
            
            # Update PMA_PORT environment variable (for phpMyAdmin to connect to the DB)
            if re.search(r'PMA_PORT: \d+', content):
                content = re.sub(r'PMA_PORT: \d+', f'PMA_PORT: {db_port}', content)
            else:
                content = re.sub(
                    r'(environment:\n\s+PMA_HOST: 127\.0\.0\.1)',
                    f'environment:\n      PMA_HOST: 127.0.0.1\n      PMA_PORT: {db_port}',
                    content
                )
            
            # Update APACHE_PORT environment variable (for exposing phpMyAdmin port)
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
            print(f"{RED}Error: File {compose_file} not found. Pasarguard must be installed.{RESET}")
            success = False

        if success:
            print(f"{GREEN}Port changes applied successfully! Please restart services:{RESET}")
            print(f"  docker restart pasarguard-pasarguard-1")
            print(f"  docker restart pasarguard-mariadb-1")
            print(f"  docker restart pasarguard-phpmyadmin-1")

    except Exception as e:
        print(f"{RED}Error during port change: {str(e)}{RESET}")
        success = False

    input("Press Enter to return to the menu...")
    return success

def check_file_access(mode: str) -> bool:
    """Check access to necessary files based on migration mode."""
    print(f"{CYAN}Checking file access...{RESET}")
    success = True
    
    pasarguard_files = [
        (PASARGUARD_ENV_PATH, "Pasarguard .env"),
        (DOCKER_COMPOSE_FILE_PATH, "docker-compose.yml")
    ]
    for file_path, file_name in pasarguard_files:
        if not os.path.exists(file_path) or not os.access(file_path, os.R_OK):
            print(f"{RED}Critical Error: {file_name} is required at {file_path}. Please install Pasarguard first.{RESET}")
            return False

    if mode == 'local':
        marzban_files = [
            (MARZBAN_ENV_PATH, "Marzban .env"),
            (XRAY_CONFIG_PATH, "xray_config.json")
        ]
        for file_path, file_name in marzban_files:
            if not os.path.exists(file_path):
                print(f"{YELLOW}Warning: {file_name} not found at {file_path}. DB config will be asked manually (if .env is missing) and Xray config will be skipped.{RESET}")
            elif not os.access(file_path, os.R_OK):
                print(f"{YELLOW}Warning: No read permission for {file_name} at {file_path}. DB config will be asked manually (if .env is missing) and Xray config will be skipped.{RESET}")
    
    print(f"{GREEN}Pasarguard file access OK ✓{RESET}")
    time.sleep(0.5)
    return success

def get_marzban_config_mode() -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    global MIGRATION_SUMMARY_REPORT
    clear_screen()
    print(f"{CYAN}=== Marzban Configuration Source ==={RESET}")
    print("How do you want to provide Marzban Database configuration?")
    print("1. Local File: Load from local /opt/marzban/.env (Marzban on the same server)")
    print("2. Manual Input: Enter connection details (Marzban on a different server or .env not accessible)")
    print("3. Back to Main Menu")
    
    choice = input("Enter your choice (1-3): ").strip()
    
    marzban_config = None
    pasarguard_config = None
    xray_config = None
    
    if choice == "1":
        print(f"{CYAN}Loading Marzban config from local file...{RESET}")
        if not check_file_access('local'):
             return None, None, None
        marzban_config = get_db_config(MARZBAN_ENV_PATH, "Marzban", manual_input=False)
        if marzban_config:
            xray_config = read_xray_config()
        pasarguard_config = get_db_config(PASARGUARD_ENV_PATH, "Pasarguard", manual_input=False)
    elif choice == "2":
        print(f"{CYAN}Manually entering Marzban config...{RESET}")
        if not check_file_access('remote'):
             return None, None, None
        marzban_config = get_db_config(MARZBAN_ENV_PATH, "Marzban", manual_input=True)
        pasarguard_config = get_db_config(PASARGUARD_ENV_PATH, "Pasarguard", manual_input=False)
        print(f"{YELLOW}Note: Since Marzban is remote or inaccessible, Xray config will be skipped.{RESET}")
        MIGRATION_SUMMARY_REPORT.append(f"{YELLOW}Note: Xray config migration skipped (Remote/Manual Marzban config).{RESET}")
    elif choice == "3":
        return None, None, None
    else:
        print(f"{RED}Invalid choice. Returning to Main Menu.{RESET}")
        time.sleep(1)
        return None, None, None
    
    return marzban_config, pasarguard_config, xray_config

def migrate_marzban_to_pasarguard():
    """Migrate data from Marzban to Pasarguard."""
    global MIGRATION_SUMMARY_REPORT
    MIGRATION_SUMMARY_REPORT = []
    clear_screen()
    print(f"{CYAN}=== Migrate Marzban to Pasarguard ==={RESET}")

    marzban_config, pasarguard_config, xray_config = get_marzban_config_mode()

    if marzban_config is None or pasarguard_config is None:
        print(f"\n{RED}Migration aborted due to database configuration errors.{RESET}")
        print("\n" + "\n".join(MIGRATION_SUMMARY_REPORT))
        input("Press Enter to return to the menu...")
        return False
    
    # Critical safety check
    if (marzban_config['host'] == pasarguard_config['host'] and
        marzban_config['port'] == pasarguard_config['port'] and
        marzban_config['db'] == pasarguard_config['db']):
        print(f"{RED}Error: Marzban and Pasarguard are using the exact same database. Aborting to prevent data corruption.{RESET}")
        MIGRATION_SUMMARY_REPORT.append(f"{RED}Failure: Same database detected for Marzban and Pasarguard.{RESET}")
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
        input("Press Enter to return to the menu...")
        return False
    
    # --- START MIGRATION ---
    print(f"{CYAN}============================================================{RESET}")
    print(f"{CYAN}STARTING MIGRATION (Non-Fatal Errors will be logged as Warnings){RESET}")
    print(f"{CYAN}============================================================{RESET}")
    
    print("Ensuring default Pasarguard prerequisites...")
    ensure_default_group(pasarguard_conn)
    ensure_default_core_config(pasarguard_conn)

    print("Migrating admins...")
    admin_count = migrate_admins(marzban_conn, pasarguard_conn)
    print(f"{GREEN}{admin_count} admin(s) migrated (or skipped on error).{RESET}")
    time.sleep(0.5)

    if xray_config:
        print("Migrating xray_config.json to core_configs...")
        migrate_count = migrate_xray_config(pasarguard_conn, xray_config)
        print(f"{GREEN}{migrate_count} Xray config migrated (if 1 is correct).{RESET}")
        time.sleep(0.5)
    else:
        print(f"{YELLOW}Xray config migration skipped (Not found or manual mode).{RESET}")
        time.sleep(0.5)

    print("Migrating inbounds...")
    inbound_count = migrate_inbounds_and_associate(marzban_conn, pasarguard_conn)
    print(f"{GREEN}{inbound_count} inbound(s) migrated and linked (or skipped on error).{RESET}")
    time.sleep(0.5)

    print("Migrating hosts (with smart ALPN fix)...")
    host_count = migrate_hosts(marzban_conn, pasarguard_conn, safe_alpn)
    print(f"{GREEN}{host_count} host(s) migrated (or skipped on error).{RESET}")
    time.sleep(0.5)

    print("Migrating nodes...")
    node_count = migrate_nodes(marzban_conn, pasarguard_conn)
    print(f"{GREEN}{node_count} node(s) migrated (or skipped on error).{RESET}")
    time.sleep(0.5)

    print("Migrating users and proxy settings...")
    user_count = migrate_users_and_proxies(marzban_conn, pasarguard_conn)
    print(f"{GREEN}{user_count} user(s) migrated (or skipped on error).{RESET}")
    time.sleep(0.5)

    # --- END MIGRATION ---

    print(f"{CYAN}============================================================{RESET}")
    print(f"{GREEN}MIGRATION ATTEMPT COMPLETED!{RESET}")
    print("Please restart Pasarguard and Xray services:")
    print("  docker restart pasarguard-pasarguard-1")
    print("  docker restart pasarguard-mariadb-1")
    print("  docker restart xray")
    print(f"{CYAN}============================================================{RESET}")
    
    if MIGRATION_SUMMARY_REPORT:
        print(f"{YELLOW}SUMMARY OF WARNINGS/FAILURES:{RESET}")
        for item in MIGRATION_SUMMARY_REPORT:
            print(f"* {item}")
    else:
        print(f"{GREEN}No warnings or critical failures were logged. Appears successful!{RESET}")

    marzban_conn.close()
    pasarguard_conn.close()

    input("Press Enter to return to the menu...")
    return True

def main():
    """Main function to run the menu-driven program."""
    if os.geteuid() != 0:
        print(f"{RED}This script must be run as root. Please run with sudo or as the root user.{RESET}")
        sys.exit(1)
        
    check_dependencies()

    while True:
        display_menu()
        choice = input("Enter your choice (1-3): ").strip()

        if choice == "1":
            change_db_port()
        elif choice == "2":
            migrate_marzban_to_pasarguard()
        elif choice == "3":
            print(f"{CYAN}Exiting... Thank you for using Marz ➔ Pasarguard!{RESET}")
            sys.exit(0)
        else:
            print(f"{RED}Invalid choice. Please enter 1, 2, or 3.{RESET}")
            input("Press Enter to continue...")

if __name__ == "__main__":
    if sys.version_info < (3, 6):
        print("Python 3.6+ required.")
        sys.exit(1)
    
    main()
