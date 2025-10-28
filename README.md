# 🛡️ Marzban → Pasarguard Migration Tool

🌐 Languages: [English](README.md) | [فارسی](README.fa.md)

This tool is built with Python to automatically transfer data between Marzban and Pasarguard and manage the database port.

---

## 🚀 Features
- ✅ Automatic database port update  
- 🔄 Full migration from Marzban to Pasarguard (users, nodes, hosts, etc.)  
- 🧭 Simple and intuitive menu  
- ⚙️ Automatic prerequisite installation  

---

## 🧭 Migration Guide

1. Install Pasarguard first.  
2. Run the script and in Tab 1, change the database port:

```bash
asis-pg
```

3. Then restart Pasarguard:

```bash
pasarguard restart
```

4. Run the script again and click Tab 2:

```bash
asis-pg
```

5. The migration process will run automatically. Just restart Pasarguard once more:

```bash
pasarguard restart
```

⚙️ Once completed, all data will be successfully migrated from Marzban to Pasarguard.

---

## 🛠️ Installation

To quickly install, run:
```bash
wget https://raw.githubusercontent.com/ASiSSK/go-to-pasarguard/main/asisskpg.sh && bash asisskpg.sh
```
To run the migration panel:

```bash
asis-pg
```
