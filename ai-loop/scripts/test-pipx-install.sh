#!/bin/bash
# Smoke test: Verify pipx install includes static assets
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "Building wheel..."
pip wheel . -w dist/ --no-deps --quiet

echo "Installing with pipx..."
pipx install --force dist/*.whl

# Start server in background on unused port
PORT=9876
echo "Starting server on port $PORT..."
ai-loop serve --port $PORT --project "$PROJECT_DIR" &
SERVER_PID=$!

# Wait for server to start
sleep 2

# Check static assets are served
echo "Checking static assets..."

check_asset() {
    local path=$1
    if curl -sf "http://127.0.0.1:$PORT$path" > /dev/null; then
        echo "  ✓ $path"
    else
        echo "  ✗ $path MISSING"
        kill $SERVER_PID 2>/dev/null || true
        exit 1
    fi
}

check_asset "/v2/css/tokens.css"
check_asset "/v2/css/components.css"
check_asset "/v2/js/app.js"
check_asset "/v2/js/store.js"

# Check API endpoints
echo "Checking API endpoints..."
check_asset "/api/projects/current"

# Cleanup
echo "Cleaning up..."
kill $SERVER_PID 2>/dev/null || true

echo ""
echo "✓ Smoke test passed!"
echo "  - Static assets served correctly"
echo "  - API endpoints responding"
