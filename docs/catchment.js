/* Catchment tab: a second graph with its own state. Centers (Layer B) sit where their core members sit on the
   coauthorship map; referral regions (Layer C) attach to centers through one of several edge layers: the
   geographic proxy, or observed CareSet shared-patient flows per year. Node positions never change when the
   layer changes. Nothing here touches the Results graph's state. */
KOL.register('catchment', async function renderCatchment(site) {
  const { el, esc, fmt, css, showTip, hideTip, communityColor, TIER_LABEL, TIER_CLASS, table } = KOL;
  const content = document.getElementById('catchment-content');
  let data;
  try {
    const res = await fetch('data/catchment.json', { cache: 'no-cache' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (err) {
    content.append(el('p', { class: 'note', text: 'The catchment bundle (data/catchment.json) failed to load: ' + err.message }));
    return;
  }
  const centers = data.centers, hrrs = data.hrrs, meta = data.meta, layers = data.layers;
  const commById = new Map(data.communities.map((c) => [c.community, c]));
  const centerByCcn = new Map(centers.map((c) => [c.ccn, c]));
  const hrrById = new Map(hrrs.map((h) => [h.hrr, h]));
  centers.forEach((c) => { c.kind = 'center'; c.key = 'c:' + c.ccn; });
  hrrs.forEach((h) => { h.kind = 'hrr'; h.key = 'h:' + h.hrr; });
  const nodes = [...centers, ...hrrs];
  const totals = meta.divisions;
  const layerKeys = Object.keys(layers);
  const observedKeys = layerKeys.filter((k) => layers[k].kind === 'observed');
  const shortLabel = (k) => layers[k].label.replace('CareSet annual ', '');

  /* ---- Layer state: which edge set is live ---- */
  const view = { layer: meta.default_layer || 'proxy', selected: null, hover: null, edges: true, stateProxy: false, showHrr: true, showNoCore: true, spread: false, labels: false, size: 'reach', hidden: new Set() };
  // Two coordinate sets ship with every node: compact (x,y) and spread (xs,ys). Neither has metric axes.
  const X = (n) => view.spread ? n.xs : n.x, Y = (n) => view.spread ? n.ys : n.y;
  let edges = [];
  const layer = () => layers[view.layer];
  const isProxy = () => layer().kind === 'proxy';
  // Every layer's edges point at their nodes from the start; only the incidence lists belong to the live layer.
  Object.values(layers).forEach((L) => L.edges.forEach((e) => { e.c = centerByCcn.get(e.ccn); e.h = hrrById.get(e.hrr); }));
  function bindLayer() {
    edges = layer().edges;
    nodes.forEach((n) => { n.inc = []; });
    edges.forEach((e, i) => { if (e.c && e.h) { e.c.inc.push(i); e.h.inc.push(i); } });
  }
  bindLayer();

  function lede() {
    const L = layer();
    document.getElementById('catchment-lede').textContent =
      `${centers.length} CAR-T center hospitals carry ${fmt.int(totals.affiliate_A)} tier-A and ${fmt.int(totals.affiliate_B)} tier-B affiliates of the core 500, ` +
      `and ${fmt.int(totals.referring_proxy)} referring physicians sit in ${hrrs.filter((h) => h.referrers).length} hospital referral regions. ` +
      'Centers with a publishing core member are filled with their members\' dominant community color and placed where those members sit on the coauthorship map (the axes carry no meaning, only neighborhoods do); centers with no core member are hollow. Size is inflow, so a large hollow circle is flow the publishing core does not touch. Squares are referral regions sized by referring physicians. ' +
      (isProxy()
        ? 'The live edge layer is a geographic proxy, not observed referrals: a region links to the adult centers inside it, or, when it has none, to the centers in its state (off by default).'
        : `The live edge layer is observed: ${L.label} shared-patient flows from referring physicians in each region to center physicians and center organizations ` +
          `(${fmt.int(L.patients)} patients over ${fmt.int(L.edge_pairs)} physician-to-center pairs; ${fmt.pct(L.same_hrr_share)} of patients stay inside the region).`);
  }
  lede();

  /* ---- Viewport (independent of the Results graph) ---- */
  const wrap = document.getElementById('catch-graph');
  const canvas = document.getElementById('catch-edges');
  const ctx = canvas.getContext('2d');
  const svg = d3.select('#catch-nodes');
  const gRoot = svg.append('g');
  const gHrr = gRoot.append('g');
  const gCenters = gRoot.append('g');
  const gLabels = gRoot.append('g');
  let W = 0, H = 0, dpr = 1, transform = d3.zoomIdentity;
  const base = { x: d3.scaleLinear(), y: d3.scaleLinear() };

  function resize() {
    W = wrap.clientWidth; H = wrap.clientHeight; dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    svg.attr('viewBox', `0 0 ${W} ${H}`);
    // Fit the anchored centers (the coauthorship frame) with headroom; outer proxy nodes stay reachable by panning.
    const pad = 30, span = Math.min(W, H) - 2 * pad;
    const anchored = centers.filter((c) => c.core > 0);
    const grow = (ext) => { const m = 0.35 * (ext[1] - ext[0] || 1); return [ext[0] - m, ext[1] + m]; };
    const xs = grow(d3.extent(anchored.length ? anchored : nodes, X)), ys = grow(d3.extent(anchored.length ? anchored : nodes, Y));
    const scale = span / Math.max(xs[1] - xs[0], ys[1] - ys[0]);
    base.x.domain(xs).range([W / 2 - scale * (xs[1] - xs[0]) / 2, W / 2 + scale * (xs[1] - xs[0]) / 2]);
    base.y.domain(ys).range([H / 2 + scale * (ys[1] - ys[0]) / 2, H / 2 - scale * (ys[1] - ys[0]) / 2]);
    nodes.forEach((n) => { n.px = base.x(X(n)); n.py = base.y(Y(n)); });
    draw();
  }
  const maxCore = d3.max(centers, (c) => c.core) || 1;
  const maxAff = d3.max(centers, (c) => c.affiliate_A + c.affiliate_B) || 1;
  const maxRef = d3.max(hrrs, (h) => h.referrers) || 1;
  const reachOf = (c) => isProxy() ? c.reach + (view.stateProxy ? c.reach_state : 0) : (c.observed[view.layer]?.patients || 0);
  const maxReach = () => d3.max(shownCenters(), reachOf) || 1;
  const sizeValue = (c) => view.size === 'core' ? c.core / maxCore : view.size === 'affiliates' ? (c.affiliate_A + c.affiliate_B) / maxAff : reachOf(c) / maxReach();
  const radius = (n) => n.kind === 'center' ? 3 + 15 * Math.sqrt(sizeValue(n)) : 2 + 9 * Math.sqrt(n.referrers / maxRef);
  const fill = (c) => c.core > 0 ? communityColor(c.community, c.tier) : 'none';
  const coreCovered = (c) => c.core > 0 ? 'core' : c.affiliate_A + c.affiliate_B > 0 ? 'affiliates' : 'none';
  // Every center is on the map; those without a core member draw hollow. Regions show only when they have a live edge.
  const centerShown = (c) => (c.core > 0 || view.showNoCore) && !view.hidden.has(c.community == null ? 'none' : c.community);
  const edgeShown = (e) => !!e && e.c && e.h && (!isProxy() || view.stateProxy || e.kind !== 'same_state') && centerShown(e.c) && view.showHrr;
  const hrrShown = (h) => view.showHrr && h.inc.some((i) => edgeShown(edges[i]));
  const shownCenters = () => centers.filter(centerShown);
  // Centers kept off the map can still receive large flows; those with no affiliate either are the blind spots.
  const noCoreLink = (c) => c.core === 0 && c.affiliate_A + c.affiliate_B === 0;
  const hiddenInflows = (n) => centers.filter((c) => c.core === 0 && reachOf(c) > 0).sort((a, b) => reachOf(b) - reachOf(a)).slice(0, n);
  const coverage = () => { const t = { core: 0, affiliates: 0, none: 0 }; centers.forEach((c) => { t[coreCovered(c)] += reachOf(c); }); const sum = t.core + t.affiliates + t.none || 1; return { ...t, sum, coreShare: t.core / sum, affShare: t.affiliates / sum, noneShare: t.none / sum }; };
  const reveal = (c) => { if (!view.showNoCore) { const t = document.getElementById('catch-toggle-nocore'); t.checked = true; t.dispatchEvent(new Event('change')); } select(c); };
  const neighbours = (n) => new Set(n.inc.map((i) => edges[i]).filter(edgeShown).map((e) => n.kind === 'center' ? e.h : e.c));
  let focusSet = null;
  const dimmed = (n) => focusSet != null && !focusSet.has(n);
  const refocus = () => { focusSet = view.selected ? new Set([view.selected, ...neighbours(view.selected)]) : null; };

  function drawEdges() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (!view.edges) return;
    ctx.translate(transform.x, transform.y); ctx.scale(transform.k, transform.k);
    const dark = matchMedia('(prefers-color-scheme: dark)').matches && document.documentElement.dataset.theme !== 'light';
    const ink = dark ? '240,239,236' : '11,11,11';
    const accent = dark ? '57,135,229' : '42,120,214';
    const k = transform.k, wmax = d3.max(edges.filter(edgeShown), (e) => e.w) || 1;
    const active = view.hover || view.selected;
    for (const e of edges) {
      if (!edgeShown(e)) continue;
      const touches = active && (e.c === active || e.h === active);
      const rel = Math.min(1, Math.sqrt(e.w / wmax));
      let alpha = active ? (touches ? 0.85 : 0.03) : 0.05 + 0.35 * rel;
      if (e.kind === 'same_state' && !touches) alpha *= 0.5;
      if (alpha < 0.01) continue;
      ctx.strokeStyle = `rgba(${e.kind === 'observed_cross_hrr' ? accent : ink},${alpha})`;
      ctx.lineWidth = (touches ? 0.8 + 3 * rel : 0.5 + 1.8 * rel) / k;
      ctx.setLineDash(e.kind === 'same_state' ? [3 / k, 3 / k] : []);
      ctx.beginPath(); ctx.moveTo(e.h.px, e.h.py); ctx.lineTo(e.c.px, e.c.py); ctx.stroke();
    }
    ctx.setLineDash([]);
  }

  function drawNodes() {
    const k = transform.k;
    gRoot.attr('transform', transform);
    const hs = hrrs.filter(hrrShown).sort((a, b) => (dimmed(a) ? 0 : 1) - (dimmed(b) ? 0 : 1) || a.referrers - b.referrers);
    gHrr.selectAll('rect.hrr').data(hs, (d) => d.key).join('rect').attr('class', 'hrr')
      .attr('x', (d) => d.px - radius(d) / Math.sqrt(k)).attr('y', (d) => d.py - radius(d) / Math.sqrt(k))
      .attr('width', (d) => 2 * radius(d) / Math.sqrt(k)).attr('height', (d) => 2 * radius(d) / Math.sqrt(k))
      .attr('rx', 1.5 / k).attr('stroke-width', 0.8 / k)
      .attr('opacity', (d) => dimmed(d) ? 0.12 : (d.has_center ? 0.85 : 0.6))
      .classed('nocenter', (d) => !d.has_center)
      .on('mousemove', (e, d) => { view.hover = d; drawEdges(); showTip(e, hrrTip(d)); })
      .on('mouseleave', () => { view.hover = null; drawEdges(); hideTip(); })
      .on('click', (e, d) => { e.stopPropagation(); select(d); });
    const cs = centers.filter(centerShown).sort((a, b) => (dimmed(a) ? 0 : 1) - (dimmed(b) ? 0 : 1) || sizeValue(a) - sizeValue(b));
    gCenters.selectAll('circle.center').data(cs, (d) => d.key).join('circle')
      .attr('class', (d) => `center ${d.core > 0 ? (TIER_CLASS[d.tier] || 'outside') : coreCovered(d) === 'affiliates' ? 'hollow affiliates' : 'hollow none'}`)
      .attr('cx', (d) => d.px).attr('cy', (d) => d.py).attr('r', (d) => radius(d) / Math.sqrt(k))
      .attr('fill', fill).attr('stroke-width', (d) => (d.core > 0 ? 1.1 : 1.6) / k)
      .attr('opacity', (d) => dimmed(d) ? 0.12 : 0.95)
      .on('mousemove', (e, d) => { view.hover = d; drawEdges(); showTip(e, centerTip(d)); })
      .on('mouseleave', () => { view.hover = null; drawEdges(); hideTip(); })
      .on('click', (e, d) => { e.stopPropagation(); select(d); });
    // Labels thin out when zoomed out so the dense academic core stays readable.
    const labelled = view.selected ? [view.selected, ...neighbours(view.selected)].filter((n) => n.kind === 'center').slice(0, 8)
      : view.labels ? cs.filter((c) => c.core >= (k >= 3 ? 1 : k >= 1.8 ? 5 : 12)) : [];
    gLabels.selectAll('text.label').data(labelled, (d) => d.key).join('text').attr('class', 'label')
      .attr('x', (d) => d.px + (radius(d) + 4) / Math.sqrt(k)).attr('y', (d) => d.py).attr('dy', '0.35em')
      .attr('font-size', `${11 / k}px`).attr('stroke-width', 3 / k).text((d) => d.dba || d.name);
  }
  function draw() { drawEdges(); drawNodes(); }

  const zoom = d3.zoom().scaleExtent([0.3, 14]).on('zoom', (e) => { transform = e.transform; draw(); });
  svg.call(zoom).on('dblclick.zoom', null);
  svg.on('click', () => { if (view.selected) select(null); });

  function zoomTo(set) {
    if (!set) { if (transform.k !== 1 || transform.x || transform.y) svg.transition().duration(600).call(zoom.transform, d3.zoomIdentity); return; }
    const pts = [...set];
    const x0 = d3.min(pts, (n) => n.px), x1 = d3.max(pts, (n) => n.px), y0 = d3.min(pts, (n) => n.py), y1 = d3.max(pts, (n) => n.py);
    const w = Math.max(x1 - x0, 60), hgt = Math.max(y1 - y0, 60);
    const k = Math.max(0.6, Math.min(6, 0.7 / Math.max(w / W, hgt / H)));
    svg.transition().duration(700).call(zoom.transform, d3.zoomIdentity.translate(W / 2 - k * (x0 + x1) / 2, H / 2 - k * (y0 + y1) / 2).scale(k));
  }

  const commLabel = (c) => c.community == null ? 'No core member at this center' : commById.get(c.community).label;
  const observedLine = (c) => observedKeys.map((k) => `${shortLabel(k)}: ${fmt.int(c.observed[k]?.patients || 0)} patients`).join(' · ');
  function centerTip(c) {
    return `<b>${esc(c.dba || c.name)}</b><br><span class="tt-muted">${esc(c.city)}, ${esc(c.state)} · CCN ${c.ccn}</span><br>` +
      `${esc(commLabel(c))}${c.community != null ? ` <span class="tt-muted">(${fmt.pct(c.community_share)} of ${c.core} core)</span>` : ''}<br>` +
      `${c.core} core · ${c.affiliate_A} tier A · ${c.affiliate_B} tier B · ${c.unlinked} unlinked${c.pediatric ? ' · pediatric' : ''}<br>` +
      `<span class="tt-muted">${c.drg018_discharges != null ? `${c.drg018_discharges} DRG 018 discharges 2024 · ` : ''}${c.fact_programs} FACT program${c.fact_programs === 1 ? '' : 's'} · proxy reach ${fmt.num(c.reach, 0)} referrers</span>` +
      (observedKeys.length ? `<br><span class="tt-muted">Observed from referrers, ${observedLine(c)}</span>` : '');
  }
  function hrrTip(h) {
    const sent = observedKeys.map((k) => `${shortLabel(k)}: ${fmt.int(d3.sum(layers[k].edges.filter((e) => e.hrr === h.hrr), (e) => e.w))}`).join(' · ');
    return `<b>${esc(h.label)}</b> <span class="tt-muted">HRR ${h.hrr}</span><br>${h.referrers} referring physicians · ${h.other} other hem/onc<br>` +
      `<span class="tt-muted">${h.office_chemo} with office chemo · ${h.part_d} with Part D lymphoma orals · ${h.has_center ? `${h.centers.length} center${h.centers.length === 1 ? '' : 's'} in region` : 'no CAR-T center in region'}</span>` +
      (h.top_groups.length ? `<br><span class="tt-muted">${h.top_groups.map((g) => `${esc(g.name)} (${g.n})`).join(' · ')}</span>` : '') +
      (observedKeys.length ? `<br><span class="tt-muted">Patients sent to centers, ${sent}</span>` : '');
  }

  /* ---- Layer control, chips ---- */
  const layerSeg = document.getElementById('catch-layer');
  layerSeg.innerHTML = '';
  layerKeys.forEach((key) => layerSeg.append(el('button', { class: 'btn', 'data-layer': key, 'aria-pressed': String(key === view.layer),
    title: layers[key].kind === 'proxy' ? 'Regions linked to the centers inside them' : `${layers[key].label}: observed shared-patient flows`,
    onclick: () => setLayer(key), text: layers[key].kind === 'proxy' ? 'Edges: proxy' : layers[key].label.replace('CareSet annual', 'CareSet') })));
  function setLayer(key) {
    if (!layers[key]) return;
    view.layer = key;
    layerSeg.querySelectorAll('.btn').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.layer === key)));
    document.getElementById('catch-toggle-state').closest('label').hidden = !isProxy();
    bindLayer(); refocus(); lede(); renderLegend(); renderSide(view.selected); renderTables(); draw();
  }

  const chips = document.getElementById('catch-chips');
  chips.innerHTML = '';
  const allChip = el('button', { class: 'chip', onclick: () => { view.hidden.clear(); syncChips(); refocus(); draw(); } }, ['Show all']);
  chips.append(allChip);
  const present = new Set(centers.map((c) => c.community == null ? 'none' : c.community));
  data.communities.filter((c) => present.has(c.community) && c.tier !== 'outside main network').forEach((c) => {
    const n = centers.filter((x) => x.community === c.community).length;
    chips.append(el('button', { class: 'chip', 'data-key': c.community, 'aria-pressed': 'false', onclick: () => toggleHidden(c.community),
      title: 'Click to remove this community\'s centers from the map; click again to restore' },
      [el('span', { class: 'dot', style: `background:${communityColor(c.community, c.tier)}` }), c.label.replace(/^Loose /, '').replace(/ \(.*\)$/, ''), el('span', { class: 'n', text: n })]));
  });
  function toggleHidden(key) {
    if (view.hidden.has(key)) view.hidden.delete(key); else view.hidden.add(key);
    if (view.selected && view.selected.kind === 'center' && !centerShown(view.selected)) select(null, true);
    syncChips(); refocus(); draw();
  }
  function syncChips() {
    chips.querySelectorAll('.chip[data-key]').forEach((b) => {
      const key = b.dataset.key === 'none' ? 'none' : +b.dataset.key;
      const off = view.hidden.has(key);
      b.classList.toggle('off', off); b.setAttribute('aria-pressed', String(off));
    });
    allChip.hidden = view.hidden.size === 0;
  }
  syncChips();

  /* ---- Side card ---- */
  const side = document.getElementById('catch-side');
  const link = (text, fn) => el('button', { onclick: fn, text });
  const unit = () => isProxy() ? 'referrers' : 'patients';
  const edgeNote = (e) => e.kind === 'same_state' ? ' (state proxy)' : e.kind === 'observed_cross_hrr' ? ' (other region)' : '';
  function renderSide(n) {
    side.innerHTML = '';
    if (!n) {
      const top = shownCenters().sort((a, b) => reachOf(b) - reachOf(a)).slice(0, 6);
      const L = layer();
      side.append(el('div', { class: 'card' }, [
        el('div', { class: 'kicker', text: isProxy() ? 'Geographic proxy' : L.label }),
        el('p', { html: 'Click a center for its core members, affiliates, and the regions that feed it. Click a square for the referring physicians in that region. Chips remove a community\'s centers; the layer control switches between the proxy and observed flows without moving any node.' }),
        el('dl', {}, [
          el('dt', { text: 'Centers on the map' }), el('dd', { text: `${shownCenters().length} of ${centers.length}` }),
          el('dt', { text: 'With a core member' }), el('dd', { text: centers.filter((c) => c.core).length }),
          el('dt', { text: 'Regions on the map' }), el('dd', { text: `${hrrs.filter(hrrShown).length} of ${hrrs.filter((h) => h.referrers).length}` }),
          el('dt', { text: 'Regions with a center' }), el('dd', { text: hrrs.filter((h) => h.referrers && h.has_center).length }),
          ...(isProxy() ? [el('dt', { text: 'Referrers unassigned' }), el('dd', { text: meta.referrers_unassigned })]
            : [el('dt', { text: 'Region-to-center edges' }), el('dd', { text: fmt.int(L.edges.length) }),
               el('dt', { text: 'Patients in flows' }), el('dd', { text: fmt.int(L.patients) }),
               el('dt', { text: 'Stay in own region' }), el('dd', { text: fmt.pct(L.same_hrr_share) })]),
        ]),
        (() => { const cv = coverage(); return el('div', { class: 'coverage' }, [
          el('div', { class: 'kicker', text: `Where the ${isProxy() ? 'referrers' : 'patients'} land` }),
          el('div', { class: 'bar' }, [el('span', { class: 'core', style: `width:${100 * cv.coreShare}%`, title: 'centers with a publishing core member' }), el('span', { class: 'aff', style: `width:${100 * cv.affShare}%`, title: 'centers with affiliates only' }), el('span', { class: 'none', style: `width:${100 * cv.noneShare}%`, title: 'centers with no link to the core network' })]),
          el('p', { class: 'small', html: `<strong>${fmt.pct(cv.coreShare)}</strong> at centers with a publishing core member · ${fmt.pct(cv.affShare)} affiliates only · ${fmt.pct(cv.noneShare)} no link` }),
        ]); })(),
        el('p', { class: 'small muted', text: `Largest ${isProxy() ? 'proxy reach (referrers in catchment)' : 'inflow (patients from referring physicians)'}:` }),
        el('ol', { class: 'leader-list' }, top.map((c) => el('li', {}, [link(c.dba || c.name, () => select(c)), el('span', { class: 'muted', text: ` · ${fmt.int(reachOf(c))}` })]))),
        ...(!view.showNoCore ? [] : [
          el('p', { class: 'small muted', text: `Large ${isProxy() ? 'proxy reach' : 'inflow'} at hollow centers (no core member); bold ones have no affiliate link either:` }),
          el('ol', { class: 'leader-list' }, hiddenInflows(6).map((c) => el('li', { class: noCoreLink(c) ? 'warn' : '' }, [
            link(c.dba || c.name, () => reveal(c)), el('span', { class: 'muted', text: ` · ${fmt.int(reachOf(c))}${noCoreLink(c) ? ' · no core or affiliate link' : ` · ${fmt.int(c.affiliate_A + c.affiliate_B)} affiliates`}` })]))),
        ]),
      ]));
      return;
    }
    if (n.kind === 'center') {
      const feeders = n.inc.map((i) => edges[i]).filter(edgeShown).sort((a, b) => b.w - a.w).slice(0, 8);
      const comm = n.community != null ? commById.get(n.community) : null;
      const key = view.layer;
      side.append(el('div', { class: 'card', style: `border-top: 4px solid ${n.core > 0 ? fill(n) : css('--c-outside')}` }, [
        el('div', { class: 'row' }, [comm ? el('span', { class: `badge ${TIER_CLASS[comm.tier]}`, text: TIER_LABEL[comm.tier] }) : el('span', { class: 'badge outside', text: 'no core' }),
          el('span', { class: 'muted small', text: n.evidence.replace('drg018_2024', 'DRG 018').replace('fact_iec_accredited', 'FACT IEC').replace(';', ' · ') })]),
        el('h3', { text: n.dba || n.name }),
        el('p', { class: 'small muted', text: `${n.name} · ${n.city}, ${n.state} · CCN ${n.ccn}` }),
        el('dl', {}, [
          el('dt', { text: 'Community' }), el('dd', {}, comm ? [link(comm.label, () => KOL.setTab(`results/${n.community}`)), ` (${fmt.pct(n.community_share)})`] : '–'),
          el('dt', { text: 'Core members' }), el('dd', { text: n.core }),
          el('dt', { text: 'Affiliates A / B' }), el('dd', { text: `${fmt.int(n.affiliate_A)} / ${fmt.int(n.affiliate_B)}` }),
          el('dt', { text: 'Unlinked center staff' }), el('dd', { text: fmt.int(n.unlinked) }),
          el('dt', { text: 'DRG 018 discharges' }), el('dd', { text: n.drg018_discharges != null ? n.drg018_discharges : 'suppressed or none' }),
          el('dt', { text: 'FACT IEC programs' }), el('dd', { text: n.fact_programs }),
          el('dt', { text: 'Proxy reach' }), el('dd', { text: `${fmt.num(n.reach, 0)} referrers in region${n.reach_state ? ` · ${fmt.num(n.reach_state, 0)} via state fallback` : ''}` }),
          ...observedKeys.flatMap((k) => [el('dt', { text: layers[k].label.replace('CareSet annual', 'Observed') }),
            el('dd', { text: `${fmt.int(n.observed[k]?.patients || 0)} patients from ${fmt.int(n.observed[k]?.referrer_pairs || 0)} referrer links` })]),
          n.pediatric ? el('dt', { text: 'Pediatric' }) : null, n.pediatric ? el('dd', { text: 'excluded as a proxy referral target' }) : null,
        ]),
        n.leaders.length ? el('p', { class: 'small muted', text: 'Core members by PageRank' + (!isProxy() ? ` (patients received from referrers, ${shortLabel(key)})` : '') + ':' }) : null,
        n.leaders.length ? el('ol', { class: 'leader-list' }, n.leaders.map((l) => el('li', {}, [link(l.name, () => KOL.setTab(`results/${l.community}`)),
          el('span', { class: 'muted', text: ` · ${fmt.num(l.pagerank * 1000, 1)}‰${!isProxy() && l.inflow?.[key] ? ` · ${fmt.int(l.inflow[key].in_patients_from_referrers)} pts / ${l.inflow[key].referrer_sources} referrers` : ''}` })]))) : null,
        feeders.length ? el('p', { class: 'small muted', text: isProxy() ? 'Referral regions in its catchment:' : 'Regions sending patients:' }) : null,
        feeders.length ? el('ul', { class: 'leader-list' }, feeders.map((e) => el('li', {}, [link(e.h.label, () => select(e.h)), el('span', { class: 'muted', text: ` · ${fmt.int(e.w)} ${unit()}${edgeNote(e)}` })]))) : null,
      ]));
      return;
    }
    const outs = n.inc.map((i) => edges[i]).filter(edgeShown).sort((a, b) => b.w - a.w).slice(0, 8);
    side.append(el('div', { class: 'card', style: `border-top: 4px solid ${css('--ink-2')}` }, [
      el('div', { class: 'kicker', text: `Hospital referral region ${n.hrr}` }),
      el('h3', { text: n.label }),
      el('dl', {}, [
        el('dt', { text: 'Referring physicians' }), el('dd', { text: n.referrers }),
        el('dt', { text: 'Other hem/onc' }), el('dd', { text: n.other }),
        el('dt', { text: 'Office chemo marker' }), el('dd', { text: n.office_chemo }),
        el('dt', { text: 'Part D lymphoma orals' }), el('dd', { text: n.part_d }),
        el('dt', { text: 'Centers in region' }), el('dd', {}, n.centers.length ? n.centers.map((ccn, i) => { const c = centerByCcn.get(ccn); return el('span', {}, [i ? ', ' : '', centerShown(c) ? link(c.dba || c.name, () => select(c)) : el('span', { class: 'muted', text: `${c.dba || c.name} (no core member)` })]); }) : 'none'),
      ]),
      n.top_groups.length ? el('p', { class: 'small muted', text: 'Largest referrer groups:' }) : null,
      n.top_groups.length ? el('ul', { class: 'leader-list' }, n.top_groups.map((g) => el('li', { text: `${g.name} · ${g.n}` }))) : null,
      outs.length ? el('p', { class: 'small muted', text: isProxy() ? 'Linked centers:' : 'Centers receiving its patients:' }) : null,
      outs.length ? el('ul', { class: 'leader-list' }, outs.map((e) => el('li', {}, [link(e.c.dba || e.c.name, () => select(e.c)), el('span', { class: 'muted', text: ` · ${fmt.int(e.w)} ${unit()}${edgeNote(e)}` })]))) : null,
    ]));
  }

  function select(n, silent) {
    view.selected = n || null;
    refocus();
    if (!silent) history.replaceState(null, '', n ? `#catchment/${n.key}` : '#catchment');
    renderSide(n);
    renderTimeline();
    zoomTo(focusSet);
    draw();
  }

  /* ---- Legend, toggles ---- */
  const legendItem = (inner, label, size = 14) => el('span', { html: `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${inner}</svg> ${label}` });
  const legendRow = document.getElementById('catch-legend');
  function renderLegend() {
    legendRow.innerHTML = '';
    legendRow.append(
      legendItem(`<circle cx="7" cy="7" r="6" fill="${css('--c1')}" stroke="${css('--ink')}" stroke-width="1.2"/>`, 'Filled: center with publishing core members, colored by their community'),
      ...(view.showNoCore ? [legendItem(`<circle cx="7" cy="7" r="6" fill="none" stroke="${css('--ink-2')}" stroke-width="1.6"/>`, 'Hollow: center with no publishing core member (dashed when no affiliates either)')] : []),
      legendItem(`<rect x="2" y="2" width="10" height="10" rx="1.5" fill="${css('--ink-2')}" opacity=".85"/>`, 'Referral region with a center'),
      legendItem(`<rect x="2" y="2" width="10" height="10" rx="1.5" fill="none" stroke="${css('--ink-2')}" stroke-width="1.2"/>`, 'Region without a center'),
      el('span', { text: isProxy() ? 'Edge width: referring physicians (proxy). Dashed: same-state fallback.' : 'Edge width: shared patients (CareSet, square-root scale). Blue: flow to a center in another region.' }),
    );
  }
  renderLegend();
  document.getElementById('catch-toggle-edges').addEventListener('change', (e) => { view.edges = e.target.checked; drawEdges(); });
  document.getElementById('catch-toggle-state').addEventListener('change', (e) => { view.stateProxy = e.target.checked; refocus(); renderSide(view.selected); draw(); });
  document.getElementById('catch-toggle-hrr').addEventListener('change', (e) => { view.showHrr = e.target.checked; refocus(); draw(); });
  document.getElementById('catch-toggle-nocore').addEventListener('change', (e) => { view.showNoCore = e.target.checked; if (view.selected && view.selected.kind === 'center' && !centerShown(view.selected)) select(null, true); refocus(); renderLegend(); renderSide(view.selected); renderTables(); draw(); });
  document.getElementById('catch-toggle-labels').addEventListener('change', (e) => { view.labels = e.target.checked; drawNodes(); });
  document.getElementById('catch-toggle-spread').addEventListener('change', (e) => { view.spread = e.target.checked; resize(); if (view.selected) zoomTo(focusSet); });
  document.querySelectorAll('#catch-size .btn').forEach((b) => b.addEventListener('click', () => {
    view.size = b.dataset.size; document.querySelectorAll('#catch-size .btn').forEach((x) => x.setAttribute('aria-pressed', String(x === b))); drawNodes();
  }));
  document.getElementById('catch-reset').addEventListener('click', () => select(null));
  document.getElementById('catch-toggle-state').closest('label').hidden = !isProxy();
  new ResizeObserver(() => resize()).observe(wrap);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);

  /* ---- Time progression: annual CareSet flows for the selection, with regulatory milestones ---- */
  const years = observedKeys.map((k) => layers[k].year).sort((a, b) => a - b);
  const keyOfYear = (y) => observedKeys.find((k) => layers[k].year === y);
  const milestones = (meta.milestones || []).map((m) => { const [y, mo] = m.date.split('-').map(Number); return { ...m, t: y + (mo - 0.5) / 12 }; });
  function timeline(container, { title, sub, series, yFmt = fmt.int, width = 600, height = 310 }) {
    const right = series.some((s) => s.axis === 'right');
    // Milestone labels take the lowest row where they do not overlap an earlier label; the top margin grows with the rows used.
    const shown = milestones.filter((m) => m.t >= years[0] && m.t <= years[years.length - 1] + 1);
    const xTmp = d3.scaleLinear().domain([years[0], years[years.length - 1] + 1]).range([54, width - (right ? 52 : 16)]);
    const rowsEnd = [];
    shown.forEach((m) => { const x0 = xTmp(m.t) + 3, x1 = x0 + 5.6 * m.label.length; let r = rowsEnd.findIndex((end) => end < x0 - 4); if (r < 0) { r = rowsEnd.length; rowsEnd.push(0); } rowsEnd[r] = x1; m.row = r; });
    const margin = { t: 46 + 11 * Math.max(1, rowsEnd.length), r: right ? 52 : 16, b: 34, l: 54 };
    const svg = KOL.svgIn(container, width, height);
    const x = d3.scaleLinear().domain([years[0], years[years.length - 1] + 1]).range([margin.l, width - margin.r]);
    const scaleFor = (list) => d3.scaleLinear().domain([0, d3.max(list.flatMap((s) => s.points.map((p) => p[1]))) || 1]).nice().range([height - margin.b, margin.t]);
    const y = scaleFor(series.filter((s) => s.axis !== 'right'));
    const yR = right ? scaleFor(series.filter((s) => s.axis === 'right')) : null;
    if (title) svg.append('text').attr('class', 'title').attr('x', margin.l).attr('y', 14).text(title);
    if (sub) svg.append('text').attr('class', 'sub').attr('x', margin.l).attr('y', 28).text(sub);
    svg.append('g').attr('class', 'grid').attr('transform', `translate(${margin.l},0)`).call(d3.axisLeft(y).ticks(4).tickSize(-(width - margin.l - margin.r)).tickFormat(''));
    svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${height - margin.b})`).call(d3.axisBottom(x).tickValues(years.map((v) => v + 0.5)).tickFormat((v) => String(Math.floor(v))));
    svg.append('g').attr('class', 'axis').attr('transform', `translate(${margin.l},0)`).call(d3.axisLeft(y).ticks(4).tickFormat((v) => yFmt(v)));
    if (right) svg.append('g').attr('class', 'axis').attr('transform', `translate(${width - margin.r},0)`).call(d3.axisRight(yR).ticks(4).tickFormat((v) => fmt.int(v)));
    // Milestone tickers: dashed verticals at month resolution, labels staggered on three rows above the plot.
    const gm = svg.append('g').attr('class', 'milestones');
    shown.forEach((m) => {
      const g = gm.append('g').attr('transform', `translate(${x(m.t)},0)`);
      g.append('line').attr('class', 'tick-line').attr('y1', margin.t - 8 - m.row * 11).attr('y2', height - margin.b);
      g.append('text').attr('class', 'tick-label').attr('x', 3).attr('y', margin.t - 11 - m.row * 11).text(m.label);
      g.append('rect').attr('x', -6).attr('y', 36).attr('width', 12).attr('height', height - margin.b - 36).attr('fill', 'transparent')
        .on('mousemove', (e) => showTip(e, `<b>${esc(m.label)}</b><br>${esc(m.detail)}`)).on('mouseleave', hideTip);
    });
    series.forEach((s) => {
      const ys = s.axis === 'right' ? yR : y, f = s.axis === 'right' ? fmt.int : yFmt;
      const line = d3.line().x((p) => x(p[0] + 0.5)).y((p) => ys(p[1]));
      svg.append('path').attr('d', line(s.points)).attr('fill', 'none').attr('stroke', s.color).attr('stroke-width', 2.2).attr('stroke-dasharray', s.dashed ? '5 4' : null);
      svg.append('g').selectAll('circle').data(s.points).join('circle').attr('cx', (p) => x(p[0] + 0.5)).attr('cy', (p) => ys(p[1])).attr('r', 3.5).attr('fill', s.color).attr('stroke', css('--surface')).attr('stroke-width', 1.5)
        .on('mousemove', (e, p) => showTip(e, `<b>${esc(s.name)}</b><br>${p[0]}: ${f(p[1])}${s.axis === 'right' ? ' (right axis)' : ''}`)).on('mouseleave', hideTip);
    });
    container.append(KOL.legend(series.map((s) => ({ color: s.color, label: s.name, line: true, dashed: s.dashed }))));
    return svg;
  }
  const timelineSection = el('section', { id: 'catch-timeline' });
  function renderTimeline() {
    timelineSection.innerHTML = '';
    if (years.length < 2) return;
    const n = view.selected;
    const span = `${years[0]}–${years[years.length - 1]}`;
    const heading = !n ? 'How the flow to CAR-T centers changed over time'
      : n.kind === 'center' ? `How the flow to ${n.dba || n.name} changed over time` : `How the flow out of ${n.label} changed over time`;
    timelineSection.append(el('h2', { text: heading }));
    timelineSection.append(el('p', { class: 'lede', text: `CareSet DocGraph annual shared-patient flows, ${span}, from referring physicians (as labeled on 2024 markers) to center physicians and center organizations. Dashed verticals are CAR-T regulatory milestones; hover one for the detail. ` + (n ? 'Click empty map space to return to the all-center view.' : 'Select a center or region on the map to see its own series.') }));
    const grid = el('div', { class: 'grid-2' }); timelineSection.append(grid);
    const c1 = el('div', { class: 'card' }), c2 = el('div', { class: 'card' }); grid.append(c1, c2);
    const accent = css('--accent'), warn = css('--warning'), serious = css('--serious'), ink2 = css('--ink-2');
    if (!n) {
      const by = (group) => years.map((yv) => [yv, d3.sum(centers.filter((c) => coreCovered(c) === group), (c) => c.observed[keyOfYear(yv)]?.patients || 0)]);
      timeline(c1, { title: 'Patients from referring physicians, by where they land', sub: 'per year', series: [
        { name: 'Centers with a publishing core member', color: accent, points: by('core') },
        { name: 'Affiliates only', color: warn, points: by('affiliates') },
        { name: 'No link to the core network', color: serious, points: by('none') }] });
      timeline(c2, { title: 'Breadth of the referral base', sub: 'per year', series: [
        { name: 'Referring physician to center links', color: ink2, points: years.map((yv) => [yv, layers[keyOfYear(yv)].edge_pairs || 0]) },
        { name: 'Regions sending patients (right axis)', color: accent, dashed: true, axis: 'right', points: years.map((yv) => [yv, new Set(layers[keyOfYear(yv)].edges.map((e) => e.hrr)).size]) }] });
      return;
    }
    if (n.kind === 'center') {
      timeline(c1, { title: 'Patients received from referring physicians', sub: n.core ? `${n.core} publishing core members at this center` : 'no publishing core member at this center', series: [
        { name: 'Patients', color: n.core ? fill(n) : ink2, points: years.map((yv) => [yv, n.observed[keyOfYear(yv)]?.patients || 0]) }] });
      timeline(c2, { title: 'Breadth of its referral base', sub: 'per year', series: [
        { name: 'Referring physician links', color: ink2, points: years.map((yv) => [yv, n.observed[keyOfYear(yv)]?.referrer_pairs || 0]) },
        { name: 'Regions sending patients (right axis)', color: accent, dashed: true, axis: 'right', points: years.map((yv) => [yv, layers[keyOfYear(yv)].edges.filter((e) => e.ccn === n.ccn).length]) }] });
      return;
    }
    const sent = (yv, pred) => d3.sum(layers[keyOfYear(yv)].edges.filter((e) => e.hrr === n.hrr && pred(e)), (e) => e.w);
    timeline(c1, { title: 'Patients sent to CAR-T centers', sub: 'by where they land', series: [
      { name: 'Centers with a publishing core member', color: accent, points: years.map((yv) => [yv, sent(yv, (e) => e.c && coreCovered(e.c) === 'core')]) },
      { name: 'Other centers', color: serious, points: years.map((yv) => [yv, sent(yv, (e) => e.c && coreCovered(e.c) !== 'core')]) }] });
    timeline(c2, { title: 'Where the patients go', sub: 'per year', series: [
      { name: 'Patients staying in this region', color: accent, points: years.map((yv) => [yv, sent(yv, (e) => e.kind === 'observed_same_hrr')]) },
      { name: 'Centers receiving (right axis)', color: ink2, dashed: true, axis: 'right', points: years.map((yv) => [yv, layers[keyOfYear(yv)].edges.filter((e) => e.hrr === n.hrr).length]) }] });
  }

  /* ---- Tables under the graph (rebuilt when the layer changes) ---- */
  function spearman(pairs) {
    const rank = (vals) => {
      const idx = vals.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]); const r = new Array(vals.length); let i = 0;
      while (i < idx.length) { let j = i; while (j + 1 < idx.length && idx[j + 1][0] === idx[i][0]) j++; const avg = (i + j) / 2 + 1; for (let k = i; k <= j; k++) r[idx[k][1]] = avg; i = j + 1; }
      return r;
    };
    const a = rank(pairs.map((p) => p[0])), b = rank(pairs.map((p) => p[1])), n = a.length;
    const ma = d3.mean(a), mb = d3.mean(b);
    const cov = d3.sum(a.map((x, i) => (x - ma) * (b[i] - mb))), va = d3.sum(a.map((x) => (x - ma) ** 2)), vb = d3.sum(b.map((x) => (x - mb) ** 2));
    return n > 2 && va && vb ? cov / Math.sqrt(va * vb) : null;
  }
  function renderTables() {
    content.innerHTML = '';
    content.append(timelineSection);
    renderTimeline();
    const sec = (title, sub) => { const s = el('section', {}); s.append(el('h2', { text: title })); if (sub) s.append(el('p', { class: 'lede', text: sub })); content.append(s); return s; };
    const L = layer();
    const byComm = data.communities.filter((c) => c.tier !== 'outside main network').map((c) => {
      const cs = centers.filter((x) => x.community === c.community && centerShown(x));
      const hrrSet = new Set(cs.map((x) => x.hrr).filter(Boolean));
      return { c, centers: cs.length, core: c.size, a: d3.sum(cs, (x) => x.affiliate_A), b: d3.sum(cs, (x) => x.affiliate_B),
        referrers: d3.sum(hrrs.filter((h) => hrrSet.has(h.hrr)), (h) => h.referrers), reach: d3.sum(cs, reachOf) };
    }).sort((x, y) => y.reach - x.reach);
    const s1 = sec('How each community extends into its institutions and catchment',
      'Centers are assigned to the community of most of their core members. Affiliates are counted at those centers; referrers are counted in the referral regions that contain them. A physician can be counted under two communities when two centers share a region.');
    table(s1, byComm, [
      { label: 'Community', value: (r) => el('span', { style: `border-left: 4px solid ${communityColor(r.c.community, r.c.tier)}; padding-left: .5rem`, text: r.c.label }) },
      { label: 'Core', key: 'core', num: true }, { label: 'Centers', key: 'centers', num: true },
      { label: 'Affiliates A', value: (r) => fmt.int(r.a), num: true }, { label: 'Affiliates B', value: (r) => fmt.int(r.b), num: true },
      { label: 'Referrers in same regions', value: (r) => fmt.int(r.referrers), num: true },
      { label: isProxy() ? 'Proxy reach' : `Patients received (${shortLabel(view.layer)})`, value: (r) => fmt.int(r.reach), num: true },
    ]);
    const sentBy = (h) => d3.sum(edges.filter((e) => e.hrr === h.hrr), (e) => e.w);
    const s2 = sec('Referral regions with the most referring physicians',
      isProxy() ? 'Regions without a CAR-T center are where the proxy says the least: their physicians are linked only by the same-state fallback, which is off by default on the map.'
                : 'Patients sent is the observed flow from referring physicians in the region to center physicians or center organizations anywhere, so a region without a center can still send patients.');
    table(s2, hrrs.slice().sort((a, b) => b.referrers - a.referrers).slice(0, 15), [
      { label: 'Region', value: (r) => el('button', { class: 'linklike', onclick: () => { select(r); document.querySelector('#panel-catchment .graph-wrap').scrollIntoView({ behavior: 'smooth' }); }, text: r.label }) },
      { label: 'Referrers', key: 'referrers', num: true }, { label: 'Other hem/onc', key: 'other', num: true },
      { label: 'Centers in region', value: (r) => r.centers.length ? r.centers.map((ccn) => { const c = centerByCcn.get(ccn); return (c.dba || c.name) + (c.core ? '' : ' (no core member)'); }).join('; ') : '—' },
      ...(isProxy() ? [] : [{ label: 'Patients sent', value: (r) => fmt.int(sentBy(r)), num: true }]),
      { label: 'Largest referrer group', value: (r) => r.top_groups[0] ? `${r.top_groups[0].name} (${r.top_groups[0].n})` : '—' },
    ]);
    const s24 = sec('How much of the flow does the publishing core touch?',
      'For each edge layer, the share of flow that lands at centers with a publishing core member, at centers reached only through affiliates, and at centers with no link to the core network.');
    const saved = view.layer;
    const covRows = layerKeys.map((k) => { view.layer = k; const cv = coverage(); view.layer = saved; return { layer: layers[k].label, unit: layers[k].unit, ...cv }; });
    table(s24, covRows, [
      { label: 'Layer', key: 'layer' }, { label: 'Total', value: (r) => `${fmt.int(r.sum)} ${r.unit}`, num: true },
      { label: 'Publishing core present', value: (r) => `${fmt.int(r.core)} (${fmt.pct(r.coreShare)})`, num: true },
      { label: 'Affiliates only', value: (r) => `${fmt.int(r.affiliates)} (${fmt.pct(r.affShare)})`, num: true },
      { label: 'No link', value: (r) => `${fmt.int(r.none)} (${fmt.pct(r.noneShare)})`, num: true },
    ]);
    const blind = hiddenInflows(12);
    if (blind.length) {
      const s25 = sec('Large inflows at centers with no publishing core member',
        `Hollow circles on the map: no core HCP is affiliated with these centers, yet referring physicians send them ${isProxy() ? 'proxy reach' : 'patients'}. Highlighted rows have no tier A or B affiliate either: the network as built has no path to them.`);
      table(s25, blind.map((c) => ({ ...c, _hl: noCoreLink(c) })), [
        { label: 'Center', value: (r) => el('button', { class: 'linklike', onclick: () => { reveal(r); document.querySelector('#panel-catchment .graph-wrap').scrollIntoView({ behavior: 'smooth' }); }, text: r.dba || r.name }) },
        { label: 'Location', value: (r) => `${r.city}, ${r.state}` },
        { label: isProxy() ? 'Proxy reach' : `Patients received (${shortLabel(view.layer)})`, value: (r) => fmt.int(reachOf(r)), num: true },
        ...(isProxy() ? [] : [{ label: 'Referrer links', value: (r) => fmt.int(r.observed[view.layer]?.referrer_pairs || 0), num: true }]),
        { label: 'Affiliates A / B', value: (r) => `${fmt.int(r.affiliate_A)} / ${fmt.int(r.affiliate_B)}`, num: true },
        { label: 'Unlinked center staff', value: (r) => fmt.int(r.unlinked), num: true },
        { label: 'Evidence', value: (r) => r.evidence.replace('drg018_2024', 'DRG 018').replace('fact_iec_accredited', 'FACT IEC').replace(';', ', ') + (r.pediatric ? ', pediatric' : '') },
        { label: 'Link to the core network', value: (r) => noCoreLink(r) ? el('strong', { text: 'none' }) : 'affiliates only' },
      ]);
    }
    if (observedKeys.length) {
      const s3 = sec('Does the geographic proxy agree with observed flows?',
        'For each observed year: the share of patients that stay inside the referring physician\'s own referral region, and the rank correlation between each core-anchored adult center\'s proxy reach and its observed inflow.');
      const rows = observedKeys.map((k) => {
        const pairs = centers.filter((c) => !c.pediatric && c.core > 0).map((c) => [c.reach, c.observed[k]?.patients || 0]);
        return { layer: layers[k].label, patients: layers[k].patients, pairs: layers[k].edge_pairs, same: layers[k].same_hrr_share, rho: spearman(pairs),
          sending: new Set(layers[k].edges.map((e) => e.hrr)).size, receiving: new Set(layers[k].edges.map((e) => e.ccn)).size };
      });
      table(s3, rows, [
        { label: 'Layer', key: 'layer' }, { label: 'Patients', value: (r) => fmt.int(r.patients), num: true }, { label: 'Referrer-center pairs', value: (r) => fmt.int(r.pairs), num: true },
        { label: 'Regions sending', key: 'sending', num: true }, { label: 'Centers receiving', key: 'receiving', num: true },
        { label: 'Stay in own region', value: (r) => fmt.pct(r.same), num: true }, { label: 'Spearman ρ, proxy reach vs inflow', value: (r) => fmt.num(r.rho, 2), num: true },
      ]);
    }
    const s4 = sec('What this layer is and is not');
    s4.append(el('div', { class: 'card' }, [
      el('p', { html: '<strong>Layer B (affiliation)</strong> counts clinicians who share a center hospital and a PECOS group with a core member (tier A) or one of the two (tier B). It comes from CMS Doctors and Clinicians files refreshed 2026-10-15 and is enrollment and claims evidence, not employment records.' }),
      el('p', { html: '<strong>Layer C, proxy.</strong> A referring physician is linked to the adult centers in their hospital referral region. No patient is observed; the edge weight is a head count.' }),
      ...(observedKeys.length ? [el('p', { html: `<strong>Layer C, observed.</strong> CareSet DocGraph Hop Teaming (${observedKeys.map((k) => layers[k].year).join(', ')}): Medicare fee-for-service patients seen by a referring physician and then by a center physician or center organization within the year, pairs under 11 patients suppressed. An edge is shared patients of any kind, not a lymphoma referral; the published feature table keeps wait-time statistics to separate co-management from referral. These years precede the 2024 markers and center set and the 2025 removal of CAR-T REMS requirements.` })] : []),
      el('p', { class: 'small muted', text: `Division snapshot ${meta.division_snapshot}; ${fmt.int(totals.referring_proxy)} referring proxies, ${fmt.int(totals.affiliate_A)} tier A, ${fmt.int(totals.affiliate_B)} tier B, ${fmt.int(totals.center_affiliate_unlinked)} unlinked center staff, ${fmt.int(totals.hemonc_other)} other hem/onc. Built ${meta.built_at_utc.slice(0, 10)} from site run ${meta.site_run_id}.` + (observedKeys.length ? ' CareSet data: CC BY-NC-SA 4.0, attribution to CareSet Systems, Inc.' : '') }),
    ]));
  }
  renderTables();

  resize();
  renderSide(null);
  const applySub = (sub) => {
    if (!sub) { if (view.selected) select(null, true); return; }
    const n = sub.startsWith('c:') ? centerByCcn.get(sub.slice(2)) : sub.startsWith('h:') ? hrrById.get(sub.slice(2)) : null;
    if (n) select(n, true);
  };
  KOL.onSub('catchment', applySub);
  if (KOL.state.sub) setTimeout(() => applySub(KOL.state.sub), 50);
});
