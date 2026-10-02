#!/bin/bash
set -e

echo "=== TarotBot Deploy ==="

# Update system
apt-get update && apt-get upgrade -y

# Install Docker
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
fi

# Install Docker Compose plugin
if ! docker compose version &> /dev/null; then
    apt-get install -y docker-compose-plugin
fi

# Create app directory
mkdir -p /opt/tarotbot/data
cd /opt/tarotbot

# Copy files (run from project dir)
# scp -r ./* root@server:/opt/tarotbot/

# Create .env if not exists
if [ ! -f .env ]; then
    echo "ERROR: .env file not found! Copy it from .env.example and fill in keys."
    exit 1
fi

# Build and start
docker compose up -d --build

echo "=== Bot started! Check logs: docker compose logs -f ==="