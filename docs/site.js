/* Boot, header-bar tab routing, shared helpers, and small chart primitives. */
window.KOL = (function () {
  const TABS = ['results', 'catchment', 'stability', 'labels', 'backtesting', 'sources'];
  const TIER_LABEL = { firm: 'Firm', provisional: 'Provisional', unstable: 'Unstable', 'outside main network': 'Outside the main network' };
  const TIER_CLASS = { firm: 'firm', provisional: 'provisional', unstable: 'unstable', 'outside main network': 'outside' };
  const COMPOSITE_LABEL = { academic_authority: 'Academic Authority', network_connector: 'Network Connector', rising_voice: 'Rising Voice' };
  const state = { data: null, tab: null, rendered: new Set(), renderers: {} };

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const fmt = {
    pct: (v, d = 0) => v == null ? '–' : (100 * v).toFixed(d) + '%',
    num: (v, d = 2) => v == null ? '–' : Number(v).toFixed(d),
    int: (v) => v == null ? '–' : Math.round(v).toLocaleString(),
    signed: (v, d = 2) => v == null ? '–' : (v >= 0 ? '+' : '') + Number(v).toFixed(d),
    title: (s) => String(s || '').replace(/\w\S*/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase()),
  };
  const communityColor = (c, tier) => tier === 'outside main network' ? css('--c-outside') : css(`--c${Math.min(c, 13)}`);
  const tierColor = (tier) => css(`--tier-${TIER_CLASS[tier] || 'outside'}`);
  const seriesColor = (i) => css(`--c${(i % 8) + 1}`);

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') node.className = v;
      else if (k === 'html') node.innerHTML = v;
      else if (k === 'text') node.textContent = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else if (v != null) node.setAttribute(k, v);
    }
    for (const child of [].concat(children)) if (child != null) node.append(child.nodeType ? child : document.createTextNode(child));
    return node;
  }
  const h = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /* Tooltip */
  const tip = document.getElementById('tooltip');
  function showTip(event, html) {
    tip.innerHTML = html; tip.hidden = false;
    const pad = 14, w = tip.offsetWidth, hgt = tip.offsetHeight;
    let x = event.clientX + pad, y = event.clientY + pad;
    if (x + w > window.innerWidth - 8) x = event.clientX - w - pad;
    if (y + hgt > window.innerHeight - 8) y = event.clientY - hgt - pad;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  const hideTip = () => { tip.hidden = true; };

  /* Router: header bar, keyboard arrows, hash */
  function setTab(name, push = true) {
    const [tab, sub] = String(name || '').split('/');
    name = tab;
    if (!TABS.includes(name)) name = TABS[0];
    state.tab = name; state.sub = sub || null;
    document.querySelectorAll('.tab').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
    document.querySelectorAll('.panel').forEach((p) => { p.hidden = p.dataset.tab !== name; });
    const active = document.querySelector(`.tab[data-tab="${name}"]`);
    if (active) active.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    if (push && location.hash.split('/')[0] !== '#' + name) history.pushState(null, '', '#' + name);
    if (state.rendered.has(name) && state.onSub?.[name]) state.onSub[name](state.sub);
    if (!state.rendered.has(name) && state.renderers[name] && state.data) {
      state.rendered.add(name);
      try { state.renderers[name](state.data); } catch (err) { console.error(err); document.getElementById(`${name}-content`)?.append(el('p', { class: 'note', text: 'This section failed to render: ' + err.message })); }
    }
    window.scrollTo({ top: 0 });
  }
  const step = (delta) => setTab(TABS[(TABS.indexOf(state.tab) + delta + TABS.length) % TABS.length]);

  function wireRouter() {
    document.getElementById('tab-list').addEventListener('click', (e) => { const b = e.target.closest('.tab'); if (b) setTab(b.dataset.tab); });
    document.getElementById('tab-prev').addEventListener('click', () => step(-1));
    document.getElementById('tab-next').addEventListener('click', () => step(1));
    window.addEventListener('hashchange', () => setTab(location.hash.slice(1) || TABS[0], false));
    window.addEventListener('popstate', () => setTab(location.hash.slice(1) || TABS[0], false));
    window.addEventListener('keydown', (e) => {
      if (e.target.matches('input, textarea, select')) return;
      if (e.key === 'ArrowRight' || e.key === ']') step(1);
      if (e.key === 'ArrowLeft' || e.key === '[') step(-1);
    });
  }

  /* Small chart primitives (D3). Each takes a container element and returns the svg selection. */
  function svgIn(container, width, height) {
    const sel = d3.select(container).classed('chart', true);
    sel.selectAll('svg').remove();
    return sel.append('svg').attr('viewBox', `0 0 ${width} ${height}`).attr('role', 'img').style('max-width', `${width}px`);
  }

  /* Horizontal dot-and-whisker: rows = [{label, value, lo, hi, color, note, hl, dashed}] */
  function dotWhisker(container, rows, opts = {}) {
    const { domain, title, sub, ref = null, width = 640, rowH = 24, left = 220, fmtv = fmt.num, xLabel = '' } = opts;
    const height = rows.length * rowH + 76;
    const svg = svgIn(container, width, height);
    const x = d3.scaleLinear().domain(domain || d3.extent(rows.flatMap((r) => [r.lo ?? r.value, r.hi ?? r.value]))).nice().range([left, width - 70]);
    const y = (i) => 40 + i * rowH + rowH / 2;
    if (title) svg.append('text').attr('class', 'title').attr('x', 0).attr('y', 14).text(title);
    if (sub) svg.append('text').attr('class', 'sub').attr('x', 0).attr('y', 28).text(sub);
    svg.append('g').attr('class', 'grid').attr('transform', `translate(0,${height - 34})`).call(d3.axisBottom(x).ticks(6).tickSize(-(height - 74)).tickFormat(''));
    svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${height - 34})`).call(d3.axisBottom(x).ticks(6));
    if (xLabel) svg.append('text').attr('class', 'sub').attr('x', width - 70).attr('y', height - 6).attr('text-anchor', 'end').text(xLabel);
    if (ref != null) svg.append('line').attr('class', 'ref').attr('x1', x(ref)).attr('x2', x(ref)).attr('y1', 36).attr('y2', height - 34);
    const g = svg.append('g').selectAll('g').data(rows).join('g').attr('transform', (d, i) => `translate(0,${y(i)})`);
    g.append('text').attr('class', 'row-label').attr('x', left - 10).attr('dy', '0.35em').attr('text-anchor', 'end').attr('font-weight', (d) => d.hl ? 700 : 400).text((d) => d.label);
    g.filter((d) => d.lo != null).append('line').attr('x1', (d) => x(d.lo)).attr('x2', (d) => x(d.hi)).attr('stroke', (d) => d.color).attr('stroke-width', 2).attr('stroke-dasharray', (d) => d.dashed ? '3 3' : null);
    g.append('circle').attr('cx', (d) => x(d.value)).attr('r', 5).attr('fill', (d) => d.color).attr('stroke', css('--surface')).attr('stroke-width', 2);
    g.append('text').attr('class', 'mark-label').attr('x', (d) => x(d.hi ?? d.value) + 8).attr('dy', '0.35em').text((d) => fmtv(d.value) + (d.note ? ' ' + d.note : ''));
    g.append('rect').attr('x', 0).attr('y', -rowH / 2).attr('width', width).attr('height', rowH).attr('fill', 'transparent')
      .on('mousemove', (e, d) => showTip(e, d.tip || `<b>${esc(d.label)}</b><br>${fmtv(d.value)}${d.lo != null ? ` <span class="tt-muted">[${fmtv(d.lo)}, ${fmtv(d.hi)}]</span>` : ''}`)).on('mouseleave', hideTip);
    return svg;
  }

  /* Horizontal bars with optional whisker: rows = [{label, value, lo, hi, color, tip}] */
  function hBars(container, rows, opts = {}) {
    const { domain = [0, 1], title, sub, width = 640, rowH = 20, left = 220, fmtv = fmt.num, ref = null } = opts;
    const height = rows.length * rowH + 70;
    const svg = svgIn(container, width, height);
    const x = d3.scaleLinear().domain(domain).range([left, width - 60]);
    if (title) svg.append('text').attr('class', 'title').attr('x', 0).attr('y', 14).text(title);
    if (sub) svg.append('text').attr('class', 'sub').attr('x', 0).attr('y', 28).text(sub);
    svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${height - 28})`).call(d3.axisBottom(x).ticks(5));
    if (ref != null) svg.append('line').attr('class', 'ref').attr('x1', x(ref)).attr('x2', x(ref)).attr('y1', 36).attr('y2', height - 28);
    const g = svg.append('g').selectAll('g').data(rows).join('g').attr('transform', (d, i) => `translate(0,${40 + i * rowH})`);
    g.append('text').attr('class', 'row-label').attr('x', left - 10).attr('y', rowH / 2).attr('dy', '0.35em').attr('text-anchor', 'end').text((d) => d.label);
    g.append('rect').attr('x', x(0)).attr('y', 3).attr('height', rowH - 6).attr('width', (d) => Math.max(0, x(d.value) - x(0))).attr('rx', 3).attr('fill', (d) => d.color);
    g.filter((d) => d.lo != null).append('line').attr('x1', (d) => x(d.lo)).attr('x2', (d) => x(d.hi)).attr('y1', rowH / 2).attr('y2', rowH / 2).attr('stroke', css('--ink')).attr('stroke-width', 1.5);
    g.append('text').attr('class', 'mark-label').attr('x', (d) => x(Math.max(d.value, d.hi ?? 0)) + 6).attr('y', rowH / 2).attr('dy', '0.35em').text((d) => fmtv(d.value));
    g.append('rect').attr('x', 0).attr('y', 0).attr('width', width).attr('height', rowH).attr('fill', 'transparent')
      .on('mousemove', (e, d) => showTip(e, d.tip || `<b>${esc(d.label)}</b><br>${fmtv(d.value)}`)).on('mouseleave', hideTip);
    return svg;
  }

  /* Multi-series line chart: series = [{name, color, dashed, points: [[x,y],...]}] */
  function lines(container, series, opts = {}) {
    const { width = 520, height = 400, xDomain, yDomain, xLabel, yLabel, title, diagonal = false, xFmt = fmt.num, yFmt = fmt.num, margin = { t: 30, r: 16, b: 40, l: 48 } } = opts;
    const svg = svgIn(container, width, height);
    const x = d3.scaleLinear().domain(xDomain || d3.extent(series.flatMap((s) => s.points.map((p) => p[0])))).range([margin.l, width - margin.r]);
    const y = d3.scaleLinear().domain(yDomain || d3.extent(series.flatMap((s) => s.points.map((p) => p[1])))).nice().range([height - margin.b, margin.t]);
    if (title) svg.append('text').attr('class', 'title').attr('x', margin.l).attr('y', 16).text(title);
    svg.append('g').attr('class', 'grid').attr('transform', `translate(${margin.l},0)`).call(d3.axisLeft(y).ticks(5).tickSize(-(width - margin.l - margin.r)).tickFormat(''));
    svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${height - margin.b})`).call(d3.axisBottom(x).ticks(5));
    svg.append('g').attr('class', 'axis').attr('transform', `translate(${margin.l},0)`).call(d3.axisLeft(y).ticks(5));
    if (xLabel) svg.append('text').attr('class', 'sub').attr('x', width - margin.r).attr('y', height - 6).attr('text-anchor', 'end').text(xLabel);
    if (yLabel) svg.append('text').attr('class', 'sub').attr('x', margin.l).attr('y', margin.t - 6).text(yLabel);
    if (diagonal) svg.append('line').attr('class', 'ref').attr('x1', x(0)).attr('y1', y(0)).attr('x2', x(1)).attr('y2', y(1));
    const line = d3.line().x((p) => x(p[0])).y((p) => y(p[1]));
    svg.append('g').selectAll('path').data(series).join('path').attr('d', (s) => line(s.points)).attr('fill', 'none')
      .attr('stroke', (s) => s.color).attr('stroke-width', (s) => s.width || 2).attr('stroke-dasharray', (s) => s.dashed ? '5 4' : null).attr('opacity', (s) => s.opacity ?? 1)
      .on('mousemove', (e, s) => showTip(e, `<b>${esc(s.name)}</b>${s.note ? '<br>' + s.note : ''}`)).on('mouseleave', hideTip);
    return { svg, x, y };
  }

  function table(container, rows, columns) {
    const wrap = el('div', { class: 'table-wrap' });
    const t = el('table', { class: 'data' });
    t.append(el('thead', {}, el('tr', {}, columns.map((c) => el('th', { class: c.num ? 'num' : '', text: c.label })))));
    t.append(el('tbody', {}, rows.map((r) => el('tr', { class: r._hl ? 'hl' : '' }, columns.map((c) => {
      const v = typeof c.value === 'function' ? c.value(r) : r[c.key];
      return el('td', { class: c.num ? 'num' : '' }, v != null && v.nodeType ? v : String(v ?? '–'));
    })))));
    wrap.append(t); container.append(wrap); return t;
  }

  function legend(items) {
    return el('div', { class: 'legend' }, items.map((i) => el('span', {}, [
      el('span', { class: i.line ? 'ln' + (i.dashed ? ' dashed' : '') : 'sw', style: i.line ? `border-color:${i.color}` : `background:${i.color}` }), i.label])));
  }

  async function boot() {
    wireRouter();
    try {
      const res = await fetch('data/site.json', { cache: 'no-cache' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      state.data = await res.json();
    } catch (err) {
      document.getElementById('brand-sub').textContent = 'Could not load data/site.json';
      document.getElementById('main').prepend(el('p', { class: 'note', text: 'The data bundle failed to load: ' + err.message }));
      return;
    }
    const m = state.data.meta;
    document.getElementById('brand-sub').textContent = `${m.hcps} HCPs · ${m.edges.toLocaleString()} coauthorship edges · run ${m.run_id}`;
    document.getElementById('footer-run').textContent = `Run ${m.run_id}, computed ${m.created_at_utc.slice(0, 10)}. Leiden resolution ${m.resolution}, ${m.seeds} seeds.`;
    document.querySelectorAll('[data-limits]').forEach((p) => { p.textContent = 'Scores describe academic visibility and observed 2024 industry engagement inside a publication-selected cohort. Coauthorship is not referral, and no score is evidence of prescribing influence, clinical quality, or patient volume. ' + m.description; });
    setTab(location.hash.slice(1) || TABS[0], false);
  }

  return { TABS, TIER_LABEL, TIER_CLASS, COMPOSITE_LABEL, state, css, fmt, el, h, esc, showTip, hideTip, setTab, communityColor, tierColor, seriesColor,
    svgIn, dotWhisker, hBars, lines, table, legend, boot, register: (name, fn) => { state.renderers[name] = fn; },
    onSub: (name, fn) => { (state.onSub ||= {})[name] = fn; } };
})();
