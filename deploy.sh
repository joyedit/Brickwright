#!/bin/bash

# Brickwright - Build & Deploy Script
# Builds the mod and deploys it as a zip to the VS Mods folder

set -e

# Paths
PROJECT_DIR=~/Dev/Brickwright
GAME_PATH=~/Apps/vintagestory
MODS_PATH=~/.config/VintagestoryData/Mods
LOG_DIR=~/.config/VintagestoryData/Logs
MOD_NAME="Brickwright"
ZIP_NAME="${MOD_NAME}.zip"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== Brickwright Build & Deploy ===${NC}"

# Verify paths exist
if [ ! -d "$GAME_PATH" ]; then
    echo -e "${RED}Error: Game path not found at $GAME_PATH${NC}"
    exit 1
fi

if [ ! -d "$MODS_PATH" ]; then
    echo -e "${YELLOW}Creating mods directory at $MODS_PATH${NC}"
    mkdir -p "$MODS_PATH"
fi

# Navigate to project directory
cd "$PROJECT_DIR"

# Build the project
echo -e "${YELLOW}Building...${NC}"
export VINTAGE_STORY="$GAME_PATH"
dotnet build -c Release

if [ $? -ne 0 ]; then
    echo -e "${RED}Build failed!${NC}"
    exit 1
fi

echo -e "${GREEN}Build succeeded.${NC}"

# Determine output path
BUILD_OUTPUT="$PROJECT_DIR/bin/Release"
DLL_PATH="$BUILD_OUTPUT/${MOD_NAME}.dll"

if [ ! -f "$DLL_PATH" ]; then
    # Try net8.0 subfolder
    DLL_PATH="$BUILD_OUTPUT/net8.0/${MOD_NAME}.dll"
fi

if [ ! -f "$DLL_PATH" ]; then
    echo -e "${RED}Error: Could not find ${MOD_NAME}.dll in build output${NC}"
    echo "Searched: $BUILD_OUTPUT"
    ls -R "$BUILD_OUTPUT"
    exit 1
fi

# Create a temp staging directory
STAGING=$(mktemp -d)
trap "rm -rf $STAGING" EXIT

# Copy mod contents to staging
cp "$DLL_PATH" "$STAGING/"
cp "$PROJECT_DIR/modinfo.json" "$STAGING/"
cp "$PROJECT_DIR/modicon.png" "$STAGING/" 2>/dev/null || true
cp -r "$PROJECT_DIR/assets" "$STAGING/"

# Remove old zip from mods folder if it exists
if [ -f "$MODS_PATH/$ZIP_NAME" ]; then
    echo -e "${YELLOW}Removing old $ZIP_NAME from Mods folder...${NC}"
    rm "$MODS_PATH/$ZIP_NAME"
fi

# Create zip
echo -e "${YELLOW}Packaging into $ZIP_NAME...${NC}"
cd "$STAGING"
zip -r "$MODS_PATH/$ZIP_NAME" . > /dev/null

echo -e "${GREEN}Deployed to: $MODS_PATH/$ZIP_NAME${NC}"

# Show contents
echo -e "${YELLOW}Zip contents:${NC}"
unzip -l "$MODS_PATH/$ZIP_NAME"

# Tail recent log for quick feedback (optional, if game has been run before)
LATEST_LOG=$(ls -t "$LOG_DIR"/*.log 2>/dev/null | head -1)
if [ -n "$LATEST_LOG" ]; then
    echo ""
    echo -e "${YELLOW}Last 5 Brickwright lines from most recent log:${NC}"
    grep -i "brickwright" "$LATEST_LOG" 2>/dev/null | tail -5 || echo "(no Brickwright entries found)"
fi

echo ""
echo -e "${GREEN}Done! Start/restart Vintage Story to load the mod.${NC}"
