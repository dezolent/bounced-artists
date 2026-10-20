(() => {
  'use strict';
  document.documentElement.classList.add('js-enhanced');
  const navButton = document.querySelector('[data-nav-toggle]');
  const nav = document.getElementById('workspace-nav');
  if (navButton && nav) {
    const mobileNav = window.matchMedia('(max-width: 640px)');
    const syncNavAccess = () => { nav.inert = mobileNav.matches && !nav.classList.contains('nav-open'); };
    const closeNav = () => { nav.classList.remove('nav-open'); navButton.setAttribute('aria-expanded', 'false'); navButton.setAttribute('aria-label', 'Open navigation'); syncNavAccess(); };
    syncNavAccess();
    mobileNav.addEventListener('change', syncNavAccess);
    navButton.addEventListener('click', () => {
      const open = navButton.getAttribute('aria-expanded') !== 'true';
      nav.classList.toggle('nav-open', open);
      navButton.setAttribute('aria-expanded', String(open));
      navButton.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
      syncNavAccess();
    });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && nav.classList.contains('nav-open')) { closeNav(); navButton.focus(); } });
    document.addEventListener('click', event => { if (!nav.contains(event.target) && !navButton.contains(event.target)) closeNav(); });
  }
  document.querySelectorAll('[data-password-toggle]').forEach(button => {
    const input = document.getElementById(button.getAttribute('aria-controls'));
    if (!input) return;
    button.addEventListener('click', () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      button.textContent = show ? 'Hide' : 'Show';
      button.setAttribute('aria-pressed', String(show));
    });
  });
  const grid = document.querySelector('[data-catalog-grid]');
  const viewControls = document.querySelector('[data-view-controls]');
  if (grid && viewControls) {
    viewControls.hidden = false;
    viewControls.querySelectorAll('[data-view]').forEach(button => {
      button.addEventListener('click', () => {
        grid.classList.toggle('list-view', button.dataset.view === 'list');
        viewControls.querySelectorAll('[data-view]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      });
    });
  }
  document.querySelectorAll('[data-tabs]').forEach(group => {
    const tabs = Array.from(group.querySelectorAll('[data-tab]'));
    const panels = Array.from(group.querySelectorAll('[data-tab-panel]'));
    if (!tabs.length || tabs.length !== panels.length) return;
    const tablist = group.querySelector('.tab-nav');
    tablist.setAttribute('role', 'tablist');
    const selectTab = (tab, updateHash) => {
      tabs.forEach((item, index) => {
        const active = item === tab;
        item.setAttribute('aria-selected', String(active));
        item.tabIndex = active ? 0 : -1;
        panels[index].hidden = !active;
      });
      if (updateHash) history.replaceState(null, '', tab.getAttribute('href'));
    };
    tabs.forEach((tab, index) => {
      tab.setAttribute('role', 'tab');
      tab.id = `tab-${panels[index].id}`;
      tab.setAttribute('aria-controls', panels[index].id);
      panels[index].setAttribute('role', 'tabpanel');
      panels[index].setAttribute('aria-labelledby', tab.id);
      panels[index].tabIndex = 0;
      tab.addEventListener('click', event => { event.preventDefault(); selectTab(tab, true); });
      tab.addEventListener('keydown', event => {
        let next;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        if (next === undefined) return;
        event.preventDefault(); tabs[next].focus(); selectTab(tabs[next], true);
      });
    });
    const fromHash = () => tabs.find(tab => tab.getAttribute('href') === window.location.hash) || tabs[0];
    selectTab(fromHash(), false);
    window.addEventListener('hashchange', () => selectTab(fromHash(), false));
    // In-page links outside the tab list (such as View destinations) also activate their panel.
    document.querySelectorAll('a[href^="#"]').forEach(link => {
      if (tabs.includes(link)) return;
      link.addEventListener('click', () => {
        const targetTab = tabs.find(tab => tab.getAttribute('href') === link.getAttribute('href'));
        if (targetTab) selectTab(targetTab, false);
      });
    });
  });
})();
