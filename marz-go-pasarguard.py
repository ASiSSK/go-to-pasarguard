#!/usr/bin/env python3
"""
Marzban to Pasarguard Migration Menu
Version: 2.0.4 (Enhanced)
A tool to change ports and migrate data from Marzban to Pasarguard,
supporting MySQL/MariaDB and PostgreSQL/TimescaleDB.
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
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from dotenv import dotenv_values

# تلاش برای ایمپورت کردن psycopg2
try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

try:
    from dateutil.parser import parse as parse_date
    DATEUTIL_AVAILABLE = True
except ImportError:
    DATEUTIL_AVAILABLE = False

# --- Setup logging ---
LOG_DIR = Path("/var/log/asissk")
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'asissk_migration.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ANSI color codes
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"

# Symbols for UX
CHECKMARK = f"{GREEN}✔{RESET}"
CROSSMARK = f"{RED}✘{RESET}"
DB_ICON = "💾 "
FILE_ICON = "🗃 "
CONFIG_ICON = "⚙ "

# Default paths
MARZBAN_ENV_PATH = "/opt/marzban/.env"
PASARGUARD_ENV_PATH = "/opt/pasarguard/.env"
DOCKER_COMPOSE_FILE_PATH = "/opt/pasarguard/docker-compose.yml"
XRAY_CONFIG_PATH = "/var/lib/marzban/xray_config.json"

# --- Helper Functions ---
def safe_alpn(value: Optional[Any]) -> Optional[str]:
    """Handle ALPN values safely, including lists and JSON."""
    if not value or str(value).strip().lower() in ["none", "null", ""]:
        return None
    try:
        if isinstance(value, (list, dict)):
            return json.dumps(value)
        return str(value).strip()
    except:
        return None

def safe_json(value: Any) -> Optional[str]:
    """Safely convert value to JSON string."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            json.loads(value)
        return json.dumps(value) if not isinstance(value, str) else value
    except:
        return None

def load_env_file(env_path: str) -> Dict[str, str]:
    """Load environment file and validate."""
    env_path = Path(env_path)
    if not env_path.exists():
        logger.error(f"Environment file {env_path} not found")
        raise FileNotFoundError(f"{CROSSMARK} Environment file {env_path} not found")
    if not env_path.is_file() or not os.access(env_path, os.R_OK):
        logger.error(f"No read permission for {env_path}")
        raise PermissionError(f"{CROSSMARK} No read permission for {env_path}")
    
    env = dotenv_values(env_path)
    if not env.get("SQLALCHEMY_DATABASE_URL"):
        with open(env_path, 'r', encoding='utf-8') as f:
            logger.error(f"SQLALCHEMY_DATABASE_URL not found in {env_path}")
            print(f"{RED}Content of {env_path}:{RESET}\n{f.read()}")
        raise ValueError(f"{CROSSMARK} SQLALCHEMY_DATABASE_URL not found in {env_path}")
    logger.info(f"Loaded environment file {env_path}")
    return env

# --- Database Helpers ---

def parse_sqlalchemy_url(url: str) -> Dict[str, Any]:
    """Parse SQLALCHEMY_DATABASE_URL for MySQL and PostgreSQL."""
    mysql_pattern = r"mysql\+(asyncmy|pymysql)://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)"
    match = re.match(mysql_pattern, url)
    if match:
        return {
            "db_type": "mysql",
            "user": match.group(2),
            "password": match.group(3),
            "host": match.group(4),
            "port": int(match.group(5)),
            "db": match.group(6)
        }
    
    postgres_pattern = r"postgresql\+(psycopg2|asyncpg)://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)"
    match = re.match(postgres_pattern, url)
    if match:
        if not PSYCOPG2_AVAILABLE:
            logger.error("PostgreSQL URL detected, but 'psycopg2-binary' package is not installed")
            print(f"{RED}{CROSSMARK} PostgreSQL URL detected, but 'psycopg2-binary' package is not installed.{RESET}")
            print(f"{YELLOW}Please install it: pip3 install psycopg2-binary{RESET}")
            sys.exit(1)
        return {
            "db_type": "postgres",
            "user": match.group(2),
            "password": match.group(3),
            "host": match.group(4),
            "port": int(match.group(5)),
            "db": match.group(6)
        }
        
    logger.error(f"Invalid or unsupported SQLALCHEMY_DATABASE_URL: {url}")
    raise ValueError(f"{CROSSMARK} Invalid or unsupported SQLALCHEMY_DATABASE_URL: {url}")

def get_db_config(env_path: str, name: str) -> Dict[str, Any]:
    """Get database config from .env file."""
    try:
        env = load_env_file(env_path)
        logger.info(f"Reading {name} config from: {env_path}")
        print(f"{CYAN}{CONFIG_ICON} Reading {name} config from: {env_path}{RESET}")
        sqlalchemy_url = env.get("SQLALCHEMY_DATABASE_URL")
        config = parse_sqlalchemy_url(sqlalchemy_url)
        
        if config["db_type"] == "mysql":
            config["charset"] = "utf8mb4"
            config["cursorclass"] = pymysql.cursors.DictCursor
        
        print(f"{CYAN}=== {name.upper()} DATABASE SETTINGS ({config['db_type']}) ==={RESET}")
        print(f"Using: host={config['host']}, port={config['port']}, user={config['user']}, db={config['db']}")
        return config
    except Exception as e:
        logger.error(f"Error loading {name} config: {str(e)}")
        print(f"{RED}{CROSSMARK} Error loading {name} config: {str(e)}{RESET}")
        sys.exit(1)

def read_xray_config() -> Dict[str, Any]:
    """Read xray_config.json from /var/lib/marzban."""
    xray_path = Path(XRAY_CONFIG_PATH)
    if not xray_path.exists():
        logger.error(f"xray_config.json not found at {XRAY_CONFIG_PATH}")
        raise FileNotFoundError(f"{CROSSMARK} xray_config.json not found at {XRAY_CONFIG_PATH}")
    if not os.access(xray_path, os.R_OK):
        logger.error(f"No read permission for {XRAY_CONFIG_PATH}")
        raise PermissionError(f"{CROSSMARK} No read permission for {XRAY_CONFIG_PATH}")
    try:
        with open(xray_path, 'r', encoding='utf-8') as file:
            logger.info(f"Successfully read {XRAY_CONFIG_PATH}")
            print(f"{GREEN}{FILE_ICON} Successfully read {XRAY_CONFIG_PATH} {CHECKMARK}{RESET}")
            return json.load(file)
    except Exception as e:
        logger.error(f"Error reading xray_config.json: {str(e)}")
        raise Exception(f"{CROSSMARK} Error reading xray_config.json: {str(e)}")

def connect(cfg: Dict[str, Any]):
    """Connect to database (MySQL or PostgreSQL)."""
    db_type = cfg["db_type"]
    try:
        if db_type == "mysql":
            conn_args = {
                "host": cfg["host"],
                "port": cfg["port"],
                "user": cfg["user"],
                "password": cfg["password"],
                "database": cfg["db"],
                "charset": cfg["charset"],
                "cursorclass": cfg["cursorclass"]
            }
            conn = pymysql.connect(**conn_args)
        elif db_type == "postgres":
            if not PSYCOPG2_AVAILABLE:
                logger.error("psycopg2 driver not found for PostgreSQL connection")
                raise ImportError(f"{CROSSMARK} psycopg2 driver not found for PostgreSQL connection")
            conn_args = {
                "host": cfg["host"],
                "port": cfg["port"],
                "user": cfg["user"],
                "password": cfg["password"],
                "dbname": cfg["db"]
            }
            conn = psycopg2.connect(**conn_args)
            conn.cursor_factory = psycopg2.extras.DictCursor
        else:
            logger.error(f"Unsupported db_type: {db_type}")
            raise ValueError(f"{CROSSMARK} Unsupported db_type: {db_type}")
            
        logger.info(f"Connected to {cfg['db']}@{cfg['host']}:{cfg['port']} ({db_type})")
        print(f"{GREEN}{DB_ICON} Connected to {cfg['db']}@{cfg['host']}:{cfg['port']} ({db_type}) {CHECKMARK}{RESET}")
        time.sleep(0.5)
        return conn, db_type
    except Exception as e:
        logger.error(f"Connection failed ({db_type}): {str(e)}")
        raise Exception(f"{CROSSMARK} Connection failed ({db_type}): {str(e)}")

# --- SQL Dialect Helpers ---

def get_table_exists_sql(db_type: str, table_name: str) -> str:
    """Return SQL to check if a table exists."""
    if db_type == "mysql":
        return f"SHOW TABLES LIKE '{table_name}'"
    elif db_type == "postgres":
        return f"SELECT 1 FROM information_schema.tables WHERE table_name = '{table_name}' AND table_schema = current_schema()"
    logger.error(f"Unsupported db_type: {db_type}")
    raise ValueError(f"{CROSSMARK} Unsupported db_type: {db_type}")

def get_upsert_sql(db_type: str, table: str, columns: list, conflict_key: str) -> str:
    """Return SQL for INSERT ON DUPLICATE KEY UPDATE (MySQL) or ON CONFLICT (Postgres)."""
    cols_str = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    
    if db_type == "mysql":
        updates = ", ".join([f"{col} = %s" for col in columns if col != conflict_key])
        return f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {updates}"
    elif db_type == "postgres":
        updates = ", ".join([f"{col} = EXCLUDED.{col}" for col in columns if col != conflict_key])
        return f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders}) ON CONFLICT ({conflict_key}) DO UPDATE SET {updates}"
    logger.error(f"Unsupported db_type: {db_type}")
    raise ValueError(f"{CROSSMARK} Unsupported db_type: {db_type}")

# --- Migration Functions ---

def migrate_admins(marzban_conn, pasarguard_conn, pasarguard_db_type):
    """Migrate admins from Marzban to Pasarguard."""
    logger.info("Migrating admins")
    print(f"{CYAN}Migrating admins...{RESET}")
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM admins")
        admins = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute(get_table_exists_sql(pasarguard_db_type, "admins"))
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE admins (
                    id INT PRIMARY KEY,
                    username VARCHAR(255) NOT NULL,
                    hashed_password TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    is_sudo BOOLEAN DEFAULT FALSE,
                    password_reset_at TIMESTAMP,
                    telegram_id BIGINT,
                    discord_webhook TEXT
                )
            """)
            logger.info("Created admins table in Pasarguard")
            print(f"{GREEN}{DB_ICON} Created admins table in Pasarguard {CHECKMARK}{RESET}")
            time.sleep(0.5)

        columns = ["id", "username", "hashed_password", "created_at", "is_sudo", "password_reset_at", "telegram_id", "discord_webhook"]
        upsert_sql = get_upsert_sql(pasarguard_db_type, "admins", columns, "id")

        for a in admins:
            values = (
                a["id"], a["username"], a["hashed_password"],
                a["created_at"], a["is_sudo"], a["password_reset_at"],
                a["telegram_id"], a["discord_webhook"]
            )
            if pasarguard_db_type == "mysql":
                update_values = (
                    a["username"], a["hashed_password"],
                    a["created_at"], a["is_sudo"], a["password_reset_at"],
                    a["telegram_id"], a["discord_webhook"]
                )
                cur.execute(upsert_sql, values + update_values)
            else:  # Postgres
                cur.execute(upsert_sql, values)
                
    pasarguard_conn.commit()
    logger.info(f"Migrated {len(admins)} admin(s)")
    return len(admins)

def ensure_default_group(pasarguard_conn, pasarguard_db_type):
    """Ensure default group exists in Pasarguard."""
    logger.info("Ensuring default group")
    print(f"{CYAN}Creating default group if not exists...{RESET}")
    with pasarguard_conn.cursor() as cur:
        cur.execute(get_table_exists_sql(pasarguard_db_type, "groups"))
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE groups (
                    id INT PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    is_disabled BOOLEAN DEFAULT FALSE
                )
            """)
            logger.info("Created groups table in Pasarguard")
            print(f"{GREEN}{DB_ICON} Created groups table in Pasarguard {CHECKMARK}{RESET}")
            time.sleep(0.5)
        
        cur.execute("SELECT COUNT(*) AS cnt FROM groups WHERE id = 1")
        if cur.fetchone()["cnt"] == 0:
            cur.execute("INSERT INTO groups (id, name, is_disabled) VALUES (1, 'DefaultGroup', FALSE)")
            logger.info("Created default group in Pasarguard")
            print(f"{GREEN}{DB_ICON} Created default group in Pasarguard {CHECKMARK}{RESET}")
            time.sleep(0.5)
    pasarguard_conn.commit()

def ensure_default_core_config(pasarguard_conn, pasarguard_db_type):
    """Ensure default core config exists in Pasarguard."""
    logger.info("Ensuring default core config")
    print(f"{CYAN}Creating default core config if not exists...{RESET}")
    with pasarguard_conn.cursor() as cur:
        cur.execute(get_table_exists_sql(pasarguard_db_type, "core_configs"))
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE core_configs (
                    id INT PRIMARY KEY,
                    created_at TIMESTAMP NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    config JSON NOT NULL,
                    exclude_inbound_tags TEXT,
                    fallbacks_inbound_tags TEXT
                )
            """)
            logger.info("Created core_configs table in Pasarguard")
            print(f"{GREEN}{DB_ICON} Created core_configs table in Pasarguard {CHECKMARK}{RESET}")
            time.sleep(0.5)
        
        cur.execute("SELECT COUNT(*) AS cnt FROM core_configs WHERE id = 1")
        if cur.fetchone()["cnt"] == 0:
            cfg = {
                "log": {"loglevel": "warning"},
                "inbounds": [{
                    "tag": "Shadowsocks TCP", "listen": "0.0.0.0", "port": 1080, "protocol": "shadowsocks",
                    "settings": {"clients": [], "network": "tcp,udp"}
                }],
                "outbounds": [{"protocol": "freedom", "tag": "DIRECT"}, {"protocol": "blackhole", "tag": "BLOCK"}],
                "routing": {"rules": [{"ip": ["geoip:private"], "outboundTag": "BLOCK", "type": "field"}]}
            }
            cur.execute(
                """
                INSERT INTO core_configs
                (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
                VALUES (1, NOW(), 'ASiS SK', %s, '', '')
                """,
                (json.dumps(cfg),),
            )
            logger.info("Created default core config 'ASiS SK' in Pasarguard")
            print(f"{GREEN}{CONFIG_ICON} Created default core config 'ASiS SK' in Pasarguard {CHECKMARK}{RESET}")
            time.sleep(0.5)
    pasarguard_conn.commit()

def migrate_xray_config(pasarguard_conn, pasarguard_db_type, xray_config):
    """Migrate xray_config.json to core_configs."""
    logger.info("Migrating xray_config.json to core_configs")
    print(f"{CYAN}Migrating xray_config.json to core_configs...{RESET}")
    with pasarguard_conn.cursor() as cur:
        cur.execute("SELECT * FROM core_configs WHERE id = 1")
        existing = cur.fetchone()
        if existing:
            config_data = existing.get("config") or existing[3]
            cur.execute(
                """
                INSERT INTO core_configs
                (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
                VALUES (%s, NOW(), %s, %s, %s, %s)
                """,
                (
                    existing["id"] + 1000, "Backup_ASiS_SK", config_data,
                    existing["exclude_inbound_tags"], existing["fallbacks_inbound_tags"],
                ),
            )
            logger.info(f"Backup created as 'Backup_ASiS_SK' with ID {existing['id'] + 1000}")
            print(f"{GREEN}{CONFIG_ICON} Backup created as 'Backup_ASiS_SK' with ID {existing['id'] + 1000} {CHECKMARK}{RESET}")
            time.sleep(0.5)

        if pasarguard_db_type == "mysql":
            sql = """
                INSERT INTO core_configs (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
                VALUES (%s, NOW(), %s, %s, '', '')
                ON DUPLICATE KEY UPDATE name = %s, config = %s, created_at = NOW()
            """
            cur.execute(sql, (1, "ASiS SK", json.dumps(xray_config), "ASiS SK", json.dumps(xray_config)))
        else:  # Postgres
            sql = """
                INSERT INTO core_configs (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
                VALUES (%s, NOW(), %s, %s, '', '')
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, config = EXCLUDED.config, created_at = NOW()
            """
            cur.execute(sql, (1, "ASiS SK", json.dumps(xray_config)))
            
    pasarguard_conn.commit()
    logger.info("xray_config.json migrated as 'ASiS SK' in core_configs")
    print(f"{GREEN}{CONFIG_ICON} xray_config.json migrated as 'ASiS SK' in core_configs {CHECKMARK}{RESET}")
    return 1

def migrate_inbounds_and_associate(marzban_conn, pasarguard_conn, pasarguard_db_type):
    """Migrate inbounds and associate with default group."""
    logger.info("Migrating inbounds")
    print(f"{CYAN}Migrating inbounds...{RESET}")
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM inbounds")
        inbounds = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute(get_table_exists_sql(pasarguard_db_type, "inbounds"))
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE inbounds (
                    id INT PRIMARY KEY,
                    tag VARCHAR(255) NOT NULL
                )
            """)
            logger.info("Created inbounds table in Pasarguard")
            print(f"{GREEN}{DB_ICON} Created inbounds table in Pasarguard {CHECKMARK}{RESET}")
            time.sleep(0.5)

        cur.execute(get_table_exists_sql(pasarguard_db_type, "inbounds_groups_association"))
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE inbounds_groups_association (
                    inbound_id INT,
                    group_id INT,
                    PRIMARY KEY (inbound_id, group_id)
                )
            """)
            logger.info("Created inbounds_groups_association table in Pasarguard")
            print(f"{GREEN}{DB_ICON} Created inbounds_groups_association table in Pasarguard {CHECKMARK}{RESET}")
            time.sleep(0.5)

        upsert_sql = get_upsert_sql(pasarguard_db_type, "inbounds", ["id", "tag"], "id")
        for i in inbounds:
            if pasarguard_db_type == "mysql":
                cur.execute(upsert_sql, (i["id"], i["tag"], i["tag"]))
            else:
                cur.execute(upsert_sql, (i["id"], i["tag"]))

        if pasarguard_db_type == "mysql":
            assoc_sql = "INSERT IGNORE INTO inbounds_groups_association (inbound_id, group_id) VALUES (%s, 1)"
        else:  # Postgres
            assoc_sql = "INSERT INTO inbounds_groups_association (inbound_id, group_id) VALUES (%s, 1) ON CONFLICT DO NOTHING"
            
        for i in inbounds:
            cur.execute(assoc_sql, (i["id"],))
            
    pasarguard_conn.commit()
    logger.info(f"Migrated {len(inbounds)} inbound(s)")
    return len(inbounds)

def migrate_hosts(marzban_conn, pasarguard_conn, pasarguard_db_type, safe_alpn_func):
    """Migrate hosts with ALPN fix and default values for optional fields."""
    logger.info("Migrating hosts")
    print(f"{CYAN}Migrating hosts (with smart ALPN fix)...{RESET}")
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM hosts")
        hosts = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute(get_table_exists_sql(pasarguard_db_type, "hosts"))
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE hosts (
                    id INT PRIMARY KEY, remark VARCHAR(255), address VARCHAR(255), port INT,
                    inbound_tag VARCHAR(255), sni TEXT, host TEXT, security VARCHAR(50),
                    alpn TEXT, fingerprint TEXT, allowinsecure BOOLEAN, is_disabled BOOLEAN,
                    path TEXT, random_user_agent BOOLEAN, use_sni_as_host BOOLEAN,
                    priority INT DEFAULT 0, http_headers JSON, transport_settings JSON,
                    mux_settings JSON, noise_settings JSON, fragment_settings JSON,
                    status VARCHAR(50)
                )
            """)
            logger.info("Created hosts table in Pasarguard")
            print(f"{GREEN}{DB_ICON} Created hosts table in Pasarguard (using JSON type) {CHECKMARK}{RESET}")
            time.sleep(0.5)

        columns = [
            "id", "remark", "address", "port", "inbound_tag", "sni", "host", "security", "alpn",
            "fingerprint", "allowinsecure", "is_disabled", "path", "random_user_agent",
            "use_sni_as_host", "priority", "http_headers", "transport_settings",
            "mux_settings", "noise_settings", "fragment_settings", "status"
        ]
        upsert_sql = get_upsert_sql(pasarguard_db_type, "hosts", columns, "id")

        for h in hosts:
            values = (
                h["id"], h["remark"], h["address"], h["port"], h["inbound_tag"],
                h["sni"], h["host"], h["security"], safe_alpn_func(h.get("alpn")),
                h["fingerprint"], h["allowinsecure"], h["is_disabled"], h.get("path"),
                h.get("random_user_agent", False), h.get("use_sni_as_host", False), h.get("priority", 0),
                safe_json(h.get("http_headers")), safe_json(h.get("transport_settings")),
                safe_json(h.get("mux_settings")), safe_json(h.get("noise_settings")),
                safe_json(h.get("fragment_settings")), h.get("status")
            )
            
            if pasarguard_db_type == "mysql":
                update_values = values[1:]
                cur.execute(upsert_sql, values + update_values)
            else:  # Postgres
                cur.execute(upsert_sql, values)
                
    pasarguard_conn.commit()
    logger.info(f"Migrated {len(hosts)} host(s)")
    return len(hosts)

def migrate_nodes(marzban_conn, pasarguard_conn, pasarguard_db_type):
    """Migrate nodes."""
    logger.info("Migrating nodes")
    print(f"{CYAN}Migrating nodes...{RESET}")
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM nodes")
        nodes = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute(get_table_exists_sql(pasarguard_db_type, "nodes"))
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE nodes (
                    id INT PRIMARY KEY, name VARCHAR(255) NOT NULL, address VARCHAR(255) NOT NULL,
                    port INT, status VARCHAR(50), last_status_change TIMESTAMP, message TEXT,
                    created_at TIMESTAMP NOT NULL, uplink BIGINT, downlink BIGINT,
                    xray_version VARCHAR(50), usage_coefficient FLOAT, node_version VARCHAR(50),
                    connection_type VARCHAR(50), server_ca TEXT, keep_alive BOOLEAN,
                    max_logs INT, core_config_id INT, gather_logs BOOLEAN
                )
            """)
            logger.info("Created nodes table in Pasarguard")
            print(f"{GREEN}{DB_ICON} Created nodes table in Pasarguard {CHECKMARK}{RESET}")
            time.sleep(0.5)

        columns = [
            "id", "name", "address", "port", "status", "last_status_change", "message",
            "created_at", "uplink", "downlink", "xray_version", "usage_coefficient",
            "node_version", "connection_type", "server_ca", "keep_alive", "max_logs",
            "core_config_id", "gather_logs"
        ]
        upsert_sql = get_upsert_sql(pasarguard_db_type, "nodes", columns, "id")

        for n in nodes:
            values = (
                n["id"], n["name"], n["address"], n["port"], n["status"],
                n["last_status_change"], n["message"], n["created_at"],
                n["uplink"], n["downlink"], n["xray_version"], n["usage_coefficient"],
                n["node_version"], n["connection_type"], n.get("server_ca", ""),
                n.get("keep_alive", False), n.get("max_logs", 1000), 1, True
            )
            
            if pasarguard_db_type == "mysql":
                update_values = values[1:]
                cur.execute(upsert_sql, values + update_values)
            else:
                cur.execute(upsert_sql, values)

    pasarguard_conn.commit()
    logger.info(f"Migrated {len(nodes)} node(s)")
    return len(nodes)

def migrate_users_and_proxies(marzban_conn, pasarguard_conn, pasarguard_db_type):
    """Migrate users and their proxy settings."""
    logger.info("Migrating users and proxy settings")
    print(f"{CYAN}Migrating users and proxy settings...{RESET}")
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM users")
        users = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        cur.execute(get_table_exists_sql(pasarguard_db_type, "users"))
        if cur.fetchone() is None:
            cur.execute("""
                CREATE TABLE users (
                    id INT PRIMARY KEY, username VARCHAR(255) NOT NULL, status VARCHAR(50),
                    used_traffic BIGINT, data_limit BIGINT, created_at TIMESTAMP NOT NULL,
                    admin_id INT, data_limit_reset_strategy VARCHAR(50),
                    sub_revoked_at TIMESTAMP, note TEXT, online_at TIMESTAMP,
                    edit_at TIMESTAMP, on_hold_timeout TIMESTAMP, on_hold_expire_duration INT,
                    auto_delete_in_days INT, last_status_change TIMESTAMP,
                    expire TIMESTAMP, proxy_settings JSON
                )
            """)
            logger.info("Created users table in Pasarguard")
            print(f"{GREEN}{DB_ICON} Created users table in Pasarguard (using JSON type) {CHECKMARK}{RESET}")
            time.sleep(0.5)

        columns = [
            "id", "username", "status", "used_traffic", "data_limit", "created_at",
            "admin_id", "data_limit_reset_strategy", "sub_revoked_at", "note",
            "online_at", "edit_at", "on_hold_timeout", "on_hold_expire_duration",
            "auto_delete_in_days", "last_status_change", "expire", "proxy_settings"
        ]
        upsert_sql = get_upsert_sql(pasarguard_db_type, "users", columns, "id")

        total = 0
        for u in users:
            with marzban_conn.cursor() as pcur:
                pcur.execute("SELECT * FROM proxies WHERE user_id = %s", (u["id"],))
                proxies = pcur.fetchall()

            proxy_cfg = {}
            for p in proxies:
                try:
                    s = json.loads(p["settings"])
                except:
                    logger.warning(f"Skipping proxy for user {u['id']} due to invalid JSON settings")
                    print(f"{YELLOW}⚠ Skipping proxy for user {u['id']} due to invalid JSON settings{RESET}")
                    continue
                    
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
                    if isinstance(u["expire"], (int, float)):
                        expire_dt = datetime.datetime.fromtimestamp(u["expire"])
                    elif isinstance(u["expire"], str):
                        if DATEUTIL_AVAILABLE:
                            expire_dt = parse_date(u["expire"])
                        else:
                            expire_dt = datetime.datetime.fromisoformat(u["expire"])
                    elif isinstance(u["expire"], datetime.datetime):
                        expire_dt = u["expire"]
                except Exception as e:
                    logger.warning(f"Could not parse expire date '{u['expire']}' for user {u['id']}: {e}")
                    print(f"{YELLOW}⚠ Could not parse expire date '{u['expire']}' for user {u['id']}. Setting to NULL. Error: {e}{RESET}")

            used = u["used_traffic"] or 0

            values = (
                u["id"], u["username"], u["status"], used, u["data_limit"],
                u["created_at"], u["admin_id"], u["data_limit_reset_strategy"],
                u["sub_revoked_at"], u["note"], u["online_at"], u["edit_at"],
                u["on_hold_timeout"], u["on_hold_expire_duration"], u["auto_delete_in_days"],
                u["last_status_change"], expire_dt, json.dumps(proxy_cfg)
            )

            if pasarguard_db_type == "mysql":
                update_values = values[1:]
                cur.execute(upsert_sql, values + update_values)
            else:  # Postgres
                cur.execute(upsert_sql, values)
            total += 1

    pasarguard_conn.commit()
    logger.info(f"Migrated {total} user(s)")
    return total

# --- Service Restart Function ---
def restart_services():
    """Restart Pasarguard and Xray services."""
    logger.info("Attempting to restart Pasarguard and Xray services")
    print(f"{CYAN}Restarting Pasarguard and Xray services...{RESET}")
    try:
        compose_file = Path(DOCKER_COMPOSE_FILE_PATH)
        if compose_file.exists():
            subprocess.run(f"docker compose -f {compose_file} down", shell=True, check=True)
            subprocess.run(f"docker compose -f {compose_file} up -d", shell=True, check=True)
            logger.info("Services restarted successfully")
            print(f"{GREEN}{CHECKMARK} Services restarted successfully{RESET}")
        else:
            logger.warning(f"Docker compose file {compose_file} not found")
            print(f"{RED}{CROSSMARK} Docker compose file {compose_file} not found{RESET}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to restart services: {e}")
        print(f"{RED}{CROSSMARK} Failed to restart services: {e}{RESET}")

# --- Main Functions ---

def check_dependencies():
    """Check and install required dependencies."""
    logger.info("Checking dependencies")
    print(f"{CYAN}{CONFIG_ICON} Checking dependencies...{RESET}")
    apt_deps = {"screen": "screen", "python3": "python3", "pip": "python3-pip"}
    missing_apt_deps = []

    for cmd, pkg in apt_deps.items():
        if subprocess.run(f"command -v {cmd}", shell=True, capture_output=True).returncode != 0:
            missing_apt_deps.append(pkg)

    if missing_apt_deps:
        logger.info(f"Installing missing APT dependencies: {', '.join(missing_apt_deps)}")
        print(f"{RED}{CROSSMARK} Missing APT dependencies: {', '.join(missing_apt_deps)}{RESET}")
        print(f"{CYAN}Installing missing dependencies...{RESET}")
        try:
            subprocess.run("apt-get update -qq", shell=True, check=True)
            subprocess.run(f"apt-get install -y -qq {' '.join(missing_apt_deps)}", shell=True, check=True)
            logger.info("APT dependencies installed successfully")
            print(f"{GREEN}{CHECKMARK} APT dependencies installed successfully{RESET}")
            time.sleep(0.5)
        except subprocess.CalledProcessError as e:
            logger.error(f"Error installing APT dependencies: {e}")
            print(f"{RED}{CROSSMARK} Error installing APT dependencies: {e}{RESET}")
            sys.exit(1)

    python_deps = ["pymysql", "python-dotenv", "python-dateutil"]
    if not PSYCOPG2_AVAILABLE:
        python_deps.append("psycopg2-binary")
        
    missing_pip_deps = []
    for pkg in python_deps:
        if subprocess.run(f"pip3 show {pkg}", shell=True, capture_output=True).returncode != 0:
            missing_pip_deps.append(pkg)

    if missing_pip_deps:
        logger.info(f"Installing missing Python packages: {', '.join(missing_pip_deps)}")
        print(f"{CYAN}Installing missing Python packages: {', '.join(missing_pip_deps)}{RESET}")
        try:
            subprocess.run(f"pip3 install {' '.join(missing_pip_deps)}", shell=True, check=True)
            logger.info("Python packages installed successfully")
            print(f"{GREEN}{CHECKMARK} Python packages installed {CHECKMARK}{RESET}")
            time.sleep(0.5)
            if "psycopg2-binary" in missing_pip_deps:
                try:
                    global PSYCOPG2_AVAILABLE, psycopg2, psycopg2_extras
                    psycopg2 = __import__("psycopg2")
                    psycopg2_extras = __import__("psycopg2.extras")
                    PSYCOPG2_AVAILABLE = True
                    logger.info("Successfully imported psycopg2 after installation")
                    print(f"{GREEN}{CHECKMARK} Successfully imported psycopg2 after installation{RESET}")
                except ImportError as e:
                    logger.error(f"Failed to import psycopg2 after installation: {e}")
                    print(f"{RED}{CROSSMARK} Failed to import psycopg2 after installation: {e}{RESET}")
                    sys.exit(1)
        except subprocess.CalledProcessError as e:
            logger.error(f"Error installing Python packages: {e}")
            print(f"{RED}{CROSSMARK} Error installing Python packages: {e}{RESET}")
            sys.exit(1)
            
    logger.info("All dependencies are installed")
    print(f"{GREEN}{CHECKMARK} All dependencies are installed {CHECKMARK}{RESET}")
    time.sleep(0.5)

def clear_screen():
    os.system("clear")

def display_menu():
    """Display the main menu with a styled header."""
    clear_screen()
    print(f"{CYAN}┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓")
    print(f"┃{YELLOW}          Power By: ASiSSK               {CYAN}┃")
    print(f"┃{YELLOW}      Marz ➜ Pasarguard (Multi-DB)       {CYAN}┃")
    print(f"┃{YELLOW}              v2.0.4 (Fix)               {CYAN}┃")
    print(f"┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛{RESET}")
    print()
    print("Menu:")
    print("1. Change Database and phpMyAdmin Ports")
    print("2. Migrate Marzban to Pasarguard (MySQL or PostgreSQL)")
    print("3. Exit")
    print()

def check_file_access():
    """Check access to .env and xray_config.json files."""
    logger.info("Checking file access")
    print(f"{CYAN}{FILE_ICON} Checking file access...{RESET}")
    files = [
        (MARZBAN_ENV_PATH, "Marzban .env"),
        (PASARGUARD_ENV_PATH, "Pasarguard .env"),
        (XRAY_CONFIG_PATH, "xray_config.json"),
        (DOCKER_COMPOSE_FILE_PATH, "docker-compose.yml")
    ]
    success = True
    for file_path, file_name in files:
        file_path = Path(file_path)
        if not file_path.exists():
            logger.error(f"{file_name} not found at {file_path}")
            print(f"{RED}{CROSSMARK} {file_name} not found at {file_path}{RESET}")
            success = False
        elif not os.access(file_path, os.R_OK):
            logger.error(f"No read permission for {file_name} at {file_path}")
            print(f"{RED}{CROSSMARK} No read permission for {file_name} at {file_path}{RESET}")
            success = False
        elif not os.access(file_path, os.W_OK):
            logger.warning(f"No write permission for {file_name} at {file_path}")
            print(f"{YELLOW}⚠ No write permission for {file_name} at {file_path}{RESET}")
            success = False
        else:
            logger.info(f"Access to {file_name} OK")
            print(f"{GREEN}{CHECKMARK} Access to {file_name} OK {CHECKMARK}{RESET}")
            time.sleep(0.5)
    return success

def change_db_port():
    """Change the database and phpMyAdmin ports in .env and docker-compose.yml files."""
    logger.info("Changing database and phpMyAdmin ports")
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
                logger.error(f"Invalid {name}. Must be between 1 and 65535")
                print(f"{RED}{CROSSMARK} Invalid {name}. Must be between 1 and 65535.{RESET}")
                success = False
                break

        if success:
            # Update .env file
            env_file = Path(PASARGUARD_ENV_PATH)
            if env_file.exists():
                with open(env_file, 'r', encoding='utf-8') as file:
                    content = file.read()
                
                if re.search(r'DB_PORT=\d+', content):
                    content = re.sub(r'DB_PORT=\d+', f'DB_PORT={db_port}', content)
                else:
                    content += f'\nDB_PORT={db_port}\n'
                
                # Update port in SQLALCHEMY_DATABASE_URL
                content = re.sub(
                    r'(@[^:]+:)\d+(/[^\"]+")',
                    rf'\g<1>{db_port}\2',
                    content
                )
                
                with open(env_file, 'w', encoding='utf-8') as file:
                    file.write(content)
                logger.info(f"Updated {env_file} with database port {db_port}")
                print(f"{GREEN}{FILE_ICON} Updated {env_file} with database port {db_port} {CHECKMARK}{RESET}")
                time.sleep(0.5)
            else:
                logger.error(f"File {env_file} not found")
                print(f"{RED}{CROSSMARK} File {env_file} not found{RESET}")
                success = False

            # Update docker-compose.yml
            compose_file = Path(DOCKER_COMPOSE_FILE_PATH)
            if compose_file.exists():
                with open(compose_file, 'r', encoding='utf-8') as file:
                    content = file.read()
                
                # MySQL/MariaDB command port
                if re.search(r'--port=\d+', content):
                    content = re.sub(r'--port=\d+', f'--port={db_port}', content)
                
                # PostgreSQL ports mapping
                content = re.sub(r'ports:\s*- "\d+:5432"', f'ports:\n      - "{db_port}:5432"', content, flags=re.MULTILINE)
                
                # PMA_PORT (for phpMyAdmin/pgAdmin)
                if re.search(r'PMA_PORT: \d+', content):
                    content = re.sub(r'PMA_PORT: \d+', f'PMA_PORT: {db_port}', content)
                
                # APACHE_PORT (for phpMyAdmin)
                if re.search(r'APACHE_PORT: \d+', content):
                    content = re.sub(r'APACHE_PORT: \d+', f'APACHE_PORT: {apache_port}', content)
                
                with open(compose_file, 'w', encoding='utf-8') as file:
                    file.write(content)
                logger.info(f"Updated {compose_file} with new ports")
                print(f"{GREEN}{FILE_ICON} Updated {compose_file} with new ports {CHECKMARK}{RESET}")
                time.sleep(0.5)
            else:
                logger.error(f"File {compose_file} not found")
                print(f"{RED}{CROSSMARK} File {compose_file} not found{RESET}")
                success = False

        if success:
            logger.info("Port changes applied successfully")
            print(f"{GREEN}{CHECKMARK} Port changes applied successfully!{RESET}")
            if input(f"{YELLOW}Would you like to restart Pasarguard services? (y/n): {RESET}").lower() == 'y':
                restart_services()
        else:
            logger.error("Failed to apply all port changes")
            print(f"{RED}{CROSSMARK} Failed to apply all changes{RESET}")

    except Exception as e:
        logger.error(f"Error during port change: {str(e)}")
        print(f"{RED}{CROSSMARK} Error: {str(e)}{RESET}")
        success = False

    input(f"{CYAN}Press Enter to return to the menu...{RESET}")
    return success

def migrate_marzban_to_pasarguard():
    """Migrate data from Marzban to Pasarguard."""
    logger.info("Starting migration from Marzban to Pasarguard")
    clear_screen()
    print(f"{CYAN}=== Migrate Marzban to Pasarguard ==={RESET}")

    if not check_file_access():
        logger.error("File access issues detected")
        print(f"{RED}{CROSSMARK} File access issues detected. Please fix and try again.{RESET}")
        input(f"{CYAN}Press Enter to return to the menu...{RESET}")
        return False

    marzban_conn = None
    pasarguard_conn = None
    migration_summary = {
        "admins": 0,
        "inbounds": 0,
        "hosts": 0,
        "nodes": 0,
        "users": 0
    }
    
    try:
        print(f"{CYAN}Loading credentials from .env files...{RESET}")
        marzban_config = get_db_config(MARZBAN_ENV_PATH, "Marzban")
        pasarguard_config = get_db_config(PASARGUARD_ENV_PATH, "Pasarguard")

        if (marzban_config['host'] == pasarguard_config['host'] and
            marzban_config['port'] == pasarguard_config['port'] and
            marzban_config['db'] == pasarguard_config['db']):
            logger.error("Marzban and Pasarguard are using the same database")
            print(f"{RED}{CROSSMARK} Marzban and Pasarguard are using the same database!{RESET}")
            input(f"{CYAN}Press Enter to return to the menu...{RESET}")
            return False

        print(f"{CYAN}Testing database connections...{RESET}")
        try:
            conn, _ = connect(marzban_config)
            conn.close()
        except Exception as e:
            logger.error(f"Cannot connect to Marzban ({marzban_config['db_type']}): {str(e)}")
            print(f"{RED}{CROSSMARK} Cannot connect to Marzban ({marzban_config['db_type']}) database: {str(e)}{RESET}")
            input(f"{CYAN}Press Enter to return to the menu...{RESET}")
            return False
        try:
            conn, _ = connect(pasarguard_config)
            conn.close()
        except Exception as e:
            logger.error(f"Cannot connect to Pasarguard ({pasarguard_config['db_type']}): {str(e)}")
            print(f"{RED}{CROSSMARK} Cannot connect to Pasarguard ({pasarguard_config['db_type']}) database: {str(e)}{RESET}")
            input(f"{CYAN}Press Enter to return to the menu...{RESET}")
            return False

        marzban_conn, marzban_db_type = connect(marzban_config)
        pasarguard_conn, pasarguard_db_type = connect(pasarguard_config)

        print(f"{CYAN}============================================================{RESET}")
        print(f"{CYAN}STARTING MIGRATION ({marzban_db_type} ➜ {pasarguard_db_type}){RESET}")
        print(f"{CYAN}============================================================{RESET}")

        migration_summary["admins"] = migrate_admins(marzban_conn, pasarguard_conn, pasarguard_db_type)
        print(f"{GREEN}{CHECKMARK} {migration_summary['admins']} admin(s) migrated{RESET}")

        ensure_default_group(pasarguard_conn, pasarguard_db_type)
        print(f"{GREEN}{CHECKMARK} Default group ensured{RESET}")

        ensure_default_core_config(pasarguard_conn, pasarguard_db_type)
        print(f"{GREEN}{CHECKMARK} Default core config ensured{RESET}")

        xray_config = read_xray_config()
        migration_summary["xray_configs"] = migrate_xray_config(pasarguard_conn, pasarguard_db_type, xray_config)

        migration_summary["inbounds"] = migrate_inbounds_and_associate(marzban_conn, pasarguard_conn, pasarguard_db_type)
        print(f"{GREEN}{CHECKMARK} {migration_summary['inbounds']} inbound(s) migrated and linked{RESET}")

        migration_summary["hosts"] = migrate_hosts(marzban_conn, pasarguard_conn, pasarguard_db_type, safe_alpn)
        print(f"{GREEN}{CHECKMARK} {migration_summary['hosts']} host(s) migrated (ALPN fixed){RESET}")

        migration_summary["nodes"] = migrate_nodes(marzban_conn, pasarguard_conn, pasarguard_db_type)
        print(f"{GREEN}{CHECKMARK} {migration_summary['nodes']} node(s) migrated{RESET}")

        migration_summary["users"] = migrate_users_and_proxies(marzban_conn, pasarguard_conn, pasarguard_db_type)
        print(f"{GREEN}{CHECKMARK} {migration_summary['users']} user(s) migrated with proxy settings{RESET}")

        print(f"\n{CYAN}=== Migration Summary ==={RESET}")
        print(f"{DB_ICON} Admins: {migration_summary['admins']}")
        print(f"{DB_ICON} Inbounds: {migration_summary['inbounds']}")
        print(f"{DB_ICON} Hosts: {migration_summary['hosts']}")
        print(f"{DB_ICON} Nodes: {migration_summary['nodes']}")
        print(f"{DB_ICON} Users: {migration_summary['users']}")
        print(f"{CYAN}=========================={RESET}")

        logger.info("Migration completed successfully")
        print(f"{GREEN}{CHECKMARK} MIGRATION COMPLETED SUCCESSFULLY!{RESET}")
        if input(f"{YELLOW}Would you like to restart Pasarguard services? (y/n): {RESET}").lower() == 'y':
            restart_services()

    except Exception as e:
        logger.error(f"Error during migration: {str(e)}")
        print(f"{RED}{CROSSMARK} Error during migration: {str(e)}{RESET}")
        if pasarguard_conn:
            pasarguard_conn.rollback()
        input(f"{CYAN}Press Enter to return to the menu...{RESET}")
        return False
    finally:
        if marzban_conn:
            marzban_conn.close()
        if pasarguard_conn:
            pasarguard_conn.close()

    input(f"{CYAN}Press Enter to return to the menu...{RESET}")
    return True

# --- Main Function ---
def main():
    """Main function to run the menu-driven program."""
    if sys.version_info < (3, 6):
        logger.error("Python 3.6+ required")
        print(f"{RED}{CROSSMARK} Python 3.6+ required{RESET}")
        sys.exit(1)
    
    if not PSYCOPG2_AVAILABLE:
        logger.warning("'psycopg2-binary' not found. PostgreSQL/TimescaleDB migration will fail")
        print(f"{YELLOW}⚠ 'psycopg2-binary' not found. PostgreSQL/TimescaleDB migration will fail{RESET}")
    
    if not DATEUTIL_AVAILABLE:
        logger.warning("'python-dateutil' not found. Date parsing may fail for non-standard formats")
        print(f"{YELLOW}⚠ 'python-dateutil' not found. Date parsing may fail for non-standard formats{RESET}")

    check_dependencies()

    while True:
        display_menu()
        choice = input(f"{YELLOW}Enter your choice (1-3): {RESET}").strip()

        if choice == "1":
            change_db_port()
        elif choice == "2":
            migrate_marzban_to_pasarguard()
        elif choice == "3":
            logger.info("Exiting program")
            print(f"{CYAN}Exiting... Thank you for using Marz ➜ Pasarguard!{RESET}")
            sys.exit(0)
        else:
            logger.warning(f"Invalid choice: {choice}")
            print(f"{RED}{CROSSMARK} Invalid choice. Please enter 1, 2, or 3.{RESET}")
            input(f"{CYAN}Press Enter to continue...{RESET}")

if __name__ == "__main__":
    main()
