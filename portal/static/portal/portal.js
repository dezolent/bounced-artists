(() => {
  'use strict';
  document.documentElement.classList.add('js-enhanced');
  const assetFilters = document.querySelector('[data-asset-filters], [data-catalog-filters]');
  const resultSelector = assetFilters?.matches('[data-catalog-filters]') ? '[data-catalog-results]' : '[data-asset-results]';
  const assetResults = document.querySelector(resultSelector);
  if (assetFilters && assetResults) {
    const search = assetFilters.querySelector('[name="q"]');
    const apply = assetFilters.querySelector('[type="submit"]');
    let timer;
    let request;
    let generation = 0;
    const cancel = () => {
      clearTimeout(timer);
      generation += 1;
      if (request) request.abort();
      assetResults.removeAttribute('aria-busy');
    };
    const filter = async () => {
      cancel();
      const current = generation;
      const url = new URL(assetFilters.action || window.location.href);
      url.search = new URLSearchParams(new FormData(assetFilters)).toString();
      request = new AbortController();
      assetResults.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch(url, { signal: request.signal });
        if (!response.ok) throw new Error('Asset filtering failed');
        const page = new DOMParser().parseFromString(await response.text(), 'text/html');
        if (current !== generation) return;
        const results = page.querySelector(resultSelector);
        if (!results) throw new Error('Asset results unavailable');
        const listView = assetResults.querySelector('[data-catalog-grid]')?.classList.contains('list-view');
        assetResults.replaceChildren(...results.childNodes);
        if (listView) assetResults.querySelector('[data-catalog-grid]')?.classList.add('list-view');
        const catalogTabs = document.querySelector('[data-catalog-tabs]');
        const nextTabs = page.querySelector('[data-catalog-tabs]');
        if (catalogTabs && nextTabs) catalogTabs.replaceChildren(...nextTabs.childNodes);
        const announcement = document.querySelector('[data-filter-announcement]');
        if (announcement) announcement.textContent = `${assetResults.querySelector('[data-result-count]')?.textContent || '0'} results`;
        // Workspace switches should retain the newly selected filters too.
        const links = page.querySelectorAll('.switch-options a');
        document.querySelectorAll('.switch-options a').forEach((link, index) => {
          if (links[index]) link.href = links[index].href;
        });
        history.replaceState(null, '', url);
      } catch (error) {
        if (current === generation && error.name !== 'AbortError') {
          // Keep the ordinary GET form usable if live updates fail.
          apply.hidden = false;
          window.location.assign(url.href);
        }
      } finally {
        if (current === generation) assetResults.removeAttribute('aria-busy');
      }
    };
    assetFilters.addEventListener('submit', event => { event.preventDefault(); filter(); });
    assetFilters.querySelectorAll('select').forEach(select => select.addEventListener('change', filter));
    search.addEventListener('input', event => {
      cancel();
      if (!event.isComposing) timer = setTimeout(filter, 300);
    });
    search.addEventListener('compositionstart', cancel);
    search.addEventListener('compositionend', () => { cancel(); timer = setTimeout(filter, 300); });
    apply.hidden = true;
  }
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
        document.querySelector('[data-catalog-grid]')?.classList.toggle('list-view', button.dataset.view === 'list');
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

// Existing rows remain ordinary POST forms; adding rows is an enhancement.
(() => {
  'use strict';
  const editor = document.querySelector('[data-catalog-editor]');
  if (!editor) return;
  const controls = section => {
    const prefix = section.dataset.formset;
    return {
      prefix,
      total: section.querySelector(`:scope > input[name="${prefix}-TOTAL_FORMS"]`),
      rows: section.querySelector(':scope > [data-formset-rows]'),
      template: section.querySelector(':scope > template[data-empty-row]'),
      button: section.querySelector(':scope > .section-heading > [data-add-row]')
    };
  };
  const enhance = root => {
    root.querySelectorAll('[data-formset]').forEach(section => {
      const { total, rows, template, button } = controls(section);
      if (!total || !rows || !template || !button) return;
      section.setAttribute('data-formset-ready', '');
      button.disabled = Number(total.value) >= 150;
    });
  };
  editor.addEventListener('click', event => {
    const button = event.target.closest('[data-add-row]');
    if (!button || !editor.contains(button)) return;
    const section = button.closest('[data-formset]');
    if (!section || button.closest('.editor-row-readonly')) return;
    const { prefix, total, rows, template } = controls(section);
    if (!total || !rows || !template) return;
    const index = Number(total.value);
    if (!Number.isInteger(index) || index < 0 || index >= 150) return;
    // Replace this formset's token only. Nested empty forms retain their token.
    const token = `${prefix}-__prefix__`;
    const added = document.createElement('template');
    added.innerHTML = template.innerHTML.split(token).join(`${prefix}-${index}`);
    const newRow = added.content.firstElementChild;
    if (!newRow) return;
    rows.append(added.content);
    total.value = String(index + 1);
    button.disabled = index + 1 >= 150;
    enhance(newRow);
    let message = section.querySelector(':scope > [data-formset-message]');
    if (!message) {
      message = document.createElement('p');
      message.className = 'help-text';
      message.setAttribute('data-formset-message', '');
      message.setAttribute('role', 'status');
      section.append(message);
    }
    message.textContent = index + 1 >= 150 ? 'Row added. This section has reached its limit of 150 rows.' : 'Row added. Save all changes to keep it.';
    const focus = newRow.querySelector('input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled])');
    if (focus) focus.focus();
  });
  enhance(editor);
})();
