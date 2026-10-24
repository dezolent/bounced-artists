/* Server-rendered, scoped totals remain usable without JavaScript. */
document.querySelectorAll('[data-pulse-chart]').forEach((chart) => {
  const caption = chart.querySelector('[data-chart-caption]');
  const reset = () => { caption.textContent = 'Platform breakdown'; };
  chart.querySelectorAll('[data-slice-label]').forEach((slice) => {
    const show = () => { caption.textContent = slice.dataset.sliceLabel; };
    slice.addEventListener('pointerenter', show);
    slice.addEventListener('pointerleave', reset);
    slice.addEventListener('focus', show);
    slice.addEventListener('blur', reset);
    slice.addEventListener('click', show);
  });
});
