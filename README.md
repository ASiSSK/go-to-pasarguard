# 🛡️ Marzban to Pasarguard Migration Tool

A Python-based automation tool to migrate data and manage database port configurations between **Marzban** and **Pasarguard**.

---

## 🚀 Features
- ✅ **Change Database Port** — Automatically updates the database port in Pasarguard’s `.env` and `docker-compose.yml` files.  
- 🔄 **Migrate Marzban → Pasarguard** — Transfers:
  - Admins  
  - Inbounds  
  - Hosts  
  - Nodes  
  - Users  
  - `xray_config.json` → Pasarguard’s 
- 🧭 **Interactive Menu** — Simple text UI for choosing actions: change port, migrate, or exit.  
- ⚙️ **Auto Dependency Check** — Installs required dependencies (`screen`, `python3`, `pymysql`, `python-dotenv`).  

---

## 🧩 Requirements
- Python **3.6+**
- Ubuntu/Debian-based system (supports `apt-get`)
- Installed **Marzban** and **Pasarguard** with valid `.env` configurations  
- Root or `sudo` privileges (for dependency installation)

---

## 🛠️ Installation

1. Clone the repository:
   ```bash
   wget https://raw.githubusercontent.com/ASiSSK/go-to-pasarguard/main/asisskpg.sh && bash asisskpg.sh
