/**
 * AI Loop Reactive Store
 * Minimal reactive state management (~50 lines core)
 * Philosophy: Single source of truth, keyed DOM updates
 */

// Initial state shape (from plan)
const initialState = {
  // Connection
  connected: false,
  lastEventId: null,

  // Global settings
  globalApprovalMode: 'auto', // 'auto' | 'gate_on_fail' | 'always_gate'
  uiMode: 'dry_run', // 'dry_run' | 'write_enabled'

  // Data (keyed by run_id)
  runs: new Map(),

  // Output buffers (separate for memory efficiency, max 1000 lines per run)
  outputBuffers: new Map(),

  // UI state
  selectedRunId: null,
  activeTab: 'output', // 'output' | 'files' | 'critique'
  leftPanelWidth: parseInt(localStorage.getItem('leftPanelWidth')) || 380,
  mobileActivePanel: 'list', // 'list' | 'detail'
};

// Subscribers: Map<path, Set<callback>>
const subscribers = new Map();

// Current state
let state = { ...initialState };

/**
 * Get current state (read-only snapshot)
 */
function getState() {
  return state;
}

/**
 * Update state with partial updates
 * @param {object} updates - Partial state updates
 */
function setState(updates) {
  const oldState = state;
  state = { ...state, ...updates };

  // Notify subscribers for each changed key
  for (const key of Object.keys(updates)) {
    notifySubscribers(key, state[key], oldState[key]);
  }
}

/**
 * Update a nested path in state
 * @param {string} path - Dot-separated path (e.g., 'runs.abc123.status')
 * @param {any} value - New value
 */
function setPath(path, value) {
  const parts = path.split('.');
  const key = parts[0];

  if (parts.length === 1) {
    setState({ [key]: value });
    return;
  }

  // Handle Map updates for runs and outputBuffers
  if ((key === 'runs' || key === 'outputBuffers') && parts.length >= 2) {
    const map = new Map(state[key]);
    const id = parts[1];

    if (parts.length === 2) {
      // Setting entire run/buffer
      map.set(id, value);
    } else {
      // Setting nested property in run
      const current = map.get(id) || {};
      const nested = { ...current };
      setNestedValue(nested, parts.slice(2), value);
      map.set(id, nested);
    }

    setState({ [key]: map });
    notifySubscribers(path, value, null);
    return;
  }

  // Generic nested update
  const newState = { ...state };
  setNestedValue(newState, parts, value);
  state = newState;
  notifySubscribers(path, value, null);
}

/**
 * Helper to set nested value
 */
function setNestedValue(obj, parts, value) {
  let current = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (!(parts[i] in current)) {
      current[parts[i]] = {};
    }
    current = current[parts[i]];
  }
  current[parts[parts.length - 1]] = value;
}

/**
 * Subscribe to state changes
 * @param {string} path - Path to subscribe to ('*' for all changes)
 * @param {function} callback - Called with (newValue, oldValue, path)
 * @returns {function} Unsubscribe function
 */
function subscribe(path, callback) {
  if (!subscribers.has(path)) {
    subscribers.set(path, new Set());
  }
  subscribers.get(path).add(callback);

  return () => {
    const subs = subscribers.get(path);
    if (subs) {
      subs.delete(callback);
      if (subs.size === 0) {
        subscribers.delete(path);
      }
    }
  };
}

/**
 * Notify subscribers of a change
 */
function notifySubscribers(path, newValue, oldValue) {
  // Notify exact path subscribers
  const exactSubs = subscribers.get(path);
  if (exactSubs) {
    exactSubs.forEach(cb => cb(newValue, oldValue, path));
  }

  // Notify wildcard subscribers
  const wildcardSubs = subscribers.get('*');
  if (wildcardSubs) {
    wildcardSubs.forEach(cb => cb(newValue, oldValue, path));
  }

  // Notify parent path subscribers (e.g., 'runs' when 'runs.abc.status' changes)
  const parts = path.split('.');
  for (let i = 1; i < parts.length; i++) {
    const parentPath = parts.slice(0, i).join('.');
    const parentSubs = subscribers.get(parentPath);
    if (parentSubs) {
      parentSubs.forEach(cb => cb(newValue, oldValue, path));
    }
  }
}

/**
 * Select a value from state
 * @param {function} selector - Function that takes state and returns value
 */
function select(selector) {
  return selector(state);
}

/**
 * Get a run by ID
 */
function getRun(runId) {
  return state.runs.get(runId);
}

/**
 * Get output buffer for a run (returns array, max 1000 lines)
 */
function getOutputBuffer(runId) {
  return state.outputBuffers.get(runId) || [];
}

/**
 * Append output to a run's buffer (ring buffer, max 1000 lines)
 */
function appendOutput(runId, content) {
  const buffer = state.outputBuffers.get(runId) || [];
  const newBuffer = [...buffer, content].slice(-1000); // Keep last 1000 lines

  const newBuffers = new Map(state.outputBuffers);
  newBuffers.set(runId, newBuffer);
  setState({ outputBuffers: newBuffers });

  notifySubscribers(`outputBuffers.${runId}`, newBuffer, buffer);
}

/**
 * Update a run
 */
function updateRun(runId, updates) {
  const current = state.runs.get(runId) || {};
  const updated = { ...current, ...updates };

  const newRuns = new Map(state.runs);
  newRuns.set(runId, updated);
  setState({ runs: newRuns });

  // Notify for each updated field
  for (const key of Object.keys(updates)) {
    notifySubscribers(`runs.${runId}.${key}`, updates[key], current[key]);
  }
  notifySubscribers(`runs.${runId}`, updated, current);
}

/**
 * Delete a run
 */
function deleteRun(runId) {
  const newRuns = new Map(state.runs);
  newRuns.delete(runId);

  const newBuffers = new Map(state.outputBuffers);
  newBuffers.delete(runId);

  setState({ runs: newRuns, outputBuffers: newBuffers });
}

/**
 * Persist UI state to localStorage
 */
function persistUIState() {
  localStorage.setItem('leftPanelWidth', state.leftPanelWidth);
  localStorage.setItem('globalApprovalMode', state.globalApprovalMode);
}

/**
 * Load persisted UI state
 */
function loadPersistedState() {
  const savedMode = localStorage.getItem('globalApprovalMode');
  if (savedMode) {
    setState({ globalApprovalMode: savedMode });
  }
}

// Export store API
window.Store = {
  getState,
  setState,
  setPath,
  subscribe,
  select,
  getRun,
  getOutputBuffer,
  appendOutput,
  updateRun,
  deleteRun,
  persistUIState,
  loadPersistedState,
};
