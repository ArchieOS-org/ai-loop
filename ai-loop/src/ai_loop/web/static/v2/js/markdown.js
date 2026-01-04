/**
 * Markdown rendering utility
 * - True LRU cache (refresh recency on hit)
 * - Content-aware cache keys (includes text hash)
 * - DOMPurify hook for link security (no brittle regex)
 * - Init once at boot
 */
const Markdown = {
  _initialized: false,
  _cache: new Map(),
  _maxCacheSize: 500,

  /**
   * Initialize marked + DOMPurify (called ONCE at app boot)
   */
  init() {
    if (this._initialized) return;

    // Configure marked
    marked.setOptions({
      breaks: true,
      gfm: true,
      headerIds: false,
      mangle: false
    });

    // DOMPurify hook: enforce safe link attributes (no brittle regex)
    DOMPurify.addHook('afterSanitizeAttributes', (node) => {
      if (node.tagName === 'A') {
        node.setAttribute('target', '_blank');
        node.setAttribute('rel', 'noopener noreferrer');
      }
    });

    this._initialized = true;
  },

  /**
   * Fast hash for cache keys (not for security, just collision resistance)
   * Uses djb2 algorithm - simple and fast
   */
  _fastHash(str) {
    let hash = 5381;
    for (let i = 0; i < str.length; i++) {
      hash = ((hash << 5) + hash) ^ str.charCodeAt(i);
    }
    return (hash >>> 0).toString(36);  // Unsigned, base36 for compactness
  },

  /**
   * Render markdown with TRUE LRU memoization
   * Cache key MUST include content identity (hash) for correctness
   * @param {string} text - Raw markdown text
   * @param {string} baseKey - Base cache key (entry.id + step)
   * @returns {string} Sanitized HTML
   */
  render(text, baseKey) {
    if (!text) return '';

    // Ensure initialized
    if (!this._initialized) this.init();

    // Build content-aware cache key: baseKey + length + hash
    const contentHash = this._fastHash(text);
    const cacheKey = baseKey ? `${baseKey}:${text.length}:${contentHash}` : null;

    if (!cacheKey) {
      console.warn('[Markdown] Missing baseKey, skipping cache');
      return this._renderUncached(text);
    }

    // TRUE LRU: refresh recency on cache hit (delete + re-set)
    if (this._cache.has(cacheKey)) {
      const val = this._cache.get(cacheKey);
      this._cache.delete(cacheKey);
      this._cache.set(cacheKey, val);
      return val;
    }

    // Render and sanitize
    const html = this._renderUncached(text);

    // LRU eviction: remove oldest (first) entry
    if (this._cache.size >= this._maxCacheSize) {
      const oldestKey = this._cache.keys().next().value;
      this._cache.delete(oldestKey);
    }
    this._cache.set(cacheKey, html);

    return html;
  },

  /**
   * Render without caching
   */
  _renderUncached(text) {
    const raw = marked.parse(text);
    return DOMPurify.sanitize(raw, {
      ALLOWED_TAGS: ['h1','h2','h3','h4','p','br','ul','ol','li','strong','em','code','pre','blockquote','a','span'],
      ALLOWED_ATTR: ['href', 'target', 'rel'],
      FORBID_ATTR: ['class', 'style', 'onclick', 'onerror'],
    });
  },

  /**
   * Clear cache (for testing/memory pressure)
   */
  clearCache() {
    this._cache.clear();
  }
};

window.Markdown = Markdown;

// Initialize at load
Markdown.init();
