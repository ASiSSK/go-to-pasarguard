#!/usr/bin/env python3
"""
Marzban to Pasarguard Migration Tool
Version: 2.0.0 (Complete with SSH Remote Migration)
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
# Ensure these are installed: pip3 install python-dotenv pymysql paramiko
import paramiko
from typing import Dict, Any, Optional
from dotenv import dotenv_values

# ANSI color codes for better readability
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"

# Default paths (Pasarguard must be local to the execution environment)
PASARGUARD_ENV_PATH = "/opt/pasarguard/.env"
DOCKER_COMPOSE_FILE_PATH = "/opt/pasarguard/docker-compose.yml"

# Marzban Paths (Local or Remote)
MARZBAN_ENV_PATH_DEFAULT = "/opt/marzban/.env"
XRAY_CONFIG_PATH_DEFAULT = "/var/lib/marzban/xray_config.json"

# --- Helper Functions for Data Transformation ---

def safe_alpn(value: Optional[str]) -> Optional[str]:
    """Convert 'none', '', 'null' or None to NULL for ALPN field in Pasarguard."""
    if not value or str(value).strip().lower() in ["none", "null", ""]:
        return None
    return str(value).strip()

def safe_json(value: Any) -> Optional[str]:
    """Safely convert Python object or valid JSON string to JSON string or return NULL"""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            json.loads(value)
            return value
        return json.dumps(value)
    except:
        return None

def parse_sqlalchemy_url(url: str) -> Dict[str, Any]:
    """Parse SQLALCHEMY_DATABASE_URL to extract host, port, user, password, db."""
    pattern = r"mysql\+(asyncmy|pymysql)://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)"
    match = re.match(pattern, url)
    if not match:
        raise ValueError(f"Invalid SQLALCHEMY_DATABASE_URL format: {url}")
    return {
        "user": match.group(2),
        "password": match.group(3),
        "host": match.group(4),
        "port": int(match.group(5)),
        "db": match.group(6)
    }

def connect(cfg: Dict[str, Any]):
    """Connect to database."""
    try:
        conn = pymysql.connect(**cfg)
        print(f"{GREEN}Connected to {cfg['db']}@{cfg['host']}:{cfg['port']} ✓{RESET}")
        time.sleep(0.5)
        return conn
    except Exception as e:
        raise Exception(f"Connection failed to {cfg['host']}:{cfg['port']}. Error: {str(e)}")

# --- SSH Remote Connection Helpers (NEW) ---

def ssh_connect(host, port, user, password):
    """Establish an SSH connection to the remote Marzban server."""
    print(f"{CYAN}Attempting SSH connection to {user}@{host}:{port}...{RESET}")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(hostname=host, port=port, username=user, password=password, timeout=10)
        print(f"{GREEN}SSH connection established successfully ✓{RESET}")
        return client
    except Exception as e:
        raise Exception(f"SSH connection failed: {e}")

def get_remote_file_content(ssh_client, remote_path):
    """Execute 'cat' command via SSH to read remote file content."""
    print(f"Reading remote file: {remote_path}")
    # Use 'sudo cat' just in case if the user is not root but has sudo access
    stdin, stdout, stderr = ssh_client.exec_command(f"sudo cat {remote_path}")
    error = stderr.read().decode().strip()
    if "No such file or directory" in error or "Permission denied" in error:
        raise FileNotFoundError(f"Remote file access error for {remote_path}: {error}")
    
    output = stdout.read().decode()
    if not output and error:
        raise Exception(f"Failed to read file {remote_path}: {error}")
        
    return output

def get_remote_marzban_config(ssh_config):
    """Connect to remote Marzban and retrieve DB config and Xray config."""
    ssh_client = None
    try:
        ssh_client = ssh_connect(
            ssh_config['host'], 
            ssh_config.get('port', 22), 
            ssh_config['user'], 
            ssh_config['password']
        )
        
        # 1. Get Marzban .env content
        env_content = get_remote_file_content(ssh_client, ssh_config.get('env_path', MARZBAN_ENV_PATH_DEFAULT))
        
        # 2. Extract SQLALCHEMY_DATABASE_URL from remote .env
        match = re.search(r'SQLALCHEMY_DATABASE_URL\s*=\s*["\']?(.*?)["\']?$', env_content, re.MULTILINE)
        if not match:
            raise ValueError(f"SQLALCHEMY_DATABASE_URL not found in remote {ssh_config['env_path']}")
            
        sqlalchemy_url = match.group(1).strip()
        marzban_db_config = parse_sqlalchemy_url(sqlalchemy_url)
        marzban_db_config["charset"] = "utf8mb4"
        marzban_db_config["cursorclass"] = pymysql.cursors.DictCursor
        
        # 3. Get Marzban Xray config (JSON)
        xray_json_content = get_remote_file_content(ssh_client, ssh_config.get('xray_path', XRAY_CONFIG_PATH_DEFAULT))
        xray_config = json.loads(xray_json_content)
        
        print(f"{GREEN}Marzban Remote Configs Loaded Successfully ✓{RESET}")
        print(f"Remote DB Host: {marzban_db_config['host']}:{marzban_db_config['port']}")
        
        return marzban_db_config, xray_config
        
    finally:
        if ssh_client:
            ssh_client.close()

# --- Local Environment and Configuration ---

def load_env_file(env_path: str) -> Dict[str, str]:
    """Load LOCAL .env file and return key-value pairs."""
    if not os.path.exists(env_path):
        raise FileNotFoundError(f"Environment file {env_path} not found")
    if not os.access(env_path, os.R_OK):
        raise PermissionError(f"No read permission for {env_path}")
    
    env = dotenv_values(env_path)
    if not env.get("SQLALCHEMY_DATABASE_URL"):
        raise ValueError(f"SQLALCHEMY_DATABASE_URL not found in {env_path}")
    return env

def get_local_db_config(env_path: str, name: str) -> Dict[str, Any]:
    """Get database config from LOCAL .env file."""
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

def read_local_xray_config() -> Dict[str, Any]:
    """Read LOCAL xray_config.json from /var/lib/marzban."""
    if not os.path.exists(XRAY_CONFIG_PATH_DEFAULT):
        raise FileNotFoundError(f"xray_config.json not found at {XRAY_CONFIG_PATH_DEFAULT}")
    if not os.access(XRAY_CONFIG_PATH_DEFAULT, os.R_OK):
        raise PermissionError(f"No read permission for {XRAY_CONFIG_PATH_DEFAULT}")
    try:
        with open(XRAY_CONFIG_PATH_DEFAULT, 'r', encoding='utf-8') as file:
            return json.load(file)
    except Exception as e:
        raise Exception(f"Error reading xray_config.json: {str(e)}")

# --- Migration Functions (FULL IMPLEMENTATION) ---

# Migration: Admins
def migrate_admins(marzban_conn, pasarguard_conn):
    """Migrate admins from Marzban to Pasarguard."""
    # ... (Full implementation of migrate_admins) ...
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM admins")
        admins = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'admins'")
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE admins (
                    id INT PRIMARY KEY,
                    username VARCHAR(255) NOT NULL UNIQUE,
                    hashed_password TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    is_sudo BOOLEAN DEFAULT 0,
                    password_reset_at DATETIME,
                    telegram_id BIGINT,
                    discord_webhook TEXT,
                    is_disabled BOOLEAN DEFAULT 0
                )
            """)
            print(f"{GREEN}Created admins table in Pasarguard ✓{RESET}")
            time.sleep(0.5)

        for a in admins:
            cur.execute(
                """
                INSERT INTO admins
                (id, username, hashed_password, created_at, is_sudo,
                 password_reset_at, telegram_id, discord_webhook)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    username = VALUES(username),
                    hashed_password = VALUES(hashed_password),
                    created_at = VALUES(created_at),
                    is_sudo = VALUES(is_sudo),
                    password_reset_at = VALUES(password_reset_at),
                    telegram_id = VALUES(telegram_id),
                    discord_webhook = VALUES(discord_webhook)
                """,
                (
                    a["id"], a["username"], a["hashed_password"],
                    a["created_at"], a.get("is_sudo", 0), a.get("password_reset_at"),
                    a.get("telegram_id"), a.get("discord_webhook"),
                ),
            )
    pasarguard_conn.commit()
    return len(admins)

# Migration: Default Group
def ensure_default_group(pasarguard_conn):
    """Ensure default group exists and create table if necessary."""
    # ... (Full implementation of ensure_default_group) ...
    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'groups'")
        if cur.fetchone() is None:
            cur.execute("CREATE TABLE groups (id INT PRIMARY KEY, name VARCHAR(255) NOT NULL UNIQUE, is_disabled BOOLEAN DEFAULT 0)")
            print(f"{GREEN}Created groups table in Pasarguard ✓{RESET}")
            time.sleep(0.5)
        
        cur.execute("SELECT COUNT(*) AS cnt FROM groups WHERE id = 1")
        if cur.fetchone()["cnt"] == 0:
            cur.execute("INSERT INTO groups (id, name, is_disabled) VALUES (1, 'DefaultGroup', 0)")
            print(f"{GREEN}Created default group (ID 1) in Pasarguard ✓{RESET}")
            time.sleep(0.5)
    pasarguard_conn.commit()

# Migration: Default Core Config
def ensure_default_core_config(pasarguard_conn):
    """Ensure default core config exists and create table if necessary."""
    # ... (Full implementation of ensure_default_core_config) ...
    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'core_configs'")
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE core_configs (
                    id INT PRIMARY KEY,
                    created_at DATETIME NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    config JSON NOT NULL,
                    exclude_inbound_tags TEXT,
                    fallbacks_inbound_tags TEXT
                )
            """)
            print(f"{GREEN}Created core_configs table in Pasarguard ✓{RESET}")
            time.sleep(0.5)
        
        cur.execute("SELECT COUNT(*) AS cnt FROM core_configs WHERE id = 1")
        if cur.fetchone()["cnt"] == 0:
            cfg = {"log": {"loglevel": "warning"}, "inbounds": [],
                   "outbounds": [{"protocol": "freedom", "tag": "DIRECT"}, {"protocol": "blackhole", "tag": "BLOCK"}],
                   "routing": {"rules": [{"ip": ["geoip:private"], "outboundTag": "BLOCK", "type": "field"}]}
            }
            cur.execute(
                """
                INSERT INTO core_configs
                (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
                VALUES (1, NOW(), 'ASiS SK', %s, '', '')
                """,
                safe_json(cfg),
            )
            print(f"{GREEN}Created placeholder default core config (ID 1) in Pasarguard ✓{RESET}")
            time.sleep(0.5)
    pasarguard_conn.commit()

# Migration: Xray Config
def migrate_xray_config(pasarguard_conn, xray_config):
    """Migrate xray_config.json to core_configs (ID 1) and backup existing config."""
    # ... (Full implementation of migrate_xray_config) ...
    with pasarguard_conn.cursor() as cur:
        cur.execute("SELECT MAX(id) AS max_id FROM core_configs")
        max_id = cur.fetchone()["max_id"]
        backup_id = (max_id if max_id else 1) + 1

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
                    backup_id,
                    f"Backup_before_Marzban_Migration_{datetime.date.today()}",
                    existing["config"],
                    existing["exclude_inbound_tags"],
                    existing["fallbacks_inbound_tags"],
                ),
            )
            print(f"{GREEN}Backup created as ID {backup_id} ✓{RESET}")
            time.sleep(0.5)

        cur.execute(
            """
            INSERT INTO core_configs
            (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
            VALUES (%s, NOW(), %s, %s, '', '')
            ON DUPLICATE KEY UPDATE
                name = VALUES(name),
                config = VALUES(config),
                created_at = NOW()
            """,
            (1, "Marzban Migrated Config", safe_json(xray_config)),
        )
    pasarguard_conn.commit()
    return 1

# Migration: Inbounds
def migrate_inbounds_and_associate(marzban_conn, pasarguard_conn):
    """Migrate inbounds and associate with default group (ID 1)."""
    # ... (Full implementation of migrate_inbounds_and_associate) ...
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM inbounds")
        inbounds = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'inbounds'")
        if cur.fetchone() is None:
            cur.execute("CREATE TABLE inbounds (id INT PRIMARY KEY, tag VARCHAR(255) NOT NULL UNIQUE)")
            print(f"{GREEN}Created inbounds table in Pasarguard ✓{RESET}")
            time.sleep(0.5)

        cur.execute("SHOW TABLES LIKE 'inbounds_groups_association'")
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE inbounds_groups_association (
                    inbound_id INT, group_id INT, PRIMARY KEY (inbound_id, group_id),
                    FOREIGN KEY (inbound_id) REFERENCES inbounds(id) ON DELETE CASCADE,
                    FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
                )
            """)
            print(f"{GREEN}Created inbounds_groups_association table in Pasarguard ✓{RESET}")
            time.sleep(0.5)

        for i in inbounds:
            cur.execute(
                """
                INSERT INTO inbounds (id, tag) VALUES (%s,%s) ON DUPLICATE KEY UPDATE tag = VALUES(tag)
                """,
                (i["id"], i["tag"])
            )
            cur.execute(
                """
                INSERT IGNORE INTO inbounds_groups_association (inbound_id, group_id) VALUES (%s,1)
                """,
                (i["id"],)
            )
    pasarguard_conn.commit()
    return len(inbounds)

# Migration: Hosts
def migrate_hosts(marzban_conn, pasarguard_conn):
    """Migrate hosts with ALPN fix and default values for new Pasarguard fields."""
    # ... (Full implementation of migrate_hosts) ...
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM hosts")
        hosts = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'hosts'")
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE hosts (
                    id INT PRIMARY KEY, remark VARCHAR(255), address VARCHAR(255), port INT, inbound_tag VARCHAR(255),
                    sni TEXT, host TEXT, security VARCHAR(50), alpn TEXT, fingerprint TEXT,
                    allowinsecure BOOLEAN DEFAULT 0, is_disabled BOOLEAN DEFAULT 0, path TEXT,
                    random_user_agent BOOLEAN DEFAULT 0, use_sni_as_host BOOLEAN DEFAULT 0, priority INT DEFAULT 0,
                    http_headers TEXT, transport_settings JSON, mux_settings JSON, noise_settings JSON,
                    fragment_settings JSON, status VARCHAR(50)
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
                    remark = VALUES(remark), address = VALUES(address), port = VALUES(port), inbound_tag = VALUES(inbound_tag),
                    sni = VALUES(sni), host = VALUES(host), security = VALUES(security), alpn = VALUES(alpn),
                    fingerprint = VALUES(fingerprint), allowinsecure = VALUES(allowinsecure), is_disabled = VALUES(is_disabled),
                    path = VALUES(path), random_user_agent = VALUES(random_user_agent), use_sni_as_host = VALUES(use_sni_as_host),
                    priority = VALUES(priority), http_headers = VALUES(http_headers), transport_settings = VALUES(transport_settings),
                    mux_settings = VALUES(mux_settings), noise_settings = VALUES(noise_settings), fragment_settings = VALUES(fragment_settings),
                    status = VALUES(status)
                """,
                (
                    h["id"], h["remark"], h["address"], h["port"], h["inbound_tag"],
                    h["sni"], h["host"], h["security"], safe_alpn(h.get("alpn")),
                    h.get("fingerprint"), h.get("allowinsecure", 0), h.get("is_disabled", 0),
                    h.get("path"), h.get("random_user_agent", 0), h.get("use_sni_as_host", 0),
                    h.get("priority", 0), safe_json(h.get("http_headers")),
                    safe_json(h.get("transport_settings")), safe_json(h.get("mux_settings")),
                    safe_json(h.get("noise_settings")), safe_json(h.get("fragment_settings")),
                    h.get("status")
                ),
            )
    pasarguard_conn.commit()
    return len(hosts)

# Migration: Nodes
def migrate_nodes(marzban_conn, pasarguard_conn):
    """Migrate nodes and link them to default core config (ID 1)."""
    # ... (Full implementation of migrate_nodes) ...
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM nodes")
        nodes = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'nodes'")
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE nodes (
                    id INT PRIMARY KEY, name VARCHAR(255) NOT NULL, address VARCHAR(255) NOT NULL, port INT,
                    status VARCHAR(50), last_status_change DATETIME, message TEXT, created_at DATETIME NOT NULL,
                    uplink BIGINT, downlink BIGINT, xray_version VARCHAR(50), usage_coefficient FLOAT,
                    node_version VARCHAR(50), connection_type VARCHAR(50), server_ca TEXT, keep_alive BOOLEAN DEFAULT 0,
                    max_logs INT DEFAULT 1000, core_config_id INT DEFAULT 1, gather_logs BOOLEAN DEFAULT 1,
                    FOREIGN KEY (core_config_id) REFERENCES core_configs(id)
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
                    name = VALUES(name), address = VALUES(address), port = VALUES(port), status = VALUES(status),
                    last_status_change = VALUES(last_status_change), message = VALUES(message), created_at = VALUES(created_at),
                    uplink = VALUES(uplink), downlink = VALUES(downlink), xray_version = VALUES(xray_version),
                    usage_coefficient = VALUES(usage_coefficient), node_version = VALUES(node_version), connection_type = VALUES(connection_type),
                    server_ca = VALUES(server_ca), keep_alive = VALUES(keep_alive), max_logs = VALUES(max_logs),
                    core_config_id = 1, gather_logs = 1
                """,
                (
                    n["id"], n["name"], n["address"], n["port"], n["status"],
                    n.get("last_status_change"), n["message"], n["created_at"],
                    n.get("uplink"), n.get("downlink"), n.get("xray_version"), n["usage_coefficient"],
                    n.get("node_version"), n.get("connection_type"), n.get("server_ca", ""),
                    n.get("keep_alive", 0), n.get("max_logs", 1000)
                ),
            )
    pasarguard_conn.commit()
    return len(nodes)

# Migration: Users and Proxies
def migrate_users_and_proxies(marzban_conn, pasarguard_conn):
    """Migrate users, their proxies, and merge proxy settings into JSON column."""
    # ... (Full implementation of migrate_users_and_proxies) ...
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM users")
        users = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'users'")
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE users (
                    id INT PRIMARY KEY, username VARCHAR(255) NOT NULL UNIQUE, status VARCHAR(50), used_traffic BIGINT,
                    data_limit BIGINT, created_at DATETIME NOT NULL, admin_id INT, data_limit_reset_strategy VARCHAR(50),
                    sub_revoked_at DATETIME, note TEXT, online_at DATETIME, edit_at DATETIME, on_hold_timeout DATETIME,
                    on_hold_expire_duration INT, auto_delete_in_days INT, last_status_change DATETIME, expire DATETIME,
                    proxy_settings JSON, groups TEXT
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
                try: s = json.loads(p["settings"])
                except: s = {}; continue

                typ = p["type"].lower()
                if typ == "vmess": proxy_cfg["vmess"] = {"id": s.get("id")}
                elif typ == "vless": proxy_cfg["vless"] = {"id": s.get("id"), "flow": s.get("flow", "")}
                elif typ == "trojan": proxy_cfg["trojan"] = {"password": s.get("password")}
                elif typ == "shadowsocks": proxy_cfg["shadowsocks"] = {"password": s.get("password"), "method": s.get("method")}

            expire_dt = None
            if u["expire"]:
                try: expire_dt = datetime.datetime.fromtimestamp(u["expire"])
                except: pass
            
            groups_text = u.get("groups") if u.get("groups") else "DefaultGroup" 
            used = u.get("used_traffic") or 0
            
            cur.execute(
                """
                INSERT INTO users
                (id, username, status, used_traffic, data_limit, created_at,
                 admin_id, data_limit_reset_strategy, sub_revoked_at, note,
                 online_at, edit_at, on_hold_timeout, on_hold_expire_duration,
                 auto_delete_in_days, last_status_change, expire, proxy_settings, groups)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    username = VALUES(username), status = VALUES(status), used_traffic = VALUES(used_traffic),
                    data_limit = VALUES(data_limit), created_at = VALUES(created_at), admin_id = VALUES(admin_id),
                    data_limit_reset_strategy = VALUES(data_limit_reset_strategy), sub_revoked_at = VALUES(sub_revoked_at),
                    note = VALUES(note), online_at = VALUES(online_at), edit_at = VALUES(edit_at),
                    on_hold_timeout = VALUES(on_hold_timeout), on_hold_expire_duration = VALUES(on_hold_expire_duration),
                    auto_delete_in_days = VALUES(auto_delete_in_days), last_status_change = VALUES(last_status_change),
                    expire = VALUES(expire), proxy_settings = VALUES(proxy_settings), groups = VALUES(groups)
                """,
                (
                    u["id"], u["username"], u["status"], used, u["data_limit"],
                    u["created_at"], u["admin_id"], u["data_limit_reset_strategy"],
                    u["sub_revoked_at"], u["note"], u["online_at"], u["edit_at"],
                    u.get("on_hold_timeout"), u.get("on_hold_expire_duration"), u.get("auto_delete_in_days"),
                    u.get("last_status_change"), expire_dt, safe_json(proxy_cfg), groups_text,
                ),
            )
            total += 1

    pasarguard_conn.commit()
    return total

# --- Main/Utility Functions ---

def run_migration_steps(marzban_conn, pasarguard_conn, xray_config):
    """Executes all migration steps."""
    print(f"{CYAN}============================================================{RESET}")
    print(f"{CYAN}STARTING MIGRATION (DATA TRANSFER){RESET}")
    print(f"{CYAN}============================================================{RESET}")

    # 1. Admins
    print("Migrating admins...")
    admin_count = migrate_admins(marzban_conn, pasarguard_conn)
    print(f"{GREEN}{admin_count} admin(s) migrated ✓{RESET}")

    # 2. Default Group
    print("Ensuring default group...")
    ensure_default_group(pasarguard_conn)
    print(f"{GREEN}Default group ensured ✓{RESET}")

    # 3. Default Core Config
    print("Ensuring default core config placeholder...")
    ensure_default_core_config(pasarguard_conn)
    print(f"{GREEN}Default core config ensured ✓{RESET}")

    # 4. Xray Config (Core Config ID 1)
    print("Migrating xray_config.json to core_configs (ID 1)...")
    migrate_xray_config(pasarguard_conn, xray_config)
    print(f"{GREEN}xray_config.json migrated/backed up successfully ✓{RESET}")

    # 5. Inbounds and Association
    print("Migrating inbounds and linking to Default Group...")
    inbound_count = migrate_inbounds_and_associate(marzban_conn, pasarguard_conn)
    print(f"{GREEN}{inbound_count} inbound(s) migrated and linked ✓{RESET}")

    # 6. Hosts
    print("Migrating hosts (applying ALPN and new Pasarguard fields)...")
    host_count = migrate_hosts(marzban_conn, pasarguard_conn)
    print(f"{GREEN}{host_count} host(s) migrated ✓{RESET}")

    # 7. Nodes (Linked to Core Config ID 1)
    print("Migrating nodes (linking to Core Config ID 1)...")
    node_count = migrate_nodes(marzban_conn, pasarguard_conn)
    print(f"{GREEN}{node_count} node(s) migrated ✓{RESET}")

    # 8. Users and Proxies (Merging proxies into user JSON)
    print("Migrating users and proxy settings...")
    user_count = migrate_users_and_proxies(marzban_conn, pasarguard_conn)
    print(f"{GREEN}{user_count} user(s) migrated with proxy settings ✓{RESET}")

    print(f"{CYAN}============================================================{RESET}")
    print(f"{GREEN}MIGRATION COMPLETED SUCCESSFULLY! ✓{RESET}")
    print("Please restart Pasarguard and Xray services.")
    print(f"{CYAN}============================================================{RESET}")

def check_dependencies():
    """Check and install required dependencies."""
    print(f"{CYAN}Checking dependencies...{RESET}")
    python_deps = ["pymysql", "python-dotenv", "paramiko"]
    for pkg in python_deps:
        try:
            __import__(pkg.replace('-', '_'))
        except ImportError:
            print(f"Installing Python package: {pkg}")
            try:
                subprocess.run(f"pip3 install {pkg}", shell=True, check=True, stdout=subprocess.DEVNULL)
                print(f"{GREEN}Python package {pkg} installed ✓{RESET}")
            except subprocess.CalledProcessError as e:
                print(f"{RED}Error installing {pkg}. Please install manually: pip3 install {pkg}. Error: {e}{RESET}")
                sys.exit(1)
    print(f"{GREEN}All dependencies are installed/checked ✓{RESET}")
    time.sleep(0.5)

def clear_screen():
    """Clear the terminal screen."""
    os.system("clear")

def display_menu():
    """Display the main menu with a styled header."""
    clear_screen()
    print(f"{CYAN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓")
    print(f"┃{YELLOW}          Power By: ASiSSK               {CYAN}┃")
    print(f"┃{YELLOW}          Marz ➜ Pasarguard              {CYAN}┃")
    print(f"┃{YELLOW}              v2.0.0 (SSH)               {CYAN}┃")
    print(f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛{RESET}")
    print()
    print("Menu:")
    print("1. Change Database and phpMyAdmin Ports (Pasarguard Local)")
    print("2. Migrate Marzban Locally (Source and Target on this server)")
    print("3. Migrate Marzban Remotely via SSH (Source on Remote Server)")
    print("4. Exit")
    print()

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
                r'SQLALCHEMY_DATABASE_URL="mysql\+(asyncmy|pymysql)://([^:]+):([^@]+)@127\.0\.0\.1:\d+/[^"]+"',
                lambda match: re.sub(r':\d+/', f':{db_port}/', match.group(0)),
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
            content = re.sub(r'(--port=)\d+', r'\g<1>' + db_port, content)
            content = re.sub(r'PMA_PORT: \d+', f'PMA_PORT: {db_port}', content)
            content = re.sub(r'APACHE_PORT: \d+', f'APACHE_PORT: {apache_port}', content)
            
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

def migrate_marzban_remotely():
    """Migrate data from a remote Marzban server via SSH to local Pasarguard."""
    clear_screen()
    print(f"{CYAN}=== Migrate Marzban Remotely via SSH ==={RESET}")
    print(f"{YELLOW}Pasarguard must be installed and running on this local server.{RESET}")

    # 1. Get SSH details
    ssh_host = input("Enter Marzban Server IP/Host: ").strip()
    ssh_port = input("Enter Marzban SSH Port (Default: 22): ").strip() or "22"
    ssh_user = input("Enter Marzban SSH User (e.g., root): ").strip()
    ssh_pass = input("Enter Marzban SSH Password: ").strip()

    if not all([ssh_host, ssh_user, ssh_pass]):
        print(f"{RED}Error: SSH details are required.{RESET}")
        input("Press Enter to return to the menu...")
        return False
        
    ssh_config = {
        'host': ssh_host,
        'port': int(ssh_port),
        'user': ssh_user,
        'password': ssh_pass,
        'env_path': MARZBAN_ENV_PATH_DEFAULT,
        'xray_path': XRAY_CONFIG_PATH_DEFAULT
    }

    marzban_conn, pasarguard_conn = None, None
    try:
        # 2. Get Marzban config remotely
        marzban_config, xray_config = get_remote_marzban_config(ssh_config)
        
        # 3. Get Pasarguard config locally
        pasarguard_config = get_local_db_config(PASARGUARD_ENV_PATH, "Pasarguard (Local Target)")
        
        # 4. Connect to databases
        print(f"{CYAN}Connecting to databases...{RESET}")
        # NOTE: Marzban DB must be remotely accessible (firewall open & user permission)
        marzban_conn = connect(marzban_config)
        pasarguard_conn = connect(pasarguard_config)

        # 5. Execute Migration Steps
        run_migration_steps(marzban_conn, pasarguard_conn, xray_config)
        
    except Exception as e:
        print(f"{RED}Error during remote migration: {str(e)}{RESET}")
        input("Press Enter to return to the menu...")
        return False
    finally:
        if marzban_conn:
            marzban_conn.close()
        if pasarguard_conn:
            pasarguard_conn.close()

    input("Press Enter to return to the menu...")
    return True

def migrate_marzban_locally():
    """Migrate data from LOCAL Marzban to LOCAL Pasarguard."""
    clear_screen()
    print(f"{CYAN}=== Migrate Marzban Locally (This Server) ==={RESET}")

    marzban_conn, pasarguard_conn = None, None
    try:
        print(f"Loading credentials from .env files...")
        marzban_config = get_local_db_config(MARZBAN_ENV_PATH_DEFAULT, "Marzban (Local Source)")
        pasarguard_config = get_local_db_config(PASARGUARD_ENV_PATH, "Pasarguard (Local Target)")

        if (marzban_config['host'] == pasarguard_config['host'] and
            marzban_config['port'] == pasarguard_config['port'] and
            marzban_config['db'] == pasarguard_config['db']):
            print(f"{RED}Error: Marzban and Pasarguard are using the same database. Please change the Pasarguard database port using option 1.{RESET}")
            input("Press Enter to return to the menu...")
            return False

        print(f"{CYAN}Connecting to databases...{RESET}")
        marzban_conn = connect(marzban_config)
        pasarguard_conn = connect(pasarguard_config)
        
        # Get Xray config locally
        print("Reading local xray_config.json...")
        xray_config = read_local_xray_config()
        print(f"{GREEN}Successfully read local xray_config.json ✓{RESET}")

        # Execute Migration Steps
        run_migration_steps(marzban_conn, pasarguard_conn, xray_config)

    except Exception as e:
        print(f"{RED}Error during local migration: {str(e)}{RESET}")
        input("Press Enter to return to the menu...")
        return False
    finally:
        if marzban_conn:
            marzban_conn.close()
        if pasarguard_conn:
            pasarguard_conn.close()

    input("Press Enter to return to the menu...")
    return True

# --- Main execution loop ---

def main():
    """Main function to run the menu-driven program."""
    if os.geteuid() != 0:
        print(f"{RED}Error: This script must be run as root (using sudo).{RESET}")
        sys.exit(1)
        
    check_dependencies()

    while True:
        display_menu()
        choice = input("Enter your choice (1-4): ").strip()

        if choice == "1":
            change_db_port()
        elif choice == "2":
            migrate_marzban_locally()
        elif choice == "3":
            migrate_marzban_remotely()
        elif choice == "4":
            print(f"{CYAN}Exiting... Thank you for using Marz ➜ Pasarguard v2.0.0!{RESET}")
            sys.exit(0)
        else:
            print(f"{RED}Invalid choice. Please enter 1, 2, 3, or 4.{RESET}")
            input("Press Enter to continue...")

if __name__ == "__main__":
    if sys.version_info < (3, 6):
        print("Python 3.6+ required.")
        sys.exit(1)
    main()
