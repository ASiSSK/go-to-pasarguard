#!/usr/bin/env python3
"""
Migration Utilities for Marzban to Pasarguard
Contains helper functions for database migration.
"""

import pymysql
import json
import datetime
import getpass
from typing import Dict, Any, Optional
from dotenv import load_dotenv
import os

# Helper: Smart input with defaults
def ask(prompt: str, default: str = "") -> str:
    if default:
        response = input(f"{prompt} [{default}]: ").strip()
        return response if response else default
    else:
        while True:
            response = input(f"{prompt}: ").strip()
            if response:
                return response
            print("  This field cannot be empty.")

# Function to get database config from .env file
def get_db_config(env_path: str) -> Dict[str, Any]:
    """Load database configuration from .env file."""
    load_dotenv(env_path)
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = int(os.getenv("DB_PORT", "3306"))
    user = os.getenv("DB_USER", ask("Database username"))
    password = os.getenv("DB_PASSWORD", getpass.getpass("Database password: "))
    db_name = os.getenv("DB_NAME", ask("Database name"))
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": db_name,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }

# Smart cleaners
def safe_alpn(value: Optional[str]) -> Optional[str]:
    """Convert 'none', '', 'null' → NULL for Pasarguard."""
    if not value or str(value).strip().lower() in ["none", "null", ""]:
        return None
    return str(value).strip()

def safe_json(value: Any) -> Optional[str]:
    """Safely convert to JSON or return NULL."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            json.loads(value)  # validate
        return json.dumps(value) if not isinstance(value, str) else value
    except:
        return None

# Connection
def connect(cfg: Dict[str, Any]):
    """Connect to the database."""
    try:
        conn = pymysql.connect(**cfg)
        print(f"Connected to {cfg['database']}@{cfg['host']}:{cfg['port']}")
        return conn
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

# Migration Functions
def migrate_admins(marzban_conn, pasarguard_conn):
    """Migrate admins from Marzban to Pasarguard."""
    print("Migrating admins...")
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

def ensure_default_group(pasarguard_conn):
    """Ensure default group exists in Pasarguard."""
    with pasarguard_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM groups")
        if cur.fetchone()["cnt"] == 0:
            cur.execute("INSERT INTO groups (id, name, is_disabled) VALUES (1,'DefaultGroup',0)")
            print("Default group created.")
        else:
            print("Default group already exists.")
    pasarguard_conn.commit()

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
            print("Default core config created.")
        else:
            print("Default core config already exists.")
    pasarguard_conn.commit()

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

def migrate_hosts(marzban_conn, pasarguard_conn):
    """Migrate hosts with smart ALPN fix."""
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
    return len(hosts)

def migrate_nodes(marzban_conn, pasarguard_conn):
    """Migrate nodes from Marzban to Pasarguard."""
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
