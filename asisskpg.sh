#!/bin/bash
echo "Installing Marzban to Pasarguard Migration Tool..."

# Check for root permissions
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (use sudo)"
    exit 1
fi

# Define repository URL
REPO_URL="https://raw.githubusercontent.com/ASiSSK/go-to-pasarguard/main"

# Download required files
echo "Downloading required files..."
wget -q "$REPO_URL/marz-go-pasarguard.py" -O marz-go-pasarguard.py
wget -q "$REPO_URL/migration_utils.py" -O migration_utils.py
wget -q "$REPO_URL/requirements.txt" -O requirements.txt

# Check if downloads were successful
for file in marz-go-pasarguard.py migration_utils.py requirements.txt; do
    if [ ! -f "$file" ]; then
        echo "Error: Failed to download $file"
        exit 1
    fi
done

# Install dependencies
echo "Installing dependencies..."
apt-get update
apt-get install -y screen python3 python3-pip
pip3 install -r requirements.txt

# Move files to /usr/local/bin
echo "Setting up asis-pg command..."
mv marz-go-pasarguard.py /usr/local/bin/asis-pg
mv migration_utils.py /usr/local/bin/migration_utils.py
chmod +x /usr/local/bin/asis-pg

# Clean up
rm -f requirements.txt

echo "Installation completed! Run the tool with: asis-pg"
