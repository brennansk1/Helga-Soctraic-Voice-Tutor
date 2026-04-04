#!/bin/bash

# Setup 16GB NVMe Swap file for Jetson Orin Nano (8GB RAM)
# Run as root: sudo ./scripts/setup_swap.sh

set -e

SWAP_FILE="/swapfile"
SWAP_SIZE="16G"

echo "Creating $SWAP_SIZE swap file at $SWAP_FILE..."

# Create swap file
fallocate -l $SWAP_SIZE $SWAP_FILE

# Set permissions
chmod 600 $SWAP_FILE

# Format as swap
mkswap $SWAP_FILE

# Enable swap
swapon $SWAP_FILE

# Make permanent
if ! grep -q "$SWAP_FILE" /etc/fstab; then
    echo "$SWAP_FILE none swap sw 0 0" >> /etc/fstab
fi

echo "Swap setup complete. Current swap:"
swapon --show