/**
 * AI Loop SSE Client
 * Connects to /api/events and updates Store
 * Features: auto-reconnect, replay semantics, event handling
 */

let eventSource = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_DELAY = 30000; // 30s max

// Track per-run last event IDs for fine-grained resume
let lastEventIds = {};

/**
 * Connect to SSE endpoint
 */
function connectSSE() {
  const state = Store.getState();
  let url = '/api/events';

  // Build combined last event ID from tracked per-run IDs
  const combinedIds = Object.values(lastEventIds).filter(Boolean).join(',');
  if (combinedIds) {
    url += `?since=${encodeURIComponent(combinedIds)}`;
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

    // Track per-run last event IDs for fine-grained resume
    if (data.lastEventIds) {
      lastEventIds = { ...lastEventIds, ...data.lastEventIds };
    }

    // Track combined last event ID for legacy support
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

    const state = Store.getState();
    const issueId = data.issue_identifier;

    // Check for existing stub (temp_id = issue_identifier)
    const stub = state.runs.get(issueId);
    if (stub && stub.is_stub) {
      console.log('[SSE] Reconciling stub:', issueId, '→', data.run_id);

      // Preserve UI state from stub
      const preservedState = { approval_mode: stub.approval_mode };
      const wasSelected = state.selectedRunId === issueId;

      // Remove stub, add real run
      Store.deleteRun(issueId);
      Store.updateRun(data.run_id, {
        run_id: data.run_id,
        issue_identifier: data.issue_identifier,
        issue_title: data.issue_title,
        status: data.status || 'pending',
        ...preservedState,
        is_stub: false,
        iteration: 0,
        confidence: null,
        started_at: new Date().toISOString(),
        completed_at: null,
        gate_pending: null,
      });

      // Restore selection if stub was selected
      if (wasSelected) {
        Store.setState({ selectedRunId: data.run_id });
      }
    } else {
      // No stub, just add the run
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
    }

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

  // Handle timeline event (canonical v2 events)
  eventSource.addEventListener('timeline', (e) => {
    const event = JSON.parse(e.data);
    const runId = event.run_id;

    // run.output events UPSERT into phase output card (not append)
    if (event.kind === 'run.output') {
      const stepSection = {
        ts: event.ts,
        step: event.payload.step,
        text: event.payload.text,
        duration_s: event.payload.duration_s,
        char_count: event.payload.char_count
      };
      Store.upsertPhaseOutput(runId, event.phase, stepSection);

      // Notify Timeline component if it's watching this run
      if (window.Timeline && runId === window.Timeline.currentRunId) {
        window.Timeline.upsertPhaseOutputCard(runId, event.phase);
      }
    } else {
      // All other events append normally
      Store.appendTimelineEntry(runId, event);

      // Notify Timeline component
      if (window.Timeline && runId === window.Timeline.currentRunId) {
        window.Timeline.appendEntryNode(event);
      }
    }

    // Handle run:created - also update runs Map for run list
    if (event.kind === 'run.created') {
      const issueId = event.payload?.issue_identifier;
      const state = Store.getState();

      // Check for existing stub
      const stub = state.runs.get(issueId);
      if (stub && stub.is_stub) {
        console.log('[SSE] Reconciling stub:', issueId, '→', runId);
        const preservedState = { approval_mode: stub.approval_mode };
        const wasSelected = state.selectedRunId === issueId;

        Store.deleteRun(issueId);
        Store.updateRun(runId, {
          run_id: runId,
          issue_identifier: issueId,
          status: 'pending',
          ...preservedState,
          is_stub: false,
          iteration: 0,
          confidence: null,
          started_at: new Date().toISOString(),
          completed_at: null,
          gate_pending: null,
        });

        if (wasSelected) {
          Store.setState({ selectedRunId: runId });
        }
      } else if (!state.runs.has(runId)) {
        Store.updateRun(runId, {
          run_id: runId,
          issue_identifier: issueId,
          status: 'pending',
          approval_mode: Store.getState().globalApprovalMode,
          iteration: 0,
          confidence: null,
          started_at: new Date().toISOString(),
          completed_at: null,
          gate_pending: null,
        });
      }
    }

    // Handle run.milestone for completion
    if (event.kind === 'run.milestone' && event.payload?.milestone_name?.startsWith('run_')) {
      const status = event.payload.milestone_name.replace('run_', '');
      Store.updateRun(runId, {
        status: status,
        completed_at: new Date().toISOString(),
        gate_pending: null,
      });
    }

    // Handle run.gate for pending gates
    if (event.kind === 'run.gate' && event.payload?.pending) {
      Store.updateRun(runId, {
        gate_pending: {
          gate_type: event.payload.gate_type,
          critique: event.payload.critique,
        },
      });

      // Auto-select this run if none selected
      if (!Store.getState().selectedRunId) {
        Store.setState({ selectedRunId: runId });
      }
    }

    // Update gate results (confidence, blockers)
    if (event.kind === 'run.gate' && event.payload?.confidence !== undefined) {
      Store.updateRun(runId, {
        confidence: event.payload.confidence,
      });
    }

    updateLastEventId(e);
  });
}

/**
 * Update last event ID for replay semantics
 * @param {Event} event - SSE event with lastEventId
 */
function updateLastEventId(event) {
  if (event.lastEventId) {
    // Parse run_id:line format to update per-run tracking
    const parts = event.lastEventId.split(':');
    if (parts.length >= 2) {
      const runId = parts.slice(0, -1).join(':');  // Handle run IDs with colons
      lastEventIds[runId] = event.lastEventId;
    }

    // Also update store for legacy support
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
