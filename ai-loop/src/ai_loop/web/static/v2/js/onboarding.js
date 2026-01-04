/**
 * AI Loop Onboarding & Dev Mode Support
 *
 * Handles:
 * - Pairing token from URL (stored in sessionStorage)
 * - CSRF token refresh via /api/session (single source of truth)
 * - Reconnect overlay when backend restarts
 * - Automatic 403 retry with refreshed tokens
 *
 * CSRF Bootstrap: Single source of truth is /api/session (not meta tags).
 * On load, we call /api/session to get the CSRF token. This ensures the
 * token is always fresh and avoids stale meta-tag issues after restarts.
 *
 * Jobs-Level Guarantees:
 * - No lying states: `.ready` iff `isReady === true`
 * - No deadlocks: `ready()` starts init if needed
 * - No storms: Idempotent init reuses in-flight promise
 * - Failure visible: Error events fired, state exposed
 * - Failure recoverable: `retry()` re-runs init
 */

// Part A: Lazy fallback - initialize from DOM if available, fallback in getter
let csrfToken = document.getElementById('csrf-token')?.value || null;

// Part B: Truthful + idempotent readiness model
let readyPromise = null;
let initInFlight = false;
let isReady = false;
let initError = null;

// Reconnect overlay element
let reconnectOverlay = null;

// Whether we're in dev mode (detected by presence of token in URL)
let isDevMode = false;

/**
 * Get the current CSRF token (lazy fallback from DOM)
 */
function getCsrfToken() {
  if (!csrfToken) {
    csrfToken = document.getElementById('csrf-token')?.value || '';
  }
  return csrfToken;
}

/**
 * Get the pairing token from sessionStorage
 */
function getPairingToken() {
  return sessionStorage.getItem('pairing_token') || '';
}

/**
 * Refresh session tokens from /api/session
 * Single source of truth for CSRF - sets csrfToken from response.
 * Returns true iff we got a valid token.
 */
async function refreshSession() {
  try {
    const res = await fetch('/api/session');
    if (res.ok) {
      const data = await res.json();
      csrfToken = data.csrf; // Single source of truth: session response
      // Also update the legacy CSRF input if it exists
      const csrfInput = document.getElementById('csrf-token');
      if (csrfInput) {
        csrfInput.value = csrfToken;
      }
      // Update API.csrfToken if API is available
      if (window.API) {
        window.API.csrfToken = csrfToken;
      }
      return true;
    }
  } catch (e) {
    // Server not ready yet
  }
  return false;
}

/**
 * Perform a secure POST with automatic 403 handling
 * If 403, tries to refresh session and retry once
 */
async function securePost(url, data) {
  const doFetch = () =>
    fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-Pairing-Token': getPairingToken(),
        'X-CSRF-Token': getCsrfToken(),
      },
      body: JSON.stringify(data),
    });

  const res = await doFetch();

  // If 403, try to refresh session and retry once
  if (res.status === 403) {
    const refreshed = await refreshSession();
    if (refreshed) {
      return doFetch();
    }
  }

  return res;
}

/**
 * Show the reconnecting overlay
 */
function showReconnectOverlay() {
  if (reconnectOverlay) return;

  reconnectOverlay = document.createElement('div');
  reconnectOverlay.className = 'reconnect-overlay';
  reconnectOverlay.innerHTML = `
    <div class="reconnect-content">
      <span class="spinner"></span>
      <span>Reconnecting...</span>
    </div>
  `;
  document.body.appendChild(reconnectOverlay);
}

/**
 * Hide the reconnecting overlay
 */
function hideReconnectOverlay() {
  if (reconnectOverlay) {
    reconnectOverlay.remove();
    reconnectOverlay = null;
  }
}

/**
 * Wait for backend to be ready, then fetch session
 */
async function waitForBackend() {
  const statusEl = document.getElementById('backend-status');

  while (true) {
    try {
      const res = await fetch('/api/status');
      const data = await res.json();
      if (data.ready) {
        // Server is back - get CSRF token from /api/session
        await refreshSession();
        hideReconnectOverlay();
        if (statusEl) {
          statusEl.classList.add('hidden');
        }
        return;
      }
    } catch (e) {
      // Server not ready or restarting
    }

    if (statusEl) {
      statusEl.textContent = 'Starting...';
    }
    await new Promise((r) => setTimeout(r, 200));
  }
}

/**
 * Start connection monitoring
 */
function startConnectionMonitor() {
  // Poll to detect dev restarts
  setInterval(async () => {
    try {
      const res = await fetch('/api/status');
      if (!res.ok) {
        showReconnectOverlay();
        waitForBackend();
      }
    } catch (e) {
      showReconnectOverlay();
      waitForBackend();
    }
  }, 2000);
}

/**
 * Initialize onboarding (idempotent - reuses in-flight promise)
 *
 * Guarantees:
 * - If init is running, returns same promise (no storms)
 * - Sets isReady=true iff we got a valid CSRF token
 * - Fires onboarding:ready or onboarding:failed events
 */
async function initOnboarding() {
  // Idempotent: if init is already running, reuse that promise
  if (initInFlight && readyPromise) {
    return readyPromise;
  }

  initInFlight = true;

  readyPromise = (async () => {
    try {
      // Store pairing token from URL (if present) for use in POST requests
      const params = new URLSearchParams(window.location.search);
      const token = params.get('token');
      if (token) {
        sessionStorage.setItem('pairing_token', token);
        isDevMode = true;
        // Clean URL (remove token from address bar for security)
        window.history.replaceState({}, '', window.location.pathname);
      }

      // Check if we already have a pairing token (from previous session)
      if (sessionStorage.getItem('pairing_token')) {
        isDevMode = true;
      }

      // Wait for backend and get initial session
      await waitForBackend();
      const sessionOk = await refreshSession();

      // Validate: "ready" means we got a real token from session
      if (!sessionOk || !csrfToken) {
        throw new Error('CSRF token not available');
      }

      // Success
      isReady = true;
      initError = null;
      document.body.classList.add('ready');
      document.body.classList.remove('init-failed');
      window.dispatchEvent(new Event('onboarding:ready'));

      // In dev mode, start connection monitor for auto-reconnect
      if (isDevMode) {
        startConnectionMonitor();
      }
    } catch (err) {
      console.error('[Onboarding] Init failed:', err);
      isReady = false;
      initError = err;
      document.body.classList.remove('ready');
      document.body.classList.add('init-failed');
      window.dispatchEvent(new CustomEvent('onboarding:failed', { detail: err }));
    } finally {
      initInFlight = false;
    }
  })();

  return readyPromise;
}

/**
 * Get readiness promise - starts init if needed (never hangs)
 */
function ready() {
  if (!readyPromise) {
    initOnboarding(); // Ensure init starts
  }
  return readyPromise;
}

/**
 * Retry initialization (for recovery after failure)
 * Resets state and re-runs init.
 */
async function retry() {
  // Reset state to allow fresh attempt (only if not currently in flight)
  if (!initInFlight) {
    readyPromise = null;
  }
  return initOnboarding();
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initOnboarding);
} else {
  initOnboarding();
}

// Export functions for use by other modules
window.Onboarding = {
  getCsrfToken,
  getPairingToken,
  refreshSession,
  securePost,
  showReconnectOverlay,
  hideReconnectOverlay,
  waitForBackend,
  ready,
  isReady: () => isReady,
  getError: () => initError,
  retry,
};
