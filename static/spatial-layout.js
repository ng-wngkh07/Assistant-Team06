/**
 * ARYM — Fixed Bottom-Dock Spatial Controller & Bento Quick-Actions Module (`static/spatial-layout.js`)
 *
 * Single-responsibility module managing:
 * 1. Enforcing the fixed `bottom-dock` spatial layout (Layout 3: Bottom Floating Liquid Glass Launcher)
 * 2. Floating Liquid Glass Slide-over Drawer (`#sidebar`) open/close state
 * 3. Bento Hub quick-action chips in `#qa-panel` and free step navigation in `#timetable-panel`
 */
(function () {
  'use strict';

  const STORE_KEY = 'sidebar_collapsed';
  const MOBILE_QUERY = '(max-width: 900px)';
  let initialized = false;

  document.documentElement?.setAttribute('data-spatial-layout', 'bottom-dock');

  function isMobileViewport() {
    if (typeof window.matchMedia === 'function') {
      return window.matchMedia(MOBILE_QUERY).matches;
    }
    return window.innerWidth <= 900;
  }

  function saveCollapsed(collapsed) {
    try {
      localStorage.setItem(STORE_KEY, collapsed ? '1' : '0');
    } catch (e) {
      /* Ignore storage quota or privacy restrictions */
    }
  }

  function isDrawerOpen() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return false;
    return (
      sidebar.classList.contains('drawer-open') ||
      sidebar.classList.contains('open') ||
      Boolean(document.body?.classList.contains('sidebar-open'))
    );
  }

  function setDrawerOpen(forceOpen, options = {}) {
    const { persist = true } = options;
    const sidebar = document.getElementById('sidebar');
    const backdrop = document.getElementById('sidebar-backdrop');
    const openBtn = document.getElementById('btn-open-sidebar');
    const body = document.body;
    const isOpen = typeof forceOpen === 'boolean' ? forceOpen : !isDrawerOpen();

    if (!isOpen && sidebar && document.activeElement && sidebar.contains(document.activeElement)) {
      if (typeof document.activeElement.blur === 'function') {
        document.activeElement.blur();
      }
    }

    if (sidebar) {
      sidebar.classList.toggle('drawer-open', isOpen);
      sidebar.classList.toggle('open', isOpen);
      sidebar.classList.remove('collapsed');
      sidebar.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
    }

    if (body) {
      body.classList.toggle('sidebar-open', isOpen);
      // Bottom-dock uses an off-canvas fixed drawer; keep legacy margin-left collapse class off.
      body.classList.remove('sidebar-collapsed');
    }

    if (backdrop) {
      backdrop.classList.toggle('open', isOpen);
      backdrop.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
    }

    if (openBtn) {
      openBtn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    }

    if (persist && !isMobileViewport()) {
      saveCollapsed(!isOpen);
    }

    return isOpen;
  }

  function toggleGlassDrawer(forceOpen) {
    return setDrawerOpen(forceOpen);
  }

  function openDrawer() {
    return setDrawerOpen(true);
  }

  function closeDrawer() {
    return setDrawerOpen(false);
  }

  function bindBentoActions() {
    document.addEventListener('click', (ev) => {
      const target = ev.target.closest('[data-bento-action]');
      if (!target) return;
      const action = target.getAttribute('data-bento-action');
      if (action === 'prompt') {
        const promptText = target.getAttribute('data-prompt') || '';
        const input = document.getElementById('user-input');
        if (input && promptText) {
          input.value = promptText;
          input.focus();
          input.dispatchEvent(new Event('input', { bubbles: true }));
        }
      } else if (action === 'open-tools') {
        document.getElementById('mode-tools')?.click();
      } else if (action === 'open-timetable') {
        document.getElementById('mode-timetable')?.click();
      } else if (action === 'open-drawer') {
        toggleGlassDrawer(true);
      }
    });
  }

  function init() {
    document.documentElement?.setAttribute('data-spatial-layout', 'bottom-dock');
    setDrawerOpen(false, { persist: false });

    if (initialized) return;
    initialized = true;

    const openBtn = document.getElementById('btn-open-sidebar');
    const collapseBtn = document.getElementById('btn-collapse-sidebar');
    const backdrop = document.getElementById('sidebar-backdrop');
    const sidebar = document.getElementById('sidebar');

    openBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleGlassDrawer();
    });

    collapseBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      closeDrawer();
    });

    backdrop?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (isDrawerOpen()) {
        closeDrawer();
      }
    });

    sidebar?.addEventListener('click', (e) => {
      if (
        e.target.closest(
          '.mode-button, #btn-new-chat-sidebar, .session-info, .history-open, #btn-open-timetable'
        )
      ) {
        closeDrawer();
      }
    });

    document.addEventListener('click', (e) => {
      if (!isDrawerOpen()) return;
      if (
        e.target.closest(
          '#sidebar, #btn-open-sidebar, [data-bento-action="open-drawer"], #arym-tour-overlay'
        )
      ) {
        return;
      }
      closeDrawer();
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && isDrawerOpen()) {
        closeDrawer();
      }
    });

    if (typeof window.matchMedia === 'function') {
      const mobileQuery = window.matchMedia(MOBILE_QUERY);
      const onBreakpointChange = () => {
        setDrawerOpen(isDrawerOpen(), { persist: false });
      };
      if (typeof mobileQuery.addEventListener === 'function') {
        mobileQuery.addEventListener('change', onBreakpointChange);
      } else if (typeof mobileQuery.addListener === 'function') {
        mobileQuery.addListener(onBreakpointChange);
      }
    }

    bindBentoActions();
  }

  window.ARYMSpatialLayout = {
    init,
    toggleGlassDrawer,
    openDrawer,
    closeDrawer,
    isDrawerOpen,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
