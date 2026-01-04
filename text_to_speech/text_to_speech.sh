#!/bin/bash
# 设置系统环境变量
export LANG=en_US.UTF-8

ZIP_FILE="/home/root/tts_packages.zip"
OLD_PACKAGES="/home/root/packages"
PACKAGES_DIR="/home/root/packages"

# Check for tts_packages.zip and extract if present
if [ -f "$ZIP_FILE" ]; then
  # Remove old packages directory/file if exists
  if [ -e "$OLD_PACKAGES" ]; then
    echo "Removing old packages at $OLD_PACKAGES"
    rm -rf "$OLD_PACKAGES"
  fi
  echo "Found $ZIP_FILE, extracting..."
  
  # Extract zip file to /home/root/ (force overwrite to avoid prompts)
  if unzip -oq "$ZIP_FILE" -d "/home/root/"; then
    echo "Successfully extracted $ZIP_FILE to /home/root/"
    
    # Install offline packages if packages directory exists
    if [ -d "$PACKAGES_DIR" ]; then
      echo "Installing offline packages from $PACKAGES_DIR..."
      pip3 install "$PACKAGES_DIR"/* || true
    else
      echo "Warning: packages directory not found after extraction: $PACKAGES_DIR" >&2
    fi
  else
    echo "Failed to extract $ZIP_FILE" >&2
  fi
  echo "Removing $PACKAGES_DIR and $ZIP_FILE"
  rm -rf "$PACKAGES_DIR"
  rm -f "$ZIP_FILE"
fi

cd "$(dirname "$0")"
pwd

python3 -m tts.speak_server
echo "text_to_speech start"
