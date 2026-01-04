/**
 * AI Loop SSE Client
 * Connects to /api/events and updates Store
 * Features: auto-reconnect, replay semantics, event handling
 */

let eventSource = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_DELAY = 30000; // 30s max

/**
 * Connect to SSE endpoint
 */
function connectSSE() {
  const state = Store.getState();
  let url = '/api/events';

  // Add replay parameter if we have a last event ID
  if (state.lastEventId) {
    url += `?since=${encodeURIComponent(state.lastEventId)}`;
  }

  Store.setState({ connected: false });

  eventSource = new EventSource(url);

  eventSource.onopen = () => {
    console.log('[SSE] Connected');
    Store.setState({ connected: true });
    reconnectAttempts = 0;
  };

  eventSource.onerror = (error) => {
    console.error('[SSE] Error:', error);
    Store.setState({ connected: false });

    // EventSource will auto-reconnect, but we track attempts
    reconnectAttempts++;

    if (eventSource.readyState === EventSource.CLOSED) {
      // Connection closed, schedule manual reconnect
      const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), MAX_RECONNECT_DELAY);
      console.log(`[SSE] Reconnecting in ${delay}ms...`);
      setTimeout(connectSSE, delay);
    }
  };

  // Handle init event (initial state dump)
  eventSource.addEventListener('init', (e) => {
    const data = JSON.parse(e.data);
    console.log('[SSE] Init:', data);

    // Set UI mode
    Store.setState({ uiMode: data.mode || 'dry_run' });

    // Load runs into store
    if (data.runs && Array.isArray(data.runs)) {
      const runs = new Map();
      for (const run of data.runs) {
        runs.set(run.run_id, run);
      }
      Store.setState({ runs });
    }

    // Track last event ID for replay
    if (data.lastEventId) {
      Store.setState({ lastEventId: data.lastEventId });
    }
  });

  // Handle heartbeat
  eventSource.addEventListener('heartbeat', () => {
    // Heartbeat received, connection is alive
  });

  // Handle run:created
  eventSource.addEventListener('run:created', (e) => {
    const data = JSON.parse(e.data);
    console.log('[SSE] Run created:', data.run_id);

    Store.updateRun(data.run_id, {
      run_id: data.run_id,
      issue_identifier: data.issue_identifier,
      issue_title: data.issue_title,
      status: 'pending',
      approval_mode: Store.getState().globalApprovalMode,
      iteration: 0,
      confidence: null,
      started_at: new Date().toISOString(),
      completed_at: null,
      gate_pending: null,
    });

    updateLastEventId(e);
  });

  // Handle run:status
  eventSource.addEventListener('run:status', (e) => {
    const data = JSON.parse(e.data);
    console.log('[SSE] Run status:', data.run_id, data.status);

    Store.updateRun(data.run_id, {
      status: data.status,
      iteration: data.iteration ?? Store.getRun(data.run_id)?.iteration,
      confidence: data.confidence ?? Store.getRun(data.run_id)?.confidence,
    });

    updateLastEventId(e);
  });

  // Handle run:output
  eventSource.addEventListener('run:output', (e) => {
    const data = JSON.parse(e.data);
    Store.appendOutput(data.run_id, data.content);
    updateLastEventId(e);
  });

  // Handle run:completed
  eventSource.addEventListener('run:completed', (e) => {
    const data = JSON.parse(e.data);
    console.log('[SSE] Run completed:', data.run_id, data.status);

    Store.updateRun(data.run_id, {
      status: data.status,
      confidence: data.final_confidence,
      completed_at: new Date().toISOString(),
      gate_pending: null,
    });

    updateLastEventId(e);
  });

  // Handle gate:pending
  eventSource.addEventListener('gate:pending', (e) => {
    const data = JSON.parse(e.data);
    console.log('[SSE] Gate pending:', data.run_id, data.gate_type);

    Store.updateRun(data.run_id, {
      gate_pending: {
        gate_type: data.gate_type,
        critique: data.critique,
      },
    });

    // Auto-select this run if none selected
    if (!Store.getState().selectedRunId) {
      Store.setState({ selectedRunId: data.run_id });
    }

    updateLastEventId(e);
  });

  // Handle gate:resolved
  eventSource.addEventListener('gate:resolved', (e) => {
    const data = JSON.parse(e.data);
    console.log('[SSE] Gate resolved:', data.run_id, data.action);

    Store.updateRun(data.run_id, {
      gate_pending: null,
    });

    updateLastEventId(e);
  });

  // Handle run:error (pipeline errors)
  eventSource.addEventListener('run:error', (e) => {
    const data = JSON.parse(e.data);
    console.log('[SSE] Run error:', data.run_id, data.error);

    Store.updateRun(data.run_id, {
      status: 'failed',
      error_message: data.error,
      completed_at: new Date().toISOString(),
    });

    updateLastEventId(e);
  });
}

/**
 * Update last event ID for replay semantics
 */
function updateLastEventId(event) {
  if (event.lastEventId) {
    Store.setState({ lastEventId: event.lastEventId });
  }
}

/**
 * Disconnect from SSE
 */
function disconnectSSE() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  Store.setState({ connected: false });
}

/**
 * Check if connected
 */
function isConnected() {
  return eventSource && eventSource.readyState === EventSource.OPEN;
}

// Export SSE API
window.SSE = {
  connect: connectSSE,
  disconnect: disconnectSSE,
  isConnected,
};
