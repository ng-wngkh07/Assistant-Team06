/**
 * ARYM — Spatial Layout Controller & Bento Quick-Actions Module (`static/spatial-layout.js`)
 *
 * Single-responsibility module managing:
 * 1. Switching between the 3 spatial layouts (`island-bento`, `split-studio`, `bottom-dock`)
 * 2. Floating Liquid Glass Drawer (`#sidebar`) open/close state
 * 3. Bento Hub quick-action chips in `#qa-panel`
 */
(function () {
  'use strict';

  const LAYOUTS = [
    { id: 'island-bento', label: '⊞ Đảo nổi Bento' },
    { id: 'split-studio', label: '◫ Split Studio' },
    { id: 'bottom-dock', label: '⬒ Dock Đáy' },
  ];

  function getSavedLayout() {
    try {
      const saved = localStorage.getItem('arym_spatial_layout');
      if (LAYOUTS.some((l) => l.id === saved)) return saved;
    } catch (_e) {}
    return 'island-bento';
  }

  function applySpatialLayout(layoutId) {
    const valid = LAYOUTS.find((l) => l.id === layoutId) || LAYOUTS[0];
    document.documentElement.setAttribute('data-spatial-layout', valid.id);
    try {
      localStorage.setItem('arym_spatial_layout', valid.id);
    } catch (_e) {}
    const btn = document.getElementById('btn-spatial-layout-cycle');
    if (btn) {
      btn.textContent = valid.label;
      btn.setAttribute('data-layout', valid.id);
    }
  }

  function cycleSpatialLayout() {
    const current = document.documentElement.getAttribute('data-spatial-layout') || 'island-bento';
    const idx = LAYOUTS.findIndex((l) => l.id === current);
    const next = LAYOUTS[(idx + 1) % LAYOUTS.length];
    applySpatialLayout(next.id);
  }

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
    applySpatialLayout(getSavedLayout());

    document.getElementById('btn-spatial-layout-cycle')?.addEventListener('click', cycleSpatialLayout);

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
    applySpatialLayout,
    cycleSpatialLayout,
    toggleGlassDrawer,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
