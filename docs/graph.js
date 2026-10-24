/* Results tab: zoomable coauthorship map (canvas edges, SVG nodes), community selector, tier sections. */
KOL.register('results', function renderResults(data) {
  const { el, esc, fmt, css, showTip, hideTip, communityColor, tierColor, TIER_LABEL, TIER_CLASS, COMPOSITE_LABEL } = KOL;
  const nodes = data.nodes, edges = data.edges, communities = data.communities;
  const byId = new Map(nodes.map((n, i) => [n.id, i]));
  const commById = new Map(communities.map((c) => [c.community, c]));
  const incident = nodes.map(() => []);
  edges.forEach((e, i) => { incident[e.s].push(i); incident[e.t].push(i); });
  const maxPr = d3.max(nodes, (n) => n.pagerank);
  const summaries = data.meta.summaries;

  document.getElementById('results-lede').textContent =
    `Leiden finds ${communities.length} coauthorship communities among ${nodes.length} HCPs. ` +
    `${communities.filter((c) => c.tier === 'firm').length} are firm enough to name, ${communities.filter((c) => c.tier === 'provisional').length} are provisional, ` +
    `${communities.filter((c) => c.tier === 'unstable').length} is a loose grouping, and ${communities.filter((c) => c.tier === 'outside main network').length} tiny groups sit outside the main network. ` +
    'Node size is PageRank; fill is community; the stroke pattern is the community\'s stability tier; a ring marks an HCP whose membership splits across runs.';

  /* ---- Viewport ---- */
  const wrap = document.getElementById('graph');
  const canvas = document.getElementById('graph-edges');
  const ctx = canvas.getContext('2d');
  const svg = d3.select('#graph-nodes');
  const gRoot = svg.append('g');
  const gRings = gRoot.append('g');
  const gNodes = gRoot.append('g');
  const gLabels = gRoot.append('g');
  let W = 0, H = 0, dpr = 1, transform = d3.zoomIdentity;
  const view = { selected: null, hover: null, edges: true, moversOnly: false, byTier: false, labels: false };

  // Data coordinates are in [-1, 1]; fit into the viewport with a margin.
  const base = { x: d3.scaleLinear(), y: d3.scaleLinear() };
  function resize() {
    W = wrap.clientWidth; H = wrap.clientHeight; dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    svg.attr('viewBox', `0 0 ${W} ${H}`);
    const pad = 28, span = Math.min(W, H) - 2 * pad;
    const xs = d3.extent(nodes, (n) => n.x), ys = d3.extent(nodes, (n) => n.y);
    const scale = span / Math.max(xs[1] - xs[0], ys[1] - ys[0]);
    base.x.domain(xs).range([W / 2 - scale * (xs[1] - xs[0]) / 2, W / 2 + scale * (xs[1] - xs[0]) / 2]);
    base.y.domain(ys).range([H / 2 + scale * (ys[1] - ys[0]) / 2, H / 2 - scale * (ys[1] - ys[0]) / 2]);
    nodes.forEach((n) => { n.px = base.x(n.x); n.py = base.y(n.y); });
    draw();
  }
  const radius = (n) => 2.5 + 13 * Math.sqrt((n.pagerank || 0) / maxPr);
  const fill = (n) => view.byTier ? tierColor(n.tier) : communityColor(n.community, n.tier);
  const isMover = (n) => n.mover_class !== 'anchored';
  const visible = (n) => (!view.moversOnly || isMover(n)) && (view.selected == null || n.community === view.selected || (view.hover != null && n === view.hover));
  const dimmed = (n) => view.selected != null && n.community !== view.selected;

  function drawEdges() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (!view.edges) return;
    ctx.translate(transform.x, transform.y); ctx.scale(transform.k, transform.k);
    const dark = matchMedia('(prefers-color-scheme: dark)').matches && document.documentElement.dataset.theme !== 'light';
    const ink = dark ? '240,239,236' : '11,11,11';
    const k = transform.k;
    const selected = view.selected, hover = view.hover;
    ctx.lineWidth = 0.6 / k;
    for (const e of edges) {
      const a = nodes[e.s], b = nodes[e.t];
      let alpha;
      if (hover) alpha = (a === hover || b === hover) ? 0.75 : 0;
      else if (selected != null) {
        const inA = a.community === selected, inB = b.community === selected;
        if (inA && inB) alpha = 0.18 + 0.5 * Math.min(1, e.w / 0.4);
        else if (inA || inB) alpha = 0.06;
        else alpha = 0;
      } else alpha = 0.035 + 0.25 * Math.min(1, e.w / 0.6);
      if (view.moversOnly && !(isMover(a) && isMover(b))) alpha *= 0.25;
      if (alpha <= 0.004) continue;
      ctx.strokeStyle = `rgba(${ink},${alpha})`;
      ctx.lineWidth = (hover || (selected != null && a.community === selected && b.community === selected) ? 0.5 + 2.5 * Math.min(1, e.w / 0.5) : 0.6) / k;
      ctx.beginPath(); ctx.moveTo(a.px, a.py); ctx.lineTo(b.px, b.py); ctx.stroke();
    }
  }

  function drawNodes() {
    const k = transform.k;
    gRoot.attr('transform', transform);
    const ordered = nodes.slice().sort((a, b) => (dimmed(a) ? 0 : 1) - (dimmed(b) ? 0 : 1) || a.pagerank - b.pagerank);
    gNodes.selectAll('circle.node').data(ordered, (d) => d.id).join('circle')
      .attr('class', (d) => `node ${TIER_CLASS[d.tier] || 'outside'}`)
      .attr('cx', (d) => d.px).attr('cy', (d) => d.py).attr('r', (d) => radius(d) / Math.sqrt(k))
      .attr('fill', fill).attr('stroke-width', 1.1 / k)
      .attr('opacity', (d) => !visible(d) ? 0.06 : dimmed(d) ? 0.12 : 0.95)
      .on('mousemove', (e, d) => { view.hover = d; drawEdges(); showTip(e, nodeTip(d)); })
      .on('mouseleave', () => { view.hover = null; drawEdges(); hideTip(); })
      .on('click', (e, d) => { e.stopPropagation(); select(d.community, d); });
    gRings.selectAll('circle.ring').data(ordered.filter((d) => isMover(d) && visible(d) && !dimmed(d)), (d) => d.id).join('circle')
      .attr('class', 'ring').attr('cx', (d) => d.px).attr('cy', (d) => d.py)
      .attr('r', (d) => (radius(d) + 3) / Math.sqrt(k))
      .attr('stroke-width', (d) => (d.mover_class === 'multi-community mover' ? 1.8 : 0.8) / k)
      .attr('stroke-dasharray', (d) => d.mover_class === 'multi-community mover' ? null : `${2 / k} ${2 / k}`)
      .attr('opacity', 0.9);
    const labelled = labelSet();
    gLabels.selectAll('text.label').data(labelled, (d) => d.id).join('text').attr('class', 'label')
      .attr('x', (d) => d.px + (radius(d) + 4) / Math.sqrt(k)).attr('y', (d) => d.py).attr('dy', '0.35em')
      .attr('font-size', `${11 / k}px`).attr('stroke-width', 3 / k).text((d) => d.name);
  }
  function labelSet() {
    if (view.selected != null) {
      const c = commById.get(view.selected);
      const members = nodes.filter((n) => n.community === view.selected);
      const leaders = members.slice().sort((a, b) => b.pagerank - a.pagerank).slice(0, c.size >= 5 ? 3 : members.length);
      const movers = members.filter((n) => n.mover_class === 'multi-community mover').slice().sort((a, b) => b.mover_score - a.mover_score).slice(0, 5);
      return [...new Set([...leaders, ...movers])];
    }
    if (view.labels) return communities.filter((c) => c.size >= 5).map((c) => nodes[byId.get(c.leader_ids[0])]);
    return [];
  }
  function draw() { drawEdges(); drawNodes(); }

  const zoom = d3.zoom().scaleExtent([0.6, 14]).on('zoom', (e) => { transform = e.transform; draw(); });
  svg.call(zoom).on('dblclick.zoom', null);
  svg.on('click', () => { if (view.selected != null) select(null); });

  function zoomTo(c) {
    if (!c) { svg.transition().duration(600).call(zoom.transform, d3.zoomIdentity); return; }
    const x0 = base.x(c.bbox.x0), x1 = base.x(c.bbox.x1), y0 = base.y(c.bbox.y1), y1 = base.y(c.bbox.y0);
    const w = Math.max(x1 - x0, 40), hgt = Math.max(y1 - y0, 40);
    const k = Math.max(0.6, Math.min(8, 0.8 / Math.max(w / W, hgt / H)));
    const t = d3.zoomIdentity.translate(W / 2 - k * (x0 + x1) / 2, H / 2 - k * (y0 + y1) / 2).scale(k);
    svg.transition().duration(700).call(zoom.transform, t);
  }

  function nodeTip(n) {
    const c = commById.get(n.community);
    const ranks = Object.entries(n.ranks).map(([k, v]) => `${COMPOSITE_LABEL[k]} #${v}`).join(' · ');
    return `<b>${esc(n.name)}</b><br><span class="tt-muted">${esc(n.institution)}${n.state ? ', ' + esc(n.state) : ''}</span><br>` +
      `${esc(c.label)} <span class="tt-muted">(${TIER_LABEL[c.tier].toLowerCase()})</span><br>` +
      `PageRank ${fmt.num(n.pagerank * 1000, 2)}‰ · ${n.publications} papers · ${n.degree} coauthors<br>` +
      `${esc(fmt.title(n.hub_role))}${isMover(n) ? ` · ${esc(n.mover_class)}` : ''}<br>` +
      (isMover(n) && n.community_split ? `<span class="tt-muted">${esc(n.community_split)}</span><br>` : '') +
      `<span class="tt-muted">${ranks}</span>`;
  }

  /* ---- Selector chips, side card, legend ---- */
  const chips = document.getElementById('community-chips');
  const allChip = el('button', { class: 'chip', 'aria-pressed': 'true', onclick: () => select(null) }, ['All communities']);
  chips.append(allChip);
  communities.filter((c) => c.tier !== 'outside main network').forEach((c) => {
    chips.append(el('button', { class: 'chip', 'data-community': c.community, 'aria-pressed': 'false', onclick: () => select(c.community), title: c.tier_reason },
      [el('span', { class: 'dot', style: `background:${communityColor(c.community, c.tier)}` }), shortLabel(c), el('span', { class: 'n', text: c.size })]));
  });
  function shortLabel(c) { return c.label.replace(/^Loose /, '').replace(/ \(.*\)$/, ''); }

  const side = document.getElementById('graph-side');
  function renderSide(c, focus) {
    side.innerHTML = '';
    if (!c) {
      const movers = data.stability.mover_class_counts;
      side.append(el('div', { class: 'card' }, [
        el('div', { class: 'kicker', text: 'How to read the map' }),
        el('p', { html: 'Click a community chip or a node to zoom in. Hover any node for its scores. Drag to pan, scroll to zoom, click empty space to reset.' }),
        el('dl', {}, [
          el('dt', { text: 'Firm communities' }), el('dd', { text: communities.filter((x) => x.tier === 'firm').length }),
          el('dt', { text: 'Leader-dependent' }), el('dd', { text: summaries.node_removal?.leader_dependent_communities ?? '–' }),
          el('dt', { text: 'Multi-community movers' }), el('dd', { text: movers['multi-community mover'] ?? 0 }),
          el('dt', { text: 'Anchored HCPs' }), el('dd', { text: movers.anchored ?? 0 }),
        ]),
        el('p', { class: 'small muted', text: 'Top PageRank overall:' }),
        el('ol', { class: 'leader-list' }, nodes.slice().sort((a, b) => b.pagerank - a.pagerank).slice(0, 8).map((n) =>
          el('li', {}, [el('button', { onclick: () => select(n.community, n), text: n.name }), el('span', { class: 'muted', text: ` · ${n.institution}` })]))),
      ]));
      return;
    }
    const members = nodes.filter((n) => n.community === c.community);
    const leaders = members.slice().sort((a, b) => b.pagerank - a.pagerank).slice(0, 5);
    const movers = members.filter((n) => n.mover_class === 'multi-community mover').sort((a, b) => b.mover_score - a.mover_score);
    side.append(el('div', { class: 'card', style: `border-top: 4px solid ${communityColor(c.community, c.tier)}` }, [
      el('div', { class: 'row' }, [el('span', { class: `badge ${TIER_CLASS[c.tier]}`, text: TIER_LABEL[c.tier] }), el('span', { class: 'muted small', text: fmt.title(c.archetype) })]),
      el('h3', { text: c.label }),
      el('p', { class: 'small', html: `<strong>Theme.</strong> ${esc(c.theme || 'No distinctive title terms beyond the shared CAR-T lymphoma vocabulary.')}` }),
      el('dl', {}, [
        el('dt', { text: 'Members' }), el('dd', { text: c.size }),
        el('dt', { text: 'Top institution' }), el('dd', { text: `${c.top_institution} (${fmt.pct(c.institution_purity)})` }),
        el('dt', { text: 'Region' }), el('dd', { text: `${c.top_state} ${fmt.pct(c.state_purity)} · ${c.region}` }),
        el('dt', { text: 'Seed consensus' }), el('dd', { text: fmt.num(c.seed_consensus, 2) }),
        el('dt', { text: 'Jaccard, 10% papers dropped' }), el('dd', { text: fmt.num(c.jaccard_literature_drop, 2) }),
        el('dt', { text: 'Multi-community movers' }), el('dd', { text: movers.length }),
      ]),
      c.tier !== 'firm' ? el('p', { class: 'small muted', html: `<strong>Why ${c.tier}:</strong> ${esc(c.tier_reason)}.${c.drifts_to ? ' Members also group with ' + esc(c.drifts_to) + '.' : ''}` }) : null,
      c.flags ? el('p', { class: 'small', html: `<strong>Flag.</strong> ${esc(c.flags)}` }) : null,
      el('p', { class: 'small muted', text: 'Network leaders (PageRank):' }),
      el('ol', { class: 'leader-list' }, leaders.map((n) => el('li', {}, [el('button', { onclick: () => focusNode(n), text: n.name }), ` · ${fmt.num(n.pagerank * 1000, 1)}‰${n === focus ? ' ◂' : ''}`]))),
      movers.length ? el('p', { class: 'small muted', text: 'Movers in this community:' }) : null,
      movers.length ? el('ul', { class: 'leader-list' }, movers.slice(0, 6).map((n) => el('li', {}, [el('button', { onclick: () => focusNode(n), text: n.name }), el('span', { class: 'muted', text: ` · ${n.community_split}` })]))) : null,
    ]));
  }
  function focusNode(n) { view.hover = n; drawEdges(); drawNodes(); setTimeout(() => { view.hover = null; drawEdges(); }, 1800); }

  function select(community, focus, silent) {
    view.selected = community;
    if (!silent) history.replaceState(null, '', community == null ? '#results' : `#results/${community}`);
    chips.querySelectorAll('.chip').forEach((b) => b.setAttribute('aria-pressed', String((b.dataset.community ? +b.dataset.community : null) === community)));
    document.querySelectorAll('.ccard').forEach((card) => card.classList.toggle('hl', +card.dataset.community === community));
    const c = community == null ? null : commById.get(community);
    renderSide(c, focus);
    zoomTo(c);
    draw();
  }

  document.getElementById('graph-legend').append(
    legendItem(`<circle cx="7" cy="7" r="6" fill="${css('--c1')}" stroke="${css('--ink')}" stroke-width="1.2"/>`, 'Firm community (solid stroke)'),
    legendItem(`<circle cx="7" cy="7" r="6" fill="${css('--c4')}" stroke="${css('--ink')}" stroke-width="1.2" stroke-dasharray="2 1.6"/>`, 'Provisional (dashed)'),
    legendItem(`<circle cx="7" cy="7" r="6" fill="${css('--c8')}" stroke="${css('--ink')}" stroke-width="1.2" stroke-dasharray="1 1.2"/>`, 'Unstable (dotted)'),
    legendItem(`<circle cx="9" cy="9" r="4" fill="${css('--c3')}"/><circle cx="9" cy="9" r="8" fill="none" stroke="${css('--ink')}" stroke-width="1.6"/>`, 'Multi-community mover', 18),
    legendItem(`<circle cx="9" cy="9" r="4" fill="${css('--c3')}"/><circle cx="9" cy="9" r="8" fill="none" stroke="${css('--ink')}" stroke-width=".8" stroke-dasharray="2 2"/>`, 'Diffuse or data-thin mover', 18),
    el('span', { text: 'Size: PageRank. Edge weight: shared papers, fractional by author count.' }),
  );
  function legendItem(inner, label, size = 14) { return el('span', { html: `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">${inner}</svg> ${label}` }); }

  document.getElementById('toggle-edges').addEventListener('change', (e) => { view.edges = e.target.checked; drawEdges(); });
  document.getElementById('toggle-movers').addEventListener('change', (e) => { view.moversOnly = e.target.checked; draw(); });
  document.getElementById('toggle-tier').addEventListener('change', (e) => { view.byTier = e.target.checked; drawNodes(); });
  document.getElementById('toggle-labels').addEventListener('change', (e) => { view.labels = e.target.checked; drawNodes(); });
  document.getElementById('graph-reset').addEventListener('click', () => select(null));
  new ResizeObserver(() => resize()).observe(wrap);
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', draw);

  /* ---- Tier sections with one card per community ---- */
  const sections = document.getElementById('community-sections');
  const tierBlurb = {
    firm: 'Stable across 50 Leiden seeds and when 10% of the literature is dropped. Safe to name.',
    provisional: 'Members stay together across seeds, but the boundary shifts when 10% of papers are dropped. Name with a caveat.',
    unstable: 'Members change community across seeds or the group reshuffles under small perturbation. A loose grouping, not a bloc.',
    'outside main network': 'Fewer than five members. Described as individuals, not groups.',
  };
  for (const tier of data.meta.tier_order) {
    const list = communities.filter((c) => c.tier === tier);
    if (!list.length) continue;
    sections.append(el('div', { class: 'tier-head' }, [el('h2', { text: TIER_LABEL[tier] }), el('span', { class: 'muted', text: `${list.length} ${list.length === 1 ? 'community' : 'communities'} · ${tierBlurb[tier]}` })]));
    sections.append(el('div', { class: 'community-cards' }, list.map((c) => communityCard(c))));
  }
  function communityCard(c) {
    const color = communityColor(c.community, c.tier);
    const themeText = c.theme ? `${c.theme}.` : 'No title terms stand out beyond the shared CAR-T lymphoma vocabulary.';
    const facts = `${c.size} HCPs; ${fmt.pct(c.institution_purity)} at ${c.top_institution}; ${fmt.pct(c.state_purity)} in ${c.top_state}. Median paper year ${Math.round(c.median_paper_year)}; most common journal ${c.top_journal}.`;
    return el('div', { class: 'ccard', 'data-community': c.community, tabindex: 0, style: `border-left-color:${color}`,
      onclick: () => { select(c.community); document.querySelector('.graph-wrap').scrollIntoView({ behavior: 'smooth', block: 'start' }); },
      onkeydown: (e) => { if (e.key === 'Enter') e.currentTarget.click(); } }, [
      el('div', { class: 'row' }, [el('span', { class: `badge ${TIER_CLASS[c.tier]}`, text: TIER_LABEL[c.tier] }), el('span', { class: 'muted small', text: fmt.title(c.archetype) }), c.flags ? el('span', { class: 'badge', text: 'leader-dependent', title: c.flags }) : null]),
      el('h3', { text: c.label }),
      el('div', { class: 'meta', text: facts }),
      el('p', { class: 'theme', html: `<strong>Theme.</strong> ${esc(themeText)} ` + (c.tier !== 'outside main network' ? `Leaders: ${esc(fmt.title(c.leaders).replace(/;/g, ','))}.` : '') + (c.multi_community_movers ? ` ${c.multi_community_movers} multi-community mover${c.multi_community_movers > 1 ? 's' : ''}.` : '') }),
      c.tier !== 'firm' && c.tier !== 'outside main network' ? el('p', { class: 'why', text: `Why ${c.tier}: ${c.tier_reason}.${c.drifts_to ? ' Members also group with ' + c.drifts_to + '.' : ''}` }) : null,
    ]);
  }

  resize();
  renderSide(null);
  const applySub = (sub) => { const c = parseInt(sub, 10); if (commById.has(c)) select(c, null, true); else if (view.selected != null) select(null, null, true); };
  KOL.onSub('results', applySub);
  if (KOL.state.sub) setTimeout(() => applySub(KOL.state.sub), 50);
});
