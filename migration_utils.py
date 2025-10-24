import os
import re
import pymysql
import json
import datetime
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# ANSI color codes
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
GREEN = "\033[32m"
RESET = "\033[0m"

def load_env_file(env_path: str) -> Dict[str, str]:
    """Load environment variables from a .env file."""
    if not os.path.exists(env_path):
        print(f"{RED}ERROR: {env_path} not found. Please create it with required variables.{RESET}")
        sys.exit(1)
    load_dotenv(env_path)
    return dict(os.environ)

def parse_sqlalchemy_url(url: str) -> tuple[str, str]:
    """Extract host and port from SQLALCHEMY_DATABASE_URL."""
    try:
        match = re.match(r"mysql\+[^:]+://[^@]+@([^:/]+)(?::(\d+))?/[^/]+", url)
        if match:
            host = match.group(1)
            port = match.group(2) or "3306"
            return host, port
        return "127.0.0.1", "3306"
    except:
        return "127.0.0.1", "3306"

def get_db_config(name: str, env: Dict[str, str], env_path: str) -> Dict[str, Any]:
    """Get database configuration from .env file."""
    print(f"\n=== {name.upper()} DATABASE SETTINGS ===")
    if name.lower() == "marzban":
        keys = {
            "host": "MYSQL_HOST",
            "port": "MYSQL_PORT",
            "user": "MYSQL_USER",
            "password": "MYSQL_PASSWORD",
            "database": "MYSQL_DATABASE"
        }
        defaults = {
            "host": "127.0.0.1",
            "port": "3306",
            "database": "marzban"
        }
    else:  # Pasarguard
        keys = {
            "host": "DB_HOST",
            "port": "DB_PORT",
            "user": "DB_USER",
            "password": "DB_PASSWORD",
            "database": "DB_NAME"
        }
        defaults = {
            "host": "127.0.0.1",
            "port": "3307",
            "database": "pasarguard"
        }

    config = {}
    for key, env_key in keys.items():
        value = env.get(env_key, defaults.get(key, ""))
        if not value and key in ["user", "password", "database"]:
            print(f"{RED}ERROR: Missing {env_key} in {env_path}.{RESET}")
            sys.exit(1)
        config[key] = value

    if name.lower() == "marzban" and (not config["host"] or not config["port"]):
        sqlalchemy_url = env.get("SQLALCHEMY_DATABASE_URL", "")
        if sqlalchemy_url:
            config["host"], config["port"] = parse_sqlalchemy_url(sqlalchemy_url)

    try:
        config["port"] = int(config["port"])
    except ValueError:
        print(f"{RED}ERROR: Invalid {keys['port']} ({config['port']}) in {env_path}.{RESET}")
        sys.exit(1)

    config["charset"] = "utf8mb4"
    config["cursorclass"] = pymysql.cursors.DictCursor

    print(f"Using: host={config['host']}, port={config['port']}, user={config['user']}, db={config['database']}")
    return config

def safe_alpn(value: Optional[str]) -> Optional[str]:
    """Convert 'none', '', 'null' to NULL for Pasarguard."""
    if not value or str(value).strip().lower() in ["none", "null", ""]:
        return None
    return str(value).strip()

def safe_json(value: Any) -> Optional[str]:
    """Safely convert to JSON or return NULL."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            json.loads(value)
        return json.dumps(value) if not isinstance(value, str) else value
    except:
        return None

def read_xray_config(path: str = "/var/lib/marzban/xray_config.json") -> Optional[Dict]:
    """Read xray_config.json file."""
    if not os.path.exists(path):
        print(f"{YELLOW}Warning: File {path} not found. Skipping core_configs update.{RESET}")
        return None
    try:
        with open(path, "r") as f:
            config = json.load(f)
        print(f"{GREEN}Successfully read {path} ✓{RESET}")
        return config
    except Exception as e:
        print(f"{RED}Error reading {path}: {e}{RESET}")
        return None

def connect(cfg: Dict[str, Any]):
    """Connect to the database."""
    try:
        conn = pymysql.connect(**cfg)
        print(f"{GREEN}Connected to {cfg['database']}@{cfg['host']}:{cfg['port']} ✓{RESET}")
        return conn
    except Exception as e:
        print(f"{RED}Connection failed: {e}{RESET}")
        sys.exit(1)

def migrate_admins(marzban_conn, pasarguard_conn):
    """Migrate admins from Marzban to Pasarguard."""
    print("\nMigrating admins...")
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
    print(f"{GREEN}{len(admins)} admin(s) migrated ✓{RESET}")

def ensure_default_group(pasarguard_conn):
    """Ensure a default group exists in Pasarguard."""
    print("\nCreating default group if not exists...")
    with pasarguard_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM groups")
        if cur.fetchone()["cnt"] == 0:
            cur.execute("INSERT INTO groups (id, name, is_disabled) VALUES (1,'DefaultGroup',0)")
            print(f"{GREEN}Default group created ✓{RESET}")
        else:
            print(f"{GREEN}Default group already exists ✓{RESET}")
    pasarguard_conn.commit()

def migrate_xray_config(pasarguard_conn):
    """Migrate xray_config.json to Pasarguard core_configs."""
    print("\nMigrating xray_config.json to core_configs...")
    xray_config = read_xray_config()
    if not xray_config:
        print(f"{YELLOW}Skipping core_configs update (no valid config found).{RESET}")
        return

    with pasarguard_conn.cursor() as cur:
        cur.execute("SELECT * FROM core_configs WHERE id = 1")
        existing = cur.fetchone()
        if existing:
            print("Backing up existing core_config...")
            # Find a free ID for backup
            cur.execute("SELECT MAX(id) AS max_id FROM core_configs")
            max_id = cur.fetchone()["max_id"] or 1000
            backup_id = max_id + 1
            try:
                cur.execute(
                    """
                    INSERT INTO core_configs
                    (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
                    VALUES (%s, NOW(), %s, %s, %s, %s)
                    """,
                    (
                        backup_id,
                        "Backup_Default_Core_Config",
                        existing["config"],
                        existing["exclude_inbound_tags"],
                        existing["fallbacks_inbound_tags"],
                    ),
                )
                print(f"{GREEN}Backup created as 'Backup_Default_Core_Config' with ID {backup_id} ✓{RESET}")
            except Exception as e:
                print(f"{YELLOW}Warning: Failed to create backup: {e}. Continuing...{RESET}")

        cur.execute(
            """
            INSERT INTO core_configs
            (id, created_at, name, config, exclude_inbound_tags, fallbacks_inbound_tags)
            VALUES (1, NOW(), %s, %s, '', '')
            ON DUPLICATE KEY UPDATE
                name = %s, config = %s, created_at = NOW()
            """,
            ("ASiS SK", json.dumps(xray_config), "ASiS SK", json.dumps(xray_config)),
        )
        print(f"{GREEN}xray_config.json migrated as 'ASiS SK' in core_configs ✓{RESET}")

    pasarguard_conn.commit()

def migrate_inbounds_and_associate(marzban_conn, pasarguard_conn):
    """Migrate inbounds and associate them with the default group."""
    print("\nMigrating inbounds...")
    with marzban_conn.cursor() as cur:
        cur.execute("SELECT * FROM inbounds")
        inbounds = cur.fetchall()

    with pasarguard_conn.cursor() as cur:
        for i in inbounds:
            cur.execute("INSERT IGNORE INTO inbounds (id, tag) VALUES (%s,%s)", (i["id"], i["tag"]))
        for i in inbounds:
            cur.execute("INSERT IGNORE INTO inbounds_groups_association (inbound_id, group_id) VALUES (%s,1)", (i["id"],))
    pasarguard_conn.commit()
    print(f"{GREEN}{len(inbounds)} inbound(s) migrated and linked ✓{RESET}")

def migrate_hosts(marzban_conn, pasarguard_conn):
    """Migrate hosts with ALPN fix."""
    print("\nMigrating hosts (with smart ALPN fix)...")
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
                    h["sni"], h["host"], h["security"], safe_alpn(h.get("alpn")),
                    h["fingerprint"], h["allowinsecure"], h["is_disabled"], h.get("path"),
                    h.get("random_user_agent", 0), h.get("use_sni_as_host", 0), h.get("priority", 0),
                    safe_json(h.get("http_headers")), safe_json(h.get("transport_settings")),
                    safe_json(h.get("mux_settings")), safe_json(h.get("noise_settings")),
                    safe_json(h.get("fragment_settings")), h.get("status")
                ),
            )
    pasarguard_conn.commit()
    print(f"{GREEN}{len(hosts)} host(s) migrated (ALPN fixed) ✓{RESET}")

def migrate_nodes(marzban_conn, pasarguard_conn):
    """Migrate nodes from Marzban to Pasarguard."""
    print("\nMigrating nodes...")
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
    print(f"{GREEN}{len(nodes)} node(s) migrated ✓{RESET}")

def migrate_users_and_proxies(marzban_conn, pasarguard_conn):
    """Migrate users and their proxy settings."""
    print("\nMigrating users and proxy settings...")
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
    print(f"{GREEN}{total} user(s) migrated with proxy settings ✓{RESET}")