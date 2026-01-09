#!/bin/bash
# Conductor workspace setup script
# This script runs automatically when a new workspace is created

# Copy .env file from repository root to the ai-loop subdirectory
if [ -f "$CONDUCTOR_ROOT_PATH/.env" ]; then
    cp "$CONDUCTOR_ROOT_PATH/.env" ai-loop/.env
    echo "Copied .env to ai-loop/"
else
    echo "Warning: No .env file found at repository root"
fi

# Install Python dependencies if uv is available
if command -v uv &> /dev/null && [ -f "ai-loop/pyproject.toml" ]; then
    cd ai-loop
    uv sync
    cd ..
    echo "Installed Python dependencies"
fi
