/**
 * AI Loop UI Components
 * Minimal component rendering with keyed DOM updates
 */

/**
 * Create a run card element
 */
function createRunCard(run) {
  const card = document.createElement('div');
  card.className = 'run-card';
  card.dataset.runId = run.run_id;

  const isSelected = Store.getState().selectedRunId === run.run_id;
  if (isSelected) {
    card.classList.add('selected');
  }

  card.innerHTML = `
    <div class="run-card__header">
      <span class="run-card__issue-id">${escapeHtml(run.issue_identifier || 'Unknown')}</span>
      <span class="badge badge-${getStatusColor(run.status)}">${formatStatus(run.status)}</span>
    </div>
    <div class="run-card__title truncate">${escapeHtml(run.issue_title || 'Untitled')}</div>
    <div class="run-card__meta">
      <select class="select run-card__select" data-run-id="${run.run_id}">
        <option value="auto" ${run.approval_mode === 'auto' ? 'selected' : ''}>Auto</option>
        <option value="gate_on_fail" ${run.approval_mode === 'gate_on_fail' ? 'selected' : ''}>Gate on fail</option>
        <option value="always_gate" ${run.approval_mode === 'always_gate' ? 'selected' : ''}>Always gate</option>
      </select>
      ${run.confidence !== null ? `<span class="run-card__confidence">${Math.round(run.confidence * 100)}%</span>` : ''}
      <span class="run-card__time">${formatTime(run.started_at)}</span>
    </div>
  `;

  // Click to select
  card.addEventListener('click', (e) => {
    if (e.target.tagName !== 'SELECT') {
      Store.setState({ selectedRunId: run.run_id });
    }
  });

  // Approval mode change
  const select = card.querySelector('.run-card__select');
  select.addEventListener('change', async (e) => {
    e.stopPropagation();
    await API.updateRunConfig(run.run_id, { approval_mode: e.target.value });
  });

  return card;
}

/**
 * Update a run card in place
 */
function updateRunCard(card, run) {
  const badge = card.querySelector('.badge');
  badge.className = `badge badge-${getStatusColor(run.status)}`;
  badge.textContent = formatStatus(run.status);

  const confidence = card.querySelector('.run-card__confidence');
  if (run.confidence !== null) {
    if (confidence) {
      confidence.textContent = `${Math.round(run.confidence * 100)}%`;
    } else {
      const meta = card.querySelector('.run-card__meta');
      const span = document.createElement('span');
      span.className = 'run-card__confidence';
      span.textContent = `${Math.round(run.confidence * 100)}%`;
      meta.insertBefore(span, meta.lastElementChild);
    }
  }

  const select = card.querySelector('.run-card__select');
  if (select.value !== run.approval_mode) {
    select.value = run.approval_mode || 'auto';
  }

  const isSelected = Store.getState().selectedRunId === run.run_id;
  card.classList.toggle('selected', isSelected);
}

/**
 * Render all run cards into groups
 */
function renderRunList() {
  const runs = Store.getState().runs;
  const activeItems = document.getElementById('active-runs-items');
  const pendingItems = document.getElementById('pending-runs-items');
  const completedItems = document.getElementById('completed-runs-items');
  const emptyState = document.getElementById('empty-state');

  // Clear existing
  activeItems.innerHTML = '';
  pendingItems.innerHTML = '';
  completedItems.innerHTML = '';

  if (runs.size === 0) {
    emptyState.classList.remove('hidden');
    return;
  }

  emptyState.classList.add('hidden');

  // Sort runs by started_at descending
  const sortedRuns = Array.from(runs.values()).sort((a, b) => {
    return new Date(b.started_at || 0) - new Date(a.started_at || 0);
  });

  for (const run of sortedRuns) {
    const card = createRunCard(run);
    const status = run.status || 'unknown';

    if (['planning', 'coding', 'testing', 'running', 'plan_gate', 'code_gate'].includes(status)) {
      activeItems.appendChild(card);
    } else if (['pending', 'queued'].includes(status)) {
      pendingItems.appendChild(card);
    } else {
      completedItems.appendChild(card);
    }
  }

  // Show/hide groups based on content
  document.getElementById('active-runs').classList.toggle('hidden', activeItems.children.length === 0);
  document.getElementById('pending-runs').classList.toggle('hidden', pendingItems.children.length === 0);
  document.getElementById('completed-runs').classList.toggle('hidden', completedItems.children.length === 0);
}

/**
 * Render output stream for selected run
 */
function renderOutput() {
  const selectedRunId = Store.getState().selectedRunId;
  const outputPre = document.getElementById('output-pre');

  if (!selectedRunId) {
    outputPre.textContent = 'Select a run to view output...';
    return;
  }

  const buffer = Store.getOutputBuffer(selectedRunId);
  if (buffer.length === 0) {
    outputPre.textContent = 'Waiting for output...';
    return;
  }

  // Join and display (max 1000 lines already enforced by store)
  outputPre.textContent = buffer.join('\n');

  // Auto-scroll to bottom
  const container = document.getElementById('output-stream');
  container.scrollTop = container.scrollHeight;
}

/**
 * Render critique viewer for selected run
 */
function renderCritique() {
  const selectedRunId = Store.getState().selectedRunId;
  const viewer = document.getElementById('critique-viewer');

  if (!selectedRunId) {
    viewer.innerHTML = '<p class="text-secondary p-4">Select a run to view critique</p>';
    return;
  }

  const run = Store.getRun(selectedRunId);
  if (!run || !run.gate_pending) {
    viewer.innerHTML = '<p class="text-secondary p-4">No critique available</p>';
    return;
  }

  const critique = run.gate_pending.critique || {};
  viewer.innerHTML = `
    <div class="critique-content">
      <h3 class="text-lg font-semibold mb-2">${critique.gate_type || 'Gate'} Critique</h3>
      <div class="critique-score mb-3">
        <span class="text-secondary">Confidence:</span>
        <span class="font-medium ${critique.approved ? 'text-success' : 'text-warning'}">
          ${critique.confidence !== undefined ? `${Math.round(critique.confidence * 100)}%` : 'N/A'}
        </span>
        <span class="badge badge-${critique.approved ? 'success' : 'warning'} ml-2">
          ${critique.approved ? 'Approved' : 'Needs Review'}
        </span>
      </div>
      ${critique.feedback ? `
        <div class="critique-feedback">
          <h4 class="text-sm font-medium text-secondary mb-1">Feedback</h4>
          <p class="text-primary">${escapeHtml(critique.feedback)}</p>
        </div>
      ` : ''}
      ${critique.blockers && critique.blockers.length > 0 ? `
        <div class="critique-blockers mt-3">
          <h4 class="text-sm font-medium text-error mb-1">Blockers</h4>
          <ul class="list-disc pl-4">
            ${critique.blockers.map(b => `<li class="text-primary">${escapeHtml(b)}</li>`).join('')}
          </ul>
        </div>
      ` : ''}
    </div>
  `;
}

/**
 * Update feedback bar visibility
 */
function updateFeedbackBar() {
  const selectedRunId = Store.getState().selectedRunId;
  const feedbackBar = document.getElementById('feedback-bar');

  if (!selectedRunId) {
    feedbackBar.classList.add('hidden');
    return;
  }

  const run = Store.getRun(selectedRunId);
  if (run && run.gate_pending) {
    feedbackBar.classList.remove('hidden');
  } else {
    feedbackBar.classList.add('hidden');
  }
}

/**
 * Update connection status indicator
 */
function updateConnectionStatus(connected) {
  const status = document.getElementById('connection-status');
  const dot = status.querySelector('.connection-status__dot');
  const text = status.querySelector('.connection-status__text');

  if (connected) {
    dot.style.background = 'var(--color-success)';
    text.textContent = 'Connected';
  } else {
    dot.style.background = 'var(--color-warning)';
    text.textContent = 'Connecting...';
  }
}

/**
 * Handle tab switching
 */
function setupTabs() {
  const tabs = document.querySelectorAll('.tab-bar .tab');
  const contents = document.querySelectorAll('.tab-panel');

  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const targetTab = tab.dataset.tab;

      // Update active tab
      tabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');

      // Show target content
      contents.forEach(content => {
        content.classList.toggle('hidden', !content.id.endsWith(targetTab));
        content.classList.toggle('active', content.id.endsWith(targetTab));
      });

      Store.setState({ activeTab: targetTab });

      // Re-render content
      if (targetTab === 'output') renderOutput();
      if (targetTab === 'critique') renderCritique();
    });
  });
}

/**
 * Setup mobile tab navigation
 */
function setupMobileTabs() {
  const mobileTabs = document.querySelectorAll('.mobile-tabs__btn');
  const leftPanel = document.getElementById('left-panel');
  const rightPanel = document.getElementById('right-panel');

  mobileTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const panel = tab.dataset.panel;

      mobileTabs.forEach(t => t.classList.remove('active'));
      tab.classList.add('active');

      if (panel === 'list') {
        leftPanel.classList.remove('mobile-hidden');
        rightPanel.classList.add('mobile-hidden');
      } else {
        leftPanel.classList.add('mobile-hidden');
        rightPanel.classList.remove('mobile-hidden');
      }

      Store.setState({ mobileActivePanel: panel });
    });
  });
}

/**
 * Setup resizer for split pane
 */
function setupResizer() {
  const resizer = document.getElementById('resizer');
  const leftPanel = document.getElementById('left-panel');

  let isResizing = false;

  resizer.addEventListener('mousedown', (e) => {
    isResizing = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  });

  document.addEventListener('mousemove', (e) => {
    if (!isResizing) return;

    const newWidth = Math.max(280, Math.min(600, e.clientX));
    leftPanel.style.width = `${newWidth}px`;
    Store.setState({ leftPanelWidth: newWidth });
  });

  document.addEventListener('mouseup', () => {
    if (isResizing) {
      isResizing = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      Store.persistUIState();
    }
  });

  // Restore saved width
  const savedWidth = Store.getState().leftPanelWidth;
  if (savedWidth) {
    leftPanel.style.width = `${savedWidth}px`;
  }
}

/**
 * Setup feedback bar actions
 */
function setupFeedbackBar() {
  const feedbackInput = document.getElementById('feedback-input');
  const btnApprove = document.getElementById('btn-approve');
  const btnReject = document.getElementById('btn-reject');
  const btnRequestChanges = document.getElementById('btn-request-changes');

  btnApprove.addEventListener('click', async () => {
    const runId = Store.getState().selectedRunId;
    if (runId) {
      await API.submitFeedback(runId, 'approve', feedbackInput.value);
      feedbackInput.value = '';
    }
  });

  btnReject.addEventListener('click', async () => {
    const runId = Store.getState().selectedRunId;
    if (runId) {
      await API.submitFeedback(runId, 'reject', feedbackInput.value);
      feedbackInput.value = '';
    }
  });

  btnRequestChanges.addEventListener('click', async () => {
    const runId = Store.getState().selectedRunId;
    if (runId && feedbackInput.value.trim()) {
      await API.submitFeedback(runId, 'request_changes', feedbackInput.value);
      feedbackInput.value = '';
    }
  });
}

/**
 * Setup global approval mode selector
 */
function setupGlobalApproval() {
  const select = document.getElementById('global-approval');

  // Load saved preference
  const saved = Store.getState().globalApprovalMode;
  if (saved) {
    select.value = saved;
  }

  select.addEventListener('change', (e) => {
    Store.setState({ globalApprovalMode: e.target.value });
    Store.persistUIState();
  });
}

// Notification system
function showNotification(message, type = 'info') {
  let container = document.getElementById('notification-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'notification-container';
    container.style.cssText = `
      position: fixed;
      top: 1rem;
      right: 1rem;
      z-index: 1000;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    `;
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = `notification notification-${type}`;
  toast.textContent = message;
  container.appendChild(toast);

  // Auto-dismiss after 5s
  setTimeout(() => toast.remove(), 5000);
}

// Utility functions
function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function getStatusColor(status) {
  const colors = {
    pending: 'secondary',
    queued: 'secondary',
    planning: 'info',
    coding: 'info',
    testing: 'info',
    running: 'info',
    plan_gate: 'warning',
    code_gate: 'warning',
    completed: 'success',
    success: 'success',
    failed: 'danger',
    error: 'danger',
    stopped: 'secondary',
  };
  return colors[status] || 'secondary';
}

function formatStatus(status) {
  const labels = {
    pending: 'Pending',
    queued: 'Queued',
    planning: 'Planning',
    coding: 'Coding',
    testing: 'Testing',
    running: 'Running',
    plan_gate: 'Plan Gate',
    code_gate: 'Code Gate',
    completed: 'Completed',
    success: 'Success',
    failed: 'Failed',
    error: 'Error',
    stopped: 'Stopped',
  };
  return labels[status] || status || 'Unknown';
}

function formatTime(isoString) {
  if (!isoString) return '';
  const date = new Date(isoString);
  const now = new Date();
  const diff = now - date;

  if (diff < 60000) return 'just now';
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return date.toLocaleDateString();
}

/**
 * Setup issue picker functionality
 */
function setupIssuePicker() {
  const picker = document.getElementById('issue-picker');
  const toggle = document.getElementById('issue-picker-toggle');
  const refreshBtn = document.getElementById('refresh-issues');
  const selectAllBtn = document.getElementById('select-all');
  const clearBtn = document.getElementById('clear-selection');
  const startBtn = document.getElementById('start-runs');
  const issueList = document.getElementById('issue-list');

  console.log('[IssuePicker] Setup:', {
    picker: !!picker,
    toggle: !!toggle,
    refreshBtn: !!refreshBtn,
    selectAllBtn: !!selectAllBtn,
    clearBtn: !!clearBtn,
    startBtn: !!startBtn,
    issueList: !!issueList,
  });

  if (!issueList) {
    console.error('[IssuePicker] FATAL: issue-list element not found!');
    return;
  }

  // State
  let issues = [];
  let selectedIssues = new Set();

  // Toggle collapse
  toggle.addEventListener('click', () => {
    picker.classList.toggle('collapsed');
    Store.setState({ issuePickerCollapsed: picker.classList.contains('collapsed') });
    Store.persistUIState();
  });

  // Restore collapsed state
  if (Store.getState().issuePickerCollapsed) {
    picker.classList.add('collapsed');
  }

  // Refresh issues
  refreshBtn.addEventListener('click', async () => {
    await loadIssues();
  });

  // Select all
  selectAllBtn.addEventListener('click', () => {
    issues.forEach(i => selectedIssues.add(i.identifier));
    renderIssueList();
    updateStartButton();
  });

  // Clear selection
  clearBtn.addEventListener('click', () => {
    selectedIssues.clear();
    renderIssueList();
    updateStartButton();
  });

  // Start runs
  startBtn.addEventListener('click', async () => {
    if (selectedIssues.size === 0) return;

    startBtn.disabled = true;
    startBtn.textContent = 'Starting...';

    try {
      const csrfToken = document.getElementById('csrf-token')?.value || '';
      const response = await fetch('/api/runs', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken,
        },
        body: JSON.stringify({
          issue_identifiers: Array.from(selectedIssues),
          approval_mode: Store.getState().globalApprovalMode || 'auto',
        }),
      });

      const result = await response.json();

      if (result.error) {
        console.error('[IssuePicker] Start error:', result.error);
        showNotification(`Start failed: ${result.error}`, 'error');
      } else {
        // IMMEDIATELY add pending stubs to UI (keyed by temp_id = issue_identifier)
        if (result.stubs) {
          for (const stub of result.stubs) {
            // Store under temp_id (issue_identifier) - will be reconciled later
            Store.updateRun(stub.temp_id, {
              run_id: stub.temp_id,  // Temporary - SSE will provide real run_id
              issue_identifier: stub.issue_identifier,
              status: 'pending',
              is_stub: true,  // Flag for reconciliation
              approval_mode: Store.getState().globalApprovalMode || 'auto',
              started_at: new Date().toISOString(),
            });
          }
          Components.renderRunList();
        }

        // Show rejection reasons if any
        if (result.rejected && result.rejected.length > 0) {
          const reasons = result.rejected.map(id =>
            `${id}: ${result.reason_by_issue[id] || 'already running'}`
          ).join('\n');
          showNotification(`Some issues skipped:\n${reasons}`, 'warning');
        }

        // Clear selection on success
        selectedIssues.clear();
        renderIssueList();
      }
    } catch (error) {
      console.error('[IssuePicker] Start failed:', error);
      showNotification(`Network error: ${error.message}`, 'error');
    } finally {
      updateStartButton();
    }
  });

  async function loadIssues() {
    const state = document.getElementById('state-filter').value;
    const team = document.getElementById('team-filter').value.trim();
    const project = document.getElementById('project-filter').value.trim();

    console.log('[IssuePicker] Loading issues...', { state, team, project });
    issueList.innerHTML = '<div class="issue-list-loading">Loading...</div>';

    let url = `/api/issues?state=${encodeURIComponent(state)}&limit=50`;
    if (team) url += `&team=${encodeURIComponent(team)}`;
    if (project) url += `&project=${encodeURIComponent(project)}`;

    try {
      console.log('[IssuePicker] Fetching:', url);
      const res = await fetch(url);
      console.log('[IssuePicker] Response status:', res.status);
      const data = await res.json();
      console.log('[IssuePicker] Data:', data);

      if (data.error) {
        console.error('[IssuePicker] API error:', data.error);
        issueList.innerHTML = `<div class="issue-list-empty" style="color:var(--color-error);">${escapeHtml(data.error)}</div>`;
        issues = [];
        return;
      }

      issues = data || [];
      console.log('[IssuePicker] Loaded', issues.length, 'issues');
      renderIssueList();
    } catch (e) {
      console.error('[IssuePicker] Fetch error:', e);
      issueList.innerHTML = `<div class="issue-list-empty" style="color:var(--color-error);">Failed to load</div>`;
      issues = [];
    }
  }

  function renderIssueList() {
    if (issues.length === 0) {
      issueList.innerHTML = '<div class="issue-list-empty">No issues found</div>';
      return;
    }

    issueList.innerHTML = issues.map(i => `
      <label class="issue-row ${selectedIssues.has(i.identifier) ? 'selected' : ''}" data-id="${i.identifier}">
        <input type="checkbox" ${selectedIssues.has(i.identifier) ? 'checked' : ''}>
        <span class="issue-id">${escapeHtml(i.identifier)}</span>
        <span class="issue-title">${escapeHtml(i.title)}</span>
      </label>
    `).join('');

    // Add click handlers
    issueList.querySelectorAll('.issue-row').forEach(row => {
      row.addEventListener('click', (e) => {
        const id = row.dataset.id;
        if (selectedIssues.has(id)) {
          selectedIssues.delete(id);
        } else {
          selectedIssues.add(id);
        }
        renderIssueList();
        updateStartButton();
      });
    });
  }

  function updateStartButton() {
    const count = selectedIssues.size;
    startBtn.disabled = count === 0;
    startBtn.textContent = count > 0 ? `Start (${count})` : 'Start';
  }

  // Load issues on init
  loadIssues();
}

// Export components
window.Components = {
  createRunCard,
  updateRunCard,
  renderRunList,
  renderOutput,
  renderCritique,
  updateFeedbackBar,
  updateConnectionStatus,
  setupTabs,
  setupMobileTabs,
  setupResizer,
  setupFeedbackBar,
  setupGlobalApproval,
  setupIssuePicker,
};
