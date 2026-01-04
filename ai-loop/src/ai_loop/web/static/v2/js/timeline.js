/**
 * AI Loop Timeline Component
 * Chronological view of pipeline events with virtualization
 */

const Timeline = {
  VIRTUALIZE_THRESHOLD: 300,  // entries before windowing kicks in

  container: null,
  currentRunId: null,
  renderedEntryIds: new Set(),

  /**
   * Initialize timeline component
   * @param {string} containerId - DOM ID of the timeline container
   */
  init(containerId) {
    this.container = document.getElementById(containerId);
    if (!this.container) {
      console.error('[Timeline] Container not found:', containerId);
      return;
    }

    this.container.addEventListener('scroll', () => this.onScroll());
    this.container.addEventListener('click', (e) => this.onClick(e));

    // Subscribe to store events
    Store.subscribe('entry:added', ({ runId, entry }) => {
      if (runId === this.currentRunId) {
        this.appendEntryNode(entry);
      }
    });

    Store.subscribe('entry:updated', ({ runId, entry }) => {
      if (runId === this.currentRunId) {
        this.updateEntryNode(entry);
      }
    });

    console.log('[Timeline] Initialized');
  },

  /**
   * Set the current run to display
   * @param {string} runId - The run ID
   */
  setRun(runId) {
    this.currentRunId = runId;
    this.container.innerHTML = '';
    this.renderedEntryIds.clear();

    if (!runId) {
      this.container.innerHTML = '<div class="timeline-empty">Select a run to view timeline</div>';
      return;
    }

    const entries = Store.getTimelineEntries(runId);
    if (entries.length === 0) {
      this.container.innerHTML = '<div class="timeline-empty">No events yet...</div>';
      return;
    }

    entries.forEach(entry => this.appendEntryNode(entry));
    this.scrollToBottom();
  },

  /**
   * Append an entry node to the timeline
   * @param {object} entry - Timeline entry
   */
  appendEntryNode(entry) {
    if (this.renderedEntryIds.has(entry.id)) return;  // Prevent duplicates

    // Check for empty state placeholder
    const empty = this.container.querySelector('.timeline-empty');
    if (empty) empty.remove();

    if (this.shouldVirtualize()) {
      this.virtualize();
    }

    const node = this.createEntryNode(entry);
    this.container.appendChild(node);
    this.renderedEntryIds.add(entry.id);

    if (Store.getState().userAtBottom) {
      this.scrollToBottom();
    }
  },

  /**
   * Update an existing entry node
   * @param {object} entry - Updated entry
   */
  updateEntryNode(entry) {
    const card = this.container.querySelector(`[data-entry-id="${entry.id}"]`);
    if (card) {
      const newNode = this.createEntryNode(entry);
      card.innerHTML = newNode.innerHTML;
      // Copy attributes
      card.className = newNode.className;
      if (entry.payload?.approved !== undefined) {
        card.dataset.approved = entry.payload.approved;
      }
    }
  },

  /**
   * Upsert phase output card - update existing or create new
   * @param {string} runId - The run ID
   * @param {string} phase - The phase
   */
  upsertPhaseOutputCard(runId, phase) {
    const phaseOutputId = `output:${runId}:${phase}`;
    const entries = Store.getTimelineEntries(runId);
    const entry = entries.find(e => e.id === phaseOutputId);
    if (!entry) return;

    // Find existing DOM node
    let card = this.container.querySelector(`[data-entry-id="${phaseOutputId}"]`);

    if (card) {
      // Update existing card
      const newNode = this.createEntryNode(entry);
      card.innerHTML = newNode.innerHTML;
    } else {
      // Create new card
      if (this.shouldVirtualize()) {
        this.virtualize();
      }
      const node = this.createEntryNode(entry);
      this.container.appendChild(node);
      this.renderedEntryIds.add(entry.id);
    }

    if (Store.getState().userAtBottom) {
      this.scrollToBottom();
    }
  },

  /**
   * Create a DOM node for an entry
   * @param {object} entry - Timeline entry
   * @returns {HTMLElement}
   */
  createEntryNode(entry) {
    const div = document.createElement('div');
    div.className = `timeline-card timeline-card--${entry.kind.replace('.', '-')}`;
    div.dataset.entryId = entry.id;
    if (entry.payload?.approved !== undefined) {
      div.dataset.approved = entry.payload.approved;
    }
    if (entry.severity) {
      div.dataset.severity = entry.severity;
    }

    switch (entry.kind) {
      case 'run.phase_output':
        div.innerHTML = this.renderPhaseOutputCard(entry);
        break;
      case 'run.output':
        div.innerHTML = this.renderOutputCard(entry);
        break;
      case 'run.artifact':
        div.innerHTML = this.renderArtifactCard(entry);
        break;
      case 'run.gate':
        div.innerHTML = this.renderGateCard(entry);
        break;
      case 'run.phase':
        div.innerHTML = this.renderPhaseCard(entry);
        break;
      case 'run.milestone':
        div.innerHTML = this.renderMilestoneCard(entry);
        break;
      case 'run.created':
        div.innerHTML = this.renderCreatedCard(entry);
        break;
      default:
        div.innerHTML = this.renderSystemCard(entry);
    }

    return div;
  },

  /**
   * Render phase output card (ONE card per phase, MULTIPLE steps inside)
   */
  renderPhaseOutputCard(entry) {
    const steps = entry.payload.steps || [];
    const collapsed = Store.isCollapsed(entry.id);
    const safeTitle = this.escapeHtml(entry.title);

    // Calculate total duration and chars
    const totalDuration = steps.reduce((sum, s) => sum + (s.duration_s || 0), 0);
    const totalChars = steps.reduce((sum, s) => sum + (s.char_count || 0), 0);
    const stats = `${steps.length} step${steps.length !== 1 ? 's' : ''}, ${totalChars.toLocaleString()} chars, ${totalDuration.toFixed(1)}s`;

    // Render all step sections
    const stepsHtml = steps.map(step => {
      const safeStep = this.escapeHtml((step.step || 'output').replace(/_/g, ' '));
      const safeText = this.escapeHtml(step.text || '');
      const duration = step.duration_s ? ` (${step.duration_s.toFixed(1)}s)` : '';
      return `
        <div class="output-step">
          <div class="step-header">${safeStep}${duration}</div>
          <pre class="output-text">${safeText}</pre>
        </div>
      `;
    }).join('');

    return `
      <div class="card-header card-header--output" data-action="toggle">
        <span class="card-icon">&#9654;</span>
        <span class="card-title">${safeTitle}</span>
        <span class="card-stats">${stats}</span>
        <span class="card-chevron">${collapsed ? '&#9656;' : '&#9662;'}</span>
      </div>
      <div class="output-body ${collapsed ? 'collapsed' : ''}">
        ${stepsHtml}
      </div>
    `;
  },

  /**
   * Render single output card (legacy, for direct run.output events)
   */
  renderOutputCard(entry) {
    const collapsed = Store.isCollapsed(entry.id);
    const safeTitle = this.escapeHtml(entry.title);
    const safeText = this.escapeHtml(entry.payload?.text || '');
    const duration = entry.payload?.duration_s ? ` (${entry.payload.duration_s.toFixed(1)}s)` : '';

    return `
      <div class="card-header card-header--output" data-action="toggle">
        <span class="card-icon">&#9654;</span>
        <span class="card-title">${safeTitle}</span>
        <span class="card-stats">${entry.payload?.char_count?.toLocaleString() || 0} chars${duration}</span>
        <span class="card-chevron">${collapsed ? '&#9656;' : '&#9662;'}</span>
      </div>
      <div class="output-body ${collapsed ? 'collapsed' : ''}">
        <pre class="output-text">${safeText}</pre>
      </div>
    `;
  },

  /**
   * Render gate card with approval status
   */
  renderGateCard(entry) {
    const { confidence, approved, blockers = [], warnings = [], pending } = entry.payload;
    const collapsed = Store.isCollapsed(entry.id);
    const safeTitle = this.escapeHtml(entry.title);

    const icon = pending ? '&#9679;' : (approved ? '&#10003;' : '&#10007;');
    const iconClass = pending ? 'pending' : (approved ? 'approved' : 'blocked');

    const blockersHtml = blockers.length ? `
      <div class="gate-blockers">
        ${blockers.map(b => `<div class="blocker">&#8226; ${this.escapeHtml(b)}</div>`).join('')}
      </div>
    ` : '';

    const warningsHtml = warnings.length ? `
      <div class="gate-warnings">
        ${warnings.map(w => `<div class="warning">&#8226; ${this.escapeHtml(w)}</div>`).join('')}
      </div>
    ` : '';

    return `
      <div class="card-header" data-action="toggle">
        <span class="card-icon card-icon--${iconClass}">${icon}</span>
        <span class="card-title">${safeTitle}</span>
        ${confidence !== undefined ? `<span class="confidence-badge">${confidence}%</span>` : ''}
        <span class="card-chevron">${collapsed ? '&#9656;' : '&#9662;'}</span>
      </div>
      <div class="card-body ${collapsed ? 'collapsed' : ''}">
        ${blockersHtml}
        ${warningsHtml}
      </div>
    `;
  },

  /**
   * Render artifact card
   */
  renderArtifactCard(entry) {
    const safeTitle = this.escapeHtml(entry.title);
    const { type, version, path } = entry.payload;

    return `
      <div class="card-header card-header--artifact">
        <span class="card-icon">&#128196;</span>
        <span class="card-title">${safeTitle}</span>
        ${path ? `<span class="card-path">${this.escapeHtml(path)}</span>` : ''}
      </div>
    `;
  },

  /**
   * Render phase transition card
   */
  renderPhaseCard(entry) {
    const safeTitle = this.escapeHtml(entry.title);
    const phaseIcon = {
      planning: '&#128221;',      // memo
      implementation: '&#9881;',   // gear
      fixing: '&#128295;'          // wrench
    };
    const icon = phaseIcon[entry.phase] || '&#9654;';

    return `
      <div class="card-header card-header--phase">
        <span class="card-icon">${icon}</span>
        <span class="card-title">${safeTitle}</span>
        <span class="card-timestamp">${this.formatTime(entry.ts)}</span>
      </div>
    `;
  },

  /**
   * Render milestone card
   */
  renderMilestoneCard(entry) {
    const safeTitle = this.escapeHtml(entry.title);
    const isError = entry.severity === 'error';
    const icon = isError ? '&#10060;' : '&#10004;';

    return `
      <div class="card-header card-header--milestone ${isError ? 'card-header--error' : ''}">
        <span class="card-icon">${icon}</span>
        <span class="card-title">${safeTitle}</span>
        <span class="card-timestamp">${this.formatTime(entry.ts)}</span>
      </div>
    `;
  },

  /**
   * Render run created card
   */
  renderCreatedCard(entry) {
    const safeTitle = this.escapeHtml(entry.title);

    return `
      <div class="card-header card-header--created">
        <span class="card-icon">&#128640;</span>
        <span class="card-title">${safeTitle}</span>
        <span class="card-timestamp">${this.formatTime(entry.ts)}</span>
      </div>
    `;
  },

  /**
   * Render system/info card (default fallback)
   */
  renderSystemCard(entry) {
    const safeTitle = this.escapeHtml(entry.title);
    const isError = entry.severity === 'error';
    const isWarn = entry.severity === 'warn';
    const icon = isError ? '&#10060;' : (isWarn ? '&#9888;' : '&#8505;');

    return `
      <div class="card-header card-header--system ${isError ? 'card-header--error' : ''} ${isWarn ? 'card-header--warn' : ''}">
        <span class="card-icon">${icon}</span>
        <span class="card-title">${safeTitle}</span>
        <span class="card-timestamp">${this.formatTime(entry.ts)}</span>
      </div>
    `;
  },

  /**
   * Handle click events
   */
  onClick(e) {
    // Handle load-earlier stub click
    const loadEarlier = e.target.closest('[data-action="load-earlier"]');
    if (loadEarlier) {
      this.loadEarlier();
      return;
    }

    // Handle toggle collapse
    const toggle = e.target.closest('[data-action="toggle"]');
    if (toggle) {
      const card = toggle.closest('.timeline-card');
      const entryId = card?.dataset.entryId;
      if (entryId) {
        Store.toggleCollapsed(entryId);
        const entries = Store.getTimelineEntries(this.currentRunId);
        const entry = entries.find(e => e.id === entryId);
        if (entry) {
          const newNode = this.createEntryNode(entry);
          card.innerHTML = newNode.innerHTML;
        }
      }
    }
  },

  /**
   * Handle scroll events
   */
  onScroll() {
    const { scrollTop, scrollHeight, clientHeight } = this.container;
    const atBottom = scrollHeight - scrollTop - clientHeight < 50;
    Store.setUserAtBottom(atBottom);

    // Show/hide jump to latest button
    const jumpBtn = document.getElementById('btn-jump-latest');
    if (jumpBtn) {
      jumpBtn.classList.toggle('hidden', atBottom);
    }
  },

  /**
   * Check if we should virtualize
   */
  shouldVirtualize() {
    return this.renderedEntryIds.size > this.VIRTUALIZE_THRESHOLD;
  },

  /**
   * Window older entries by collapsing into a stub
   */
  virtualize() {
    const toCollapse = Math.floor(this.VIRTUALIZE_THRESHOLD / 3);
    const nodes = Array.from(this.container.querySelectorAll('.timeline-card:not(.timeline-stub)'));

    if (nodes.length <= toCollapse) return;

    // Create or update stub
    let stub = this.container.querySelector('.timeline-stub');
    const collapsedCount = stub ? parseInt(stub.dataset.count || '0') : 0;

    if (!stub) {
      stub = document.createElement('div');
      stub.className = 'timeline-stub';
      stub.dataset.action = 'load-earlier';
      this.container.prepend(stub);
    }

    // Collapse older nodes (keep in store, remove from DOM)
    for (let i = 0; i < toCollapse && nodes[i]; i++) {
      this.renderedEntryIds.delete(nodes[i].dataset.entryId);
      nodes[i].remove();
    }

    // Update stub count
    const newCount = collapsedCount + toCollapse;
    stub.dataset.count = newCount;
    stub.innerHTML = `
      <button class="stub-button" data-action="load-earlier">
        &#8593; Load earlier events (${newCount})
      </button>
    `;
  },

  /**
   * Load earlier events from store
   */
  loadEarlier() {
    const entries = Store.getTimelineEntries(this.currentRunId);
    const stub = this.container.querySelector('.timeline-stub');
    if (!stub) return;

    // Find entries not yet rendered
    const unrendered = entries.filter(e => !this.renderedEntryIds.has(e.id));
    const toLoad = unrendered.slice(0, 50);

    // Save scroll position
    const scrollBottom = this.container.scrollHeight - this.container.scrollTop;

    // Render earlier entries after stub
    toLoad.forEach(entry => {
      if (this.renderedEntryIds.has(entry.id)) return;
      const node = this.createEntryNode(entry);
      stub.after(node);
      this.renderedEntryIds.add(entry.id);
    });

    // Update or remove stub
    const remaining = unrendered.length - toLoad.length;
    if (remaining > 0) {
      stub.dataset.count = remaining;
      stub.querySelector('.stub-button').textContent = `\u2191 Load earlier events (${remaining})`;
    } else {
      stub.remove();
    }

    // Restore scroll position
    this.container.scrollTop = this.container.scrollHeight - scrollBottom;
  },

  /**
   * Scroll to bottom
   */
  scrollToBottom() {
    this.container.scrollTop = this.container.scrollHeight;
  },

  /**
   * Expand all entries
   */
  expandAll() {
    const entries = Store.getTimelineEntries(this.currentRunId);
    const collapsed = Store.getState().collapsedById;
    const newCollapsed = { ...collapsed };

    entries.forEach(entry => {
      newCollapsed[entry.id] = false;
    });

    Store.setState({ collapsedById: newCollapsed });
    this.setRun(this.currentRunId);  // Re-render
  },

  /**
   * Collapse all entries
   */
  collapseAll() {
    const entries = Store.getTimelineEntries(this.currentRunId);
    const collapsed = Store.getState().collapsedById;
    const newCollapsed = { ...collapsed };

    entries.forEach(entry => {
      // Only collapse entries that have toggleable bodies
      if (['run.output', 'run.phase_output', 'run.gate'].includes(entry.kind)) {
        newCollapsed[entry.id] = true;
      }
    });

    Store.setState({ collapsedById: newCollapsed });
    this.setRun(this.currentRunId);  // Re-render
  },

  /**
   * Escape HTML to prevent XSS
   */
  escapeHtml(text) {
    if (text === null || text === undefined) return '';
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  },

  /**
   * Format timestamp for display
   */
  formatTime(ts) {
    if (!ts) return '';
    const date = new Date(ts);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }
};

// Export Timeline
window.Timeline = Timeline;
