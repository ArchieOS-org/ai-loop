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

  async stopJob(jobId) {
    try {
      const response = await fetch(`/api/jobs/${jobId}/stop`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': this.csrfToken,
        },
      });
      return await response.json();
    } catch (error) {
      console.error('[API] Stop job error:', error);
      return { error: error.message };
    }
  },

  async killJob(jobId) {
    try {
      const response = await fetch(`/api/jobs/${jobId}/kill`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': this.csrfToken,
        },
      });
      return await response.json();
    } catch (error) {
      console.error('[API] Kill job error:', error);
      return { error: error.message };
    }
  },

  async stopAllJobs() {
    try {
      const response = await fetch('/api/jobs/stop-all', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': this.csrfToken,
        },
      });
      return await response.json();
    } catch (error) {
      console.error('[API] Stop all jobs error:', error);
      return { error: error.message };
    }
  },

  async getJobs() {
    try {
      const response = await fetch('/api/jobs');
      return await response.json();
    } catch (error) {
      console.error('[API] Get jobs error:', error);
      return [];
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

  // Initialize Timeline component
  Timeline.init('timeline-container');

  // Set up UI components
  Components.setupMobileTabs();
  Components.setupResizer();
  Components.setupFeedbackBar();
  Components.setupGlobalApproval();
  Components.setupIssuePicker();
  Components.setupProjectPicker();

  // Set up timeline controls
  setupTimelineControls();

  // Subscribe to store changes
  setupSubscriptions();

  // Connect to SSE
  SSE.connect();

  // Initial render
  Components.renderRunList();

  console.log('[App] Ready');
}

/**
 * Set up timeline header controls
 */
function setupTimelineControls() {
  const btnExpandAll = document.getElementById('btn-expand-all');
  const btnCollapseAll = document.getElementById('btn-collapse-all');
  const btnStop = document.getElementById('btn-stop');
  const btnJumpLatest = document.getElementById('btn-jump-latest');
  const titleEl = document.getElementById('timeline-run-title');

  // Expand all
  btnExpandAll?.addEventListener('click', () => {
    Timeline.expandAll();
  });

  // Collapse all
  btnCollapseAll?.addEventListener('click', () => {
    Timeline.collapseAll();
  });

  // Stop button (stops all running jobs)
  btnStop?.addEventListener('click', async () => {
    const currentState = btnStop.dataset.state || 'idle';

    if (currentState === 'idle') {
      // First click: request graceful stop
      btnStop.dataset.state = 'stopping';
      btnStop.textContent = 'Stopping...';
      btnStop.disabled = true;

      const result = await API.stopAllJobs();
      console.log('[Stop] Result:', result);

      if (result.count > 0) {
        // Start timer for force stop option
        setTimeout(() => {
          if (btnStop.dataset.state === 'stopping') {
            btnStop.dataset.state = 'force_available';
            btnStop.textContent = 'Force Stop';
            btnStop.disabled = false;
            btnStop.classList.add('btn-danger');
          }
        }, 5000);
      } else {
        // No jobs to stop
        btnStop.dataset.state = 'idle';
        btnStop.textContent = 'Stop';
        btnStop.disabled = false;
      }
    } else if (currentState === 'force_available') {
      // Second click: force kill
      btnStop.dataset.state = 'killing';
      btnStop.textContent = 'Killing...';
      btnStop.disabled = true;

      // Kill all jobs that are still stopping
      const jobs = await API.getJobs();
      for (const job of jobs) {
        if (job.status === 'stopping') {
          await API.killJob(job.job_id);
        }
      }

      btnStop.dataset.state = 'idle';
      btnStop.textContent = 'Stop';
      btnStop.disabled = false;
      btnStop.classList.remove('btn-danger');
    }
  });

  // Jump to latest
  btnJumpLatest?.addEventListener('click', () => {
    Timeline.scrollToBottom();
    btnJumpLatest.classList.add('hidden');
  });

  // Keyboard shortcuts
  document.addEventListener('keydown', (e) => {
    // Cmd/Ctrl + E: Expand all
    if ((e.metaKey || e.ctrlKey) && e.key === 'e' && !e.shiftKey) {
      e.preventDefault();
      Timeline.expandAll();
    }
    // Cmd/Ctrl + Shift + E: Collapse all
    if ((e.metaKey || e.ctrlKey) && e.key === 'e' && e.shiftKey) {
      e.preventDefault();
      Timeline.collapseAll();
    }
  });
}

/**
 * Set up store subscriptions for reactive updates
 */
function setupSubscriptions() {
  // Connection status changes
  Store.subscribe('connected', (connected) => {
    Components.updateConnectionStatus(connected);
  });

  // Runs collection changes - re-render list and update stop button visibility
  Store.subscribe('runs', () => {
    Components.renderRunList();
    Components.updateFeedbackBar();
    updateStopButtonVisibility();
  });

  // Selected run changes
  Store.subscribe('selectedRunId', (runId) => {
    // Update selection highlighting
    document.querySelectorAll('.run-card').forEach(card => {
      card.classList.toggle('selected', card.dataset.runId === runId);
    });

    // Update timeline title
    const titleEl = document.getElementById('timeline-run-title');
    if (titleEl) {
      const run = runId ? Store.getRun(runId) : null;
      titleEl.textContent = run ? (run.issue_identifier || 'Run') : 'Select a run';
    }

    // Switch timeline to selected run
    Timeline.setRun(runId);

    // Update feedback bar
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
}

/**
 * Update stop button visibility based on active runs
 */
function updateStopButtonVisibility() {
  const btnStop = document.getElementById('btn-stop');
  if (!btnStop) return;

  const runs = Store.getState().runs;
  const hasActiveRuns = Array.from(runs.values()).some(run => {
    const status = run.status || '';
    return ['planning', 'coding', 'testing', 'running', 'plan_gate', 'code_gate', 'pending', 'queued'].includes(status);
  });

  btnStop.classList.toggle('hidden', !hasActiveRuns);

  // Reset button state when no active runs
  if (!hasActiveRuns) {
    btnStop.dataset.state = 'idle';
    btnStop.textContent = 'Stop';
    btnStop.disabled = false;
    btnStop.classList.remove('btn-danger');
  }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}

// Export API for components
window.API = API;
