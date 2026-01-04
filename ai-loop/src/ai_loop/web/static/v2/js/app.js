/**
 * AI Loop Dashboard App
 * Main entry point - connects store, events, and components
 */

// API client for server communication
const API = {
  csrfToken: null,

  init() {
    this.csrfToken = document.getElementById('csrf-token')?.value || '';
  },

  async submitFeedback(runId, action, feedback = '') {
    try {
      const response = await fetch(`/api/runs/${runId}/feedback`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': this.csrfToken,
        },
        body: JSON.stringify({ action, feedback }),
      });
      return await response.json();
    } catch (error) {
      console.error('[API] Feedback error:', error);
      return { error: error.message };
    }
  },

  async updateRunConfig(runId, config) {
    try {
      const response = await fetch(`/api/runs/${runId}/config`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': this.csrfToken,
        },
        body: JSON.stringify(config),
      });
      return await response.json();
    } catch (error) {
      console.error('[API] Config error:', error);
      return { error: error.message };
    }
  },
};

/**
 * Initialize the application
 */
function initApp() {
  console.log('[App] Initializing AI Loop Dashboard v2');

  // Initialize API client
  API.init();

  // Load persisted UI state
  Store.loadPersistedState();

  // Set up UI components
  Components.setupTabs();
  Components.setupMobileTabs();
  Components.setupResizer();
  Components.setupFeedbackBar();
  Components.setupGlobalApproval();
  Components.setupIssuePicker();

  // Subscribe to store changes
  setupSubscriptions();

  // Connect to SSE
  SSE.connect();

  // Initial render
  Components.renderRunList();

  console.log('[App] Ready');
}

/**
 * Set up store subscriptions for reactive updates
 */
function setupSubscriptions() {
  // Connection status changes
  Store.subscribe('connected', (connected) => {
    Components.updateConnectionStatus(connected);
  });

  // Runs collection changes - re-render list
  Store.subscribe('runs', () => {
    Components.renderRunList();
    Components.updateFeedbackBar();
  });

  // Selected run changes
  Store.subscribe('selectedRunId', (runId) => {
    // Update selection highlighting
    document.querySelectorAll('.run-card').forEach(card => {
      card.classList.toggle('selected', card.dataset.runId === runId);
    });

    // Re-render content
    Components.renderOutput();
    Components.renderCritique();
    Components.updateFeedbackBar();

    // On mobile, switch to detail panel when selecting
    if (runId && window.innerWidth < 768) {
      Store.setState({ mobileActivePanel: 'detail' });
      document.querySelectorAll('.mobile-tabs__btn').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.panel === 'detail');
      });
      document.getElementById('left-panel').classList.add('mobile-hidden');
      document.getElementById('right-panel').classList.remove('mobile-hidden');
    }
  });

  // Output buffer changes for selected run
  Store.subscribe('outputBuffers', (buffers, oldBuffers, path) => {
    const selectedRunId = Store.getState().selectedRunId;
    if (selectedRunId && path.includes(selectedRunId)) {
      Components.renderOutput();
    }
  });

  // Active tab changes
  Store.subscribe('activeTab', (tab) => {
    if (tab === 'output') Components.renderOutput();
    if (tab === 'critique') Components.renderCritique();
  });
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}

// Export API for components
window.API = API;
