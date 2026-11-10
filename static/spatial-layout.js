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

  function toggleGlassDrawer(forceOpen) {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    const isOpen = typeof forceOpen === 'boolean' ? forceOpen : !sidebar.classList.contains('drawer-open');
    sidebar.classList.toggle('drawer-open', isOpen);
    sidebar.classList.toggle('open', isOpen);
    sidebar.classList.remove('collapsed');
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
    document.documentElement.setAttribute('data-spatial-layout', 'bottom-dock');

    const openBtn = document.getElementById('btn-open-sidebar');
    const collapseBtn = document.getElementById('btn-collapse-sidebar');
    openBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleGlassDrawer();
    });
    collapseBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleGlassDrawer(false);
    });

    // Enable all wizard step buttons in the 2-column timetable dashboard so users can jump freely
    const wizardProgress = document.getElementById('planner-wizard-progress');
    if (wizardProgress) {
      wizardProgress.addEventListener('click', (e) => {
        const btn = e.target.closest('button[aria-controls]');
        if (!btn) return;
        const targetId = btn.getAttribute('aria-controls');
        if (!targetId) return;
        document.querySelectorAll('.planner-wizard-screen').forEach((sc) => {
          sc.hidden = sc.id !== targetId;
        });
        Array.from(wizardProgress.querySelectorAll('button')).forEach((b) => {
          b.setAttribute('aria-current', b === btn ? 'step' : 'false');
        });
      });
    }

    bindBentoActions();
  }

  window.ARYMSpatialLayout = {
    toggleGlassDrawer,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
