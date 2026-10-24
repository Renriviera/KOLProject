/* Stability, weak-label, temporal, and data-source tabs. */
(function () {
  const { el, esc, fmt, css, showTip, hideTip, seriesColor, communityColor, tierColor, dotWhisker, hBars, lines, table, legend, svgIn, TIER_LABEL, TIER_CLASS, COMPOSITE_LABEL } = KOL;
  const section = (parent, title, sub) => { const s = el('section', {}); s.append(el('h2', { text: title })); if (sub) s.append(el('p', { class: 'lede', text: sub })); parent.append(s); return s; };
  const card = (parent, children) => { const c = el('div', { class: 'card' }, children); parent.append(c); return c; };
  const stat = (value, label) => el('div', { class: 'stat' }, [el('div', { class: 'value', text: value }), el('div', { class: 'label', text: label })]);
  const commLabel = (data, k) => (data.communities.find((c) => c.community === k) || {}).label || `Community ${k}`;
  const shortName = (s) => s.replace(/^Loose /, '').replace(/ \(.*\)$/, '').replace(' – ', '–');

  /* ================= Stability ================= */
  KOL.register('stability', function (data) {
    const root = document.getElementById('stability-content');
    const st = data.stability, S = data.meta.summaries;
    document.getElementById('stability-lede').textContent = 'There is no ground-truth KOL label, so rigor comes from perturbation: different seeds, different edge weights, resampled literature, removed leaders, and reweighted composites. What survives all of them can be acted on.';

    /* Method comparison */
    const sec1 = section(root, 'Leiden is more stable than the alternatives', 'Adjusted mutual information (AMI) between partitions from different random seeds. 1 means identical communities every run.');
    const grid = el('div', { class: 'grid-2' }); sec1.append(grid);
    const c1 = el('div', { class: 'card' }); grid.append(c1);
    const methods = st.method_comparison;
    hBars(c1, methods.filter((m) => m.seed_to_seed_ami_mean != null).map((m, i) => ({
      label: m.method.replace(/ \(.*\)/, ''), value: m.seed_to_seed_ami_mean, lo: m.seed_to_seed_ami_min, hi: m.seed_to_seed_ami_mean, color: seriesColor(i),
      tip: `<b>${esc(m.method)}</b><br>Mean seed-to-seed AMI ${fmt.num(m.seed_to_seed_ami_mean, 3)}, worst pair ${fmt.num(m.seed_to_seed_ami_min, 3)}<br>Modularity ${fmt.num(m.modularity, 3)} · ${m.communities} communities`,
    })), { domain: [0, 1], title: 'Seed-to-seed agreement', sub: `${methods[0].seeds} seeds each; whisker to the worst pair`, left: 90, width: 560, rowH: 34, fmtv: (v) => fmt.num(v, 2) });
    c1.append(el('p', { class: 'small muted', text: `The published greedy-modularity partition agrees with the Leiden consensus at AMI ${fmt.num(methods[2]?.ami_vs_leiden_medoid, 2)}. Leiden also guarantees every community is internally connected.` }));
    const c2 = el('div', { class: 'card' }); grid.append(c2);
    const sweep = st.resolution_sweep;
    const lc = lines(c2, [
      { name: 'Seed AMI (mean)', color: seriesColor(0), points: sweep.map((r) => [r.resolution, r.seed_ami_mean]) },
      { name: 'Seed AMI (worst pair)', color: seriesColor(0), dashed: true, points: sweep.map((r) => [r.resolution, r.seed_ami_min]) },
      { name: 'Modularity', color: seriesColor(1), points: sweep.map((r) => [r.resolution, r.modularity_medoid]) },
    ], { width: 520, height: 300, yDomain: [0, 1], xLabel: 'Leiden resolution', title: 'Resolution sweep', yFmt: fmt.num });
    sweep.forEach((r) => lc.svg.append('text').attr('class', 'mark-label').attr('x', lc.x(r.resolution)).attr('y', lc.y(1) + 12).attr('text-anchor', 'middle').text(`${Math.round(r.communities_median)} comm.`));
    c2.append(legend([{ color: seriesColor(0), label: 'Seed AMI mean', line: true }, { color: seriesColor(0), label: 'worst pair', line: true, dashed: true }, { color: seriesColor(1), label: 'Modularity', line: true }]));
    c2.append(el('p', { class: 'small muted', text: `The primary partition uses resolution ${data.meta.resolution}: the coarsest setting where the largest community stays institution-sized. Numbers above the plot are the median community count.` }));

    /* Leader dependence */
    const sec2 = section(root, `${S.node_removal?.leader_dependent_communities ?? 'Several'} communities depend on one person`, 'Each community\'s top-PageRank member is removed and Leiden is rerun. The grey dot is the noise floor from a fresh-seed rerun with nobody removed; the colored dot is the overlap after removal. A large gap means the community was that person\'s orbit.');
    const c3 = el('div', { class: 'card' }); sec2.append(c3);
    const removal = st.node_removal_leader.slice().sort((a, b) => b.drop_beyond_noise - a.drop_beyond_noise);
    dumbbell(c3, removal.map((r) => ({
      label: shortName(commLabel(data, r.community)), a: r.noise_floor_jaccard, b: r.jaccard_after_removal, hl: r.leader_dependent,
      color: communityColor(r.community, 'firm'), sub: `without ${fmt.title(r.removed_leader)}`,
      tip: `<b>${esc(commLabel(data, r.community))}</b> (${r.size} HCPs)<br>Remove ${esc(fmt.title(r.removed_leader))}: Jaccard ${fmt.num(r.jaccard_after_removal, 2)} vs noise floor ${fmt.num(r.noise_floor_jaccard, 2)}<br>${r.pieces_holding_25pct > 1 ? `Splits into ${r.pieces_holding_25pct} pieces. ` : ''}${r.leader_dependent ? '<b>Leader-dependent</b>' : 'Holds together'}`,
    })), { title: 'Community overlap after removing its leader', sub: 'Best-match Jaccard with the original membership; bold rows are leader-dependent', xLabel: 'Jaccard overlap' });
    const glob = st.node_removal_global;
    c3.append(el('p', { class: 'small muted', text: `Removing the top ${glob[glob.length - 1].removed_top_pagerank} PageRank HCPs at once leaves a mean community overlap of ${fmt.num(glob[glob.length - 1].mean_best_jaccard, 2)}; removing only the top one leaves ${fmt.num(glob[0].mean_best_jaccard, 2)}.` }));

    /* Bootstrap */
    const sec3 = section(root, 'How communities fare when the literature changes', 'Two perturbations of the paper set, 200 replicates each. A 10% drop asks what happens if the corpus missed part of the literature; the full bootstrap resamples papers with replacement and is far harsher.');
    const c4 = el('div', { class: 'card' }); sec3.append(c4);
    const boot = st.bootstrap_communities.filter((b) => b.size >= 5);
    dumbbell(c4, boot.map((b) => ({
      label: shortName(commLabel(data, b.community)), a: b.jaccard_drop10_mean, b: b.jaccard_resample_mean, color: communityColor(b.community, 'firm'),
      hl: false, tip: `<b>${esc(commLabel(data, b.community))}</b><br>10% drop: ${fmt.num(b.jaccard_drop10_mean, 2)} (${b.verdict_drop10})<br>Full bootstrap: ${fmt.num(b.jaccard_resample_mean, 2)} (${b.verdict_resample})`,
    })), { title: 'Mean Jaccard stability', sub: 'grey dot: full bootstrap · colored dot: 10% of papers dropped', xLabel: 'Jaccard overlap', refs: [0.75, 0.6] });
    c4.append(el('p', { class: 'small muted', text: 'Reference lines at 0.75 (firm) and 0.6 (provisional) are the thresholds used for the tiers on the Results tab.' }));

    /* Weight perturbation */
    const sec4 = section(root, 'The top leaders hold up under weight changes', 'Composite weights are redrawn 2,000 times from a Dirichlet centred on the stated weights (about ±0.3 total movement). The bar is each HCP\'s probability of staying in the top 25; the whisker is the 5th to 95th percentile rank.');
    const c5 = el('div', { class: 'card' }); sec4.append(c5);
    const seg = el('div', { class: 'seg', role: 'group' }); c5.append(seg);
    const pertHost = el('div'); c5.append(pertHost);
    const classColor = { 'robust leader': css('--c3'), 'weight-sensitive': css('--c4'), occasional: css('--c2'), rarely: css('--muted') };
    const renderPert = (name) => {
      seg.querySelectorAll('.btn').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.c === name)));
      const rows = st.weight_perturbation[name].slice(0, 25);
      const node = (r) => data.nodes.find((n) => n.id === r.hcp_id);
      hBars(pertHost, rows.map((r) => ({
        label: `${r.primary_rank}. ${fmt.title(r.display_name)}`, value: r.p_top25_near, color: classColor[r.robustness_class] || css('--muted'),
        tip: `<b>${esc(fmt.title(r.display_name))}</b> · ${esc(node(r)?.institution || '')}<br>Rank ${r.primary_rank}; stays top-25 in ${fmt.pct(r.p_top25_near)} of nearby weightings<br>Rank range p5–p95: ${r.rank_p5_near}–${r.rank_p95_near}<br>Top-25 under any weights: ${fmt.pct(r.p_top25_uninformative)}<br>${r.robustness_class}`,
      })), { domain: [0, 1], title: `${COMPOSITE_LABEL[name]}: probability of staying in the top 25`, sub: `${S.weight_perturbation?.[`${name}_robust_leaders`] ?? ''} robust leaders · ${S.weight_perturbation?.[`${name}_weight_sensitive`] ?? ''} weight-sensitive`, left: 230, width: 680, fmtv: (v) => fmt.pct(v) });
      pertHost.append(legend(Object.entries(classColor).map(([k, v]) => ({ color: v, label: k }))));
    };
    Object.keys(st.weight_perturbation).forEach((name, i) => seg.append(el('button', { class: 'btn', 'data-c': name, 'aria-pressed': String(i === 0), onclick: () => renderPert(name), text: COMPOSITE_LABEL[name] })));
    renderPert(Object.keys(st.weight_perturbation)[0]);

    /* Movers */
    const counts = st.mover_class_counts;
    const sec5 = section(root, 'Movers: HCPs whose community splits across runs', `Every HCP is placed in a community in each of the ${S.movers?.runs_used ?? 250} runs (50 seeds plus 200 literature-drop replicates). Anchored HCPs sit in one community at least 90% of the time. The rest are movers, and they are only worth acting on when the split rests on real evidence.`);
    sec5.append(el('div', { class: 'stat-row' }, [
      stat(counts.anchored ?? 0, 'Anchored: one community ≥ 90% of runs'),
      stat(counts['multi-community mover'] ?? 0, 'Multi-community movers: two or more homes, ≥ 5 papers and ≥ 10 coauthors'),
      stat(counts['diffuse mover'] ?? 0, 'Diffuse movers: well evidenced but spread thinly'),
      stat(counts['data-thin mover'] ?? 0, 'Data-thin movers: too few papers to trust the split'),
    ]));
    const c6 = el('div', { class: 'card' }); sec5.append(c6);
    const filt = el('div', { class: 'chips' }); c6.append(filt);
    const heatHost = el('div', { class: 'heat-wrap' }); c6.append(heatHost);
    const classes = ['multi-community mover', 'diffuse mover', 'data-thin mover'];
    const active = new Set(['multi-community mover']);
    classes.forEach((k) => filt.append(el('button', { class: 'chip', 'aria-pressed': String(active.has(k)), onclick: (e) => { active.has(k) ? active.delete(k) : active.add(k); e.currentTarget.setAttribute('aria-pressed', String(active.has(k))); drawHeat(); }, text: `${k} (${counts[k] ?? 0})` })));
    const commKeys = data.communities.map((c) => c.community);
    function drawHeat() {
      const rows = st.membership_shares.filter((r) => active.has(r.mover_class)).slice(0, 60);
      const cellW = 30, cellH = 16, left = 210, top = 90, width = left + commKeys.length * cellW + 20, height = top + rows.length * cellH + 20;
      const svg = svgIn(heatHost, width, height);
      svg.append('text').attr('class', 'title').attr('x', 0).attr('y', 14).text('Share of runs in each community');
      svg.append('text').attr('class', 'sub').attr('x', 0).attr('y', 28).text(`${rows.length} HCPs shown, sorted by mover score · columns are the 17 primary communities`);
      const color = d3.scaleSequential().domain([0, 1]).interpolator(d3.interpolateRgbBasis([css('--surface-2'), css('--seq-2'), css('--seq-4'), css('--seq-6')]));
      commKeys.forEach((k, j) => {
        const c = data.communities[j];
        svg.append('text').attr('x', left + j * cellW + cellW / 2).attr('y', top - 6).attr('text-anchor', 'start').attr('transform', `rotate(-55 ${left + j * cellW + cellW / 2} ${top - 6})`).attr('fill', communityColor(k, c.tier)).attr('font-weight', 600).text(k);
      });
      const g = svg.append('g').selectAll('g').data(rows).join('g').attr('transform', (d, i) => `translate(0,${top + i * cellH})`);
      g.append('text').attr('class', 'row-label').attr('x', left - 8).attr('y', cellH / 2).attr('dy', '0.35em').attr('text-anchor', 'end').text((d) => d.name.length > 26 ? d.name.slice(0, 25) + '…' : d.name);
      g.selectAll('rect').data((d) => d.shares.map((v, j) => ({ v, j, d }))).join('rect').attr('x', (s) => left + s.j * cellW + 1).attr('y', 1).attr('width', cellW - 2).attr('height', cellH - 2).attr('rx', 2).attr('fill', (s) => color(s.v))
        .on('mousemove', (e, s) => showTip(e, `<b>${esc(s.d.name)}</b><br>${esc(commLabel(data, commKeys[s.j]))}: ${fmt.pct(s.v)} of runs<br><span class="tt-muted">${esc(s.d.mover_class)} · ${s.d.publications} papers</span>`)).on('mouseleave', hideTip);
      g.selectAll('text.v').data((d) => d.shares.map((v, j) => ({ v, j }))).join('text').attr('class', 'v').filter((s) => s.v >= 0.15).attr('x', (s) => left + s.j * cellW + cellW / 2).attr('y', cellH / 2).attr('dy', '0.35em').attr('text-anchor', 'middle').attr('font-size', 9).attr('fill', (s) => s.v > 0.55 ? css('--surface') : css('--ink')).text((s) => Math.round(100 * s.v));
    }
    drawHeat();
    c6.append(el('p', { class: 'small muted', text: 'Column numbers are community ids; hover a cell for the community name. Cells show the percentage when it is 15% or more.' }));

    const c7 = el('div', { class: 'card' }); sec5.append(c7);
    c7.append(el('h3', { text: 'Do movers already work with more manufacturers?' }));
    c7.append(el('p', { class: 'small lede', text: 'Partial Spearman correlation between a mover signal and the number of distinct manufacturers paying the HCP in 2024, after removing publication count, degree, and PageRank. Positive means movers have broader industry relationships than anchored peers of the same size. This is contemporaneous and descriptive; it does not show openness to future marketing.' }));
    const bt = st.mover_breadth_test;
    dotWhisker(c7, bt.map((r) => ({
      label: `${r.signal === 'mover_score' ? 'Mover score' : 'Participation coefficient'} → ${r.outcome === 'manufacturers_2024' ? 'manufacturers' : 'products named'} (${r.scope})`,
      value: r.partial_spearman, lo: r.ci_low, hi: r.ci_high, color: r.signal === 'mover_score' ? seriesColor(0) : seriesColor(1), hl: r.scope === 'HCPs with evidence' && r.signal === 'mover_score' && r.outcome === 'manufacturers_2024',
      tip: `<b>${esc(r.signal)} → ${esc(r.outcome)}</b><br>${esc(r.scope)}, n=${r.n}<br>Partial ρ ${fmt.signed(r.partial_spearman)} [${fmt.signed(r.ci_low)}, ${fmt.signed(r.ci_high)}]<br>Raw ρ ${fmt.signed(r.raw_spearman)}<br>Controls: ${esc(r.controls)}`,
    })), { domain: [-0.1, 0.4], ref: 0, left: 330, width: 720, title: 'Partial Spearman with 95% bootstrap CI', xLabel: 'partial ρ', fmtv: (v) => fmt.signed(v) });
    const strata = st.mover_breadth_strata;
    c7.append(el('h3', { text: 'Median manufacturers by publication tertile' }));
    table(c7, strata, [{ label: 'Publications', key: 'publication_tertile' }, { label: 'Mover class', key: 'mover_class' }, { label: 'HCPs', key: 'hcps', num: true }, { label: 'Median manufacturers', key: 'median_manufacturers', num: true }, { label: 'Mean', value: (r) => fmt.num(r.mean_manufacturers, 1), num: true }]);
  });

  function dumbbell(container, rows, opts) {
    const { title, sub, xLabel, width = 720, rowH = 26, left = 300, refs = [] } = opts;
    const height = rows.length * rowH + 76;
    const svg = svgIn(container, width, height);
    const x = d3.scaleLinear().domain([0, 1]).range([left, width - 100]);
    if (title) svg.append('text').attr('class', 'title').attr('x', 0).attr('y', 14).text(title);
    if (sub) svg.append('text').attr('class', 'sub').attr('x', 0).attr('y', 28).text(sub);
    svg.append('g').attr('class', 'grid').attr('transform', `translate(0,${height - 34})`).call(d3.axisBottom(x).ticks(5).tickSize(-(height - 74)).tickFormat(''));
    svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${height - 34})`).call(d3.axisBottom(x).ticks(5));
    refs.forEach((r) => svg.append('line').attr('class', 'ref').attr('x1', x(r)).attr('x2', x(r)).attr('y1', 36).attr('y2', height - 34));
    if (xLabel) svg.append('text').attr('class', 'sub').attr('x', width - 100).attr('y', height - 6).attr('text-anchor', 'end').text(xLabel);
    const g = svg.append('g').selectAll('g').data(rows).join('g').attr('transform', (d, i) => `translate(0,${40 + i * rowH + rowH / 2})`);
    g.append('text').attr('class', 'row-label').attr('x', left - 10).attr('dy', '0.35em').attr('text-anchor', 'end').attr('font-weight', (d) => d.hl ? 700 : 400).text((d) => d.label);
    g.append('line').attr('x1', (d) => x(d.a)).attr('x2', (d) => x(d.b)).attr('stroke', (d) => d.hl ? css('--tier-unstable') : css('--axis')).attr('stroke-width', 2);
    g.append('circle').attr('cx', (d) => x(d.a)).attr('r', 5).attr('fill', css('--muted'));
    g.append('circle').attr('cx', (d) => x(d.b)).attr('r', 5.5).attr('fill', (d) => d.color).attr('stroke', css('--surface')).attr('stroke-width', 1.5);
    g.append('text').attr('class', 'mark-label').attr('x', (d) => x(Math.max(d.a, d.b)) + 9).attr('dy', '0.35em').text((d) => `${fmt.num(d.a, 2)} → ${fmt.num(d.b, 2)}`);
    g.append('rect').attr('x', 0).attr('y', -rowH / 2).attr('width', width).attr('height', rowH).attr('fill', 'transparent').on('mousemove', (e, d) => showTip(e, d.tip)).on('mouseleave', hideTip);
  }

  /* ================= Weak labels ================= */
  KOL.register('labels', function (data) {
    const root = document.getElementById('labels-content');
    const L = data.labels;
    document.getElementById('labels-lede').textContent = 'Two weak labels stand in for independent recognition: authorship of the 2023 ASTCT DLBCL guideline, which the build withheld from every feature, and receipt of 2024 consulting or speaking payments on records naming a lymphoma CAR-T product. Agreement is a sanity check, not validation of influence.';
    const labelName = { guideline_panel: '2023 ASTCT guideline panel', cart_industry_advisor: 'CAR-T industry advisors (2024)' };
    const scoreName = (s) => s.replace(' (published)', '').replace('baseline_exploratory_score', 'Published baseline').replace('equal_weight_consensus', 'Equal-weight consensus').replace(/^(academic_authority|network_connector|rising_voice)$/, (m) => COMPOSITE_LABEL[m]).replace('mean_citation_pct', 'Citation percentile').replace('senior_rate', 'Senior-author rate').replace('recent_share', 'Recent share').replace('log_consulting_usd', 'Consulting payments').replace(/^publications$/, 'Publication count').replace(/^pagerank$/, 'PageRank').replace(/^betweenness$/, 'Betweenness').replace(/^participation$/, 'Participation');
    const composites = new Set(['academic_authority', 'network_connector', 'rising_voice', 'equal_weight_consensus', 'baseline_exploratory_score (published)']);
    const palette = {}; let k = 0;
    const colorFor = (s) => { if (!(s in palette)) palette[s] = composites.has(s) ? seriesColor(k++) : css('--muted'); return palette[s]; };
    ['academic_authority', 'network_connector', 'rising_voice', 'equal_weight_consensus', 'baseline_exploratory_score (published)'].forEach(colorFor);

    const seg = el('div', { class: 'seg' }); root.append(el('div', { class: 'row', style: 'margin-bottom:1rem' }, [el('span', { class: 'kicker', text: 'Label' }), seg]));
    const host = el('div'); root.append(host);
    const render = (label) => {
      seg.querySelectorAll('.btn').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.l === label)));
      host.innerHTML = '';
      const rows = L.face_validity.filter((r) => r.label === label).slice().sort((a, b) => b.auc - a.auc);
      host.append(el('div', { class: 'stat-row' }, [stat(L.positives[label], 'positives out of 500'), stat(fmt.num(rows.filter((r) => !r.circular)[0].auc, 2), `best non-circular AUC: ${scoreName(rows.filter((r) => !r.circular)[0].score)}`), stat(fmt.num(rows.find((r) => r.score === 'publications').auc, 2), 'AUC of publication count alone')]));
      const grid = el('div', { class: 'grid-2' }); host.append(grid);
      const c1 = el('div', { class: 'card' }); grid.append(c1);
      const curves = L.roc_curves[label];
      const series = Object.entries(curves).map(([s, pts]) => {
        const row = rows.find((r) => r.score === s) || {};
        return { name: `${scoreName(s)} · AUC ${fmt.num(row.auc, 2)}${row.circular ? ' (circular)' : ''}`, color: colorFor(s), dashed: !!row.circular, width: composites.has(s) ? 2.2 : 1.4, opacity: composites.has(s) ? 1 : 0.7, points: pts, note: row.circular ? 'Includes the industry axis, which overlaps this label' : '' };
      }).sort((a, b) => (a.width - b.width));
      lines(c1, series, { width: 520, height: 440, xDomain: [0, 1], yDomain: [0, 1], xLabel: 'false positive rate', yLabel: 'true positive rate', title: `ROC: ${labelName[label]}`, diagonal: true });
      c1.append(legend([...Object.entries(palette).filter(([s]) => composites.has(s)).map(([s, c]) => ({ color: c, label: scoreName(s), line: true })), { color: css('--muted'), label: 'single features (grey)', line: true }, ...(label === 'cart_industry_advisor' ? [{ color: css('--ink-2'), label: 'dashed = circular (uses industry axis)', line: true, dashed: true }] : [])]));
      const c2 = el('div', { class: 'card' }); grid.append(c2);
      dotWhisker(c2, rows.map((r) => ({ label: scoreName(r.score), value: r.auc, lo: r.auc_ci_low, hi: r.auc_ci_high, color: colorFor(r.score), dashed: r.circular, hl: composites.has(r.score), note: r.circular ? '(circular)' : '',
        tip: `<b>${esc(scoreName(r.score))}</b><br>AUC ${fmt.num(r.auc, 3)} [${fmt.num(r.auc_ci_low, 2)}, ${fmt.num(r.auc_ci_high, 2)}]<br>Precision@25 ${fmt.pct(r.precision_at_25)} · lift@50 ${fmt.num(r.lift_at_50, 1)}×<br>Average precision ${fmt.num(r.average_precision, 2)}${r.circular ? '<br><i>Circular: score includes 2024 industry payments</i>' : ''}` })),
        { domain: [0.4, 1], ref: 0.5, left: 190, width: 560, rowH: 26, title: 'ROC AUC with 95% bootstrap CI', sub: 'Bold rows are composites; grey rows are single features', xLabel: 'AUC' });
      const c3 = el('div', { class: 'card' }); host.append(c3);
      table(c3, rows.map((r) => ({ ...r, _hl: composites.has(r.score) })), [
        { label: 'Score', value: (r) => scoreName(r.score) + (r.circular ? ' ⚠ circular' : '') }, { label: 'Type', value: (r) => composites.has(r.score) ? 'composite' : 'single feature' },
        { label: 'AUC', value: (r) => `${fmt.num(r.auc, 2)} [${fmt.num(r.auc_ci_low, 2)}, ${fmt.num(r.auc_ci_high, 2)}]`, num: true },
        { label: 'Avg precision', value: (r) => fmt.num(r.average_precision, 2), num: true }, { label: 'Precision @25', value: (r) => fmt.pct(r.precision_at_25), num: true },
        { label: 'Recall @50', value: (r) => fmt.pct(r.recall_at_50), num: true }, { label: 'Lift @50', value: (r) => fmt.num(r.lift_at_50, 1) + '×', num: true }]);
      host.append(el('p', { class: 'note', html: label === 'guideline_panel'
        ? 'PageRank alone matches the guideline panel best. No weighted composite beats it, and the confidence intervals overlap heavily with only 16 positives. The composites earn their place through explanation and segmentation, not through better retrieval.'
        : 'Scores that include the industry axis are marked circular: they contain 2024 payments, and the label is defined from 2024 payments. Among non-circular scores, publication count alone matches the advisor list best. Industry chooses high-volume authors; this does not show influence.' }));
    };
    Object.keys(labelName).forEach((label, i) => seg.append(el('button', { class: 'btn', 'data-l': label, 'aria-pressed': String(i === 0), onclick: () => render(label), text: labelName[label] })));
    render('guideline_panel');
  });

  /* ================= Temporal ================= */
  KOL.register('temporal', function (data) {
    const root = document.getElementById('temporal-content');
    const T = data.temporal, cutoff = T.cutoff_year, S = data.meta.summaries.temporal || {};
    document.getElementById('temporal-lede').textContent = `Every score is recomputed from papers published through ${cutoff} only, with the industry axis removed because 2024 payments postdate the window. The test asks whether those scores line up with what happened in ${cutoff + 1}–2025: new papers, new coauthors, and 2024 consulting. Publication count through ${cutoff} is the null every score must beat.`;
    root.append(el('div', { class: 'stat-row' }, [stat(S.pre_period_edges?.toLocaleString() ?? '–', `coauthor pairs through ${cutoff}`), stat(S.post_period_new_edges?.toLocaleString() ?? '–', `new coauthor pairs ${cutoff + 1}–2025`), stat(S.hcps_without_pre_period_papers ?? '–', `HCPs with no paper through ${cutoff}`)]));
    const scoreName = (s) => s.replace(/^(academic_authority|network_connector|rising_voice)/, (m) => COMPOSITE_LABEL[m]).replace('publications (volume-only null)', 'Publication count (null)').replace('pagerank alone', 'PageRank alone').replace('mean_citation_pct alone', 'Citation percentile alone');
    const targets = [
      { key: 'new_coauthors_2023_2025', label: `New coauthors ${cutoff + 1}–2025`, emphasis: true },
      { key: 'publications_2023_2025', label: `Publications ${cutoff + 1}–2025` },
      { key: 'log_consulting_usd_2024', label: 'Consulting payments 2024 (log)' },
    ].filter((t) => T.table[0][`spearman_${t.key}`] !== undefined);
    const colorFor = (s) => s.startsWith('academic') ? seriesColor(0) : s.startsWith('network') ? seriesColor(1) : s.startsWith('rising') ? seriesColor(2) : css('--muted');

    const sec1 = section(root, 'Does any score beat simply counting papers?', 'Change in Spearman correlation versus the publication-count null, with a 95% bootstrap CI. A whisker entirely to the right of zero means the score anticipates the outcome better than volume alone.');
    const grid = el('div', { class: 'grid-wide' }); sec1.append(grid);
    targets.forEach((t) => {
      const c = el('div', { class: 'card', style: t.emphasis ? `border-top: 4px solid ${seriesColor(1)}` : '' }); grid.append(c);
      const rows = T.table.filter((r) => !r.score.startsWith('publications')).map((r) => ({
        label: scoreName(r.score), value: r[`delta_vs_volume_${t.key}`], lo: r[`delta_ci_low_${t.key}`], hi: r[`delta_ci_high_${t.key}`], color: colorFor(r.score), hl: r[`delta_ci_low_${t.key}`] > 0,
        tip: `<b>${esc(scoreName(r.score))}</b><br>ρ with ${esc(t.label)}: ${fmt.num(r[`spearman_${t.key}`], 2)}<br>Δ vs volume null: ${fmt.signed(r[`delta_vs_volume_${t.key}`])} [${fmt.signed(r[`delta_ci_low_${t.key}`])}, ${fmt.signed(r[`delta_ci_high_${t.key}`])}]`,
      }));
      dotWhisker(c, rows, { domain: [-0.4, 0.2], ref: 0, left: 275, width: 600, rowH: 24, title: t.label, sub: `null ρ = ${fmt.num(T.table.find((r) => r.score.startsWith('publications'))[`spearman_${t.key}`], 2)} · bold = beats the null`, xLabel: 'Δ Spearman vs publication count', fmtv: (v) => fmt.signed(v) });
    });
    sec1.append(el('p', { class: 'note', html: `Network Connector without the industry axis is the one composite that beats publication count, and it does so on predicting <strong>new coauthors</strong> (${esc(S.best_on_new_coauthors_2023_2025 || '')}). Rising Voice does worse than counting papers on every target: a momentum score built from a handful of recent papers has little to say about the next three years. Citation percentiles were observed in 2026, so the "no impact" variants bound how much of any signal could be citation look-ahead.` }));

    const sec2 = section(root, `Pre-${cutoff} score against new coauthors, one dot per HCP`, 'Pick the score on the x-axis. Dot size is publication count through the cutoff; blue dots are 2023 guideline panelists.');
    const c2 = el('div', { class: 'card' }); sec2.append(c2);
    const picker = el('select', { class: 'btn', 'aria-label': 'Score on the x-axis', onchange: (e) => drawScatter(e.target.value) });
    c2.append(el('div', { class: 'row', style: 'margin-bottom:.5rem' }, [el('span', { class: 'kicker', text: 'Score' }), picker]));
    const scatterHost = el('div'); c2.append(scatterHost);
    const target = targets[0].key;
    const drawScatter = (score) => {
      picker.value = score;
      // Scores arrive on their native scale (composites 0–100, single features as percentiles); rank them here.
      const raw = T.hcps.filter((h) => h[score] != null);
      const order = raw.map((h, i) => [h[score], i]).sort((a, b) => a[0] - b[0]);
      const pct = new Array(raw.length); order.forEach(([, i], r) => { pct[i] = (r + 0.5) / raw.length; });
      const pts = raw.map((h, i) => ({ ...h, _x: pct[i] }));
      const width = 680, height = 420, m = { t: 30, r: 20, b: 44, l: 52 };
      const svg = svgIn(scatterHost, width, height);
      const x = d3.scaleLinear().domain([0, 1]).range([m.l, width - m.r]);
      const yMax = d3.max(pts, (h) => h[target]);
      const y = d3.scaleSqrt().domain([0, yMax]).nice().range([height - m.b, m.t]);
      const r = d3.scaleSqrt().domain([0, d3.max(pts, (h) => h.pre_publications)]).range([2, 11]);
      svg.append('text').attr('class', 'title').attr('x', m.l).attr('y', 16).text(`${scoreName(score)} (through ${cutoff}) vs new coauthors ${cutoff + 1}–2025`);
      svg.append('g').attr('class', 'grid').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(6).tickSize(-(width - m.l - m.r)).tickFormat(''));
      svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${height - m.b})`).call(d3.axisBottom(x).ticks(5, '.0%'));
      svg.append('g').attr('class', 'axis').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(6));
      svg.append('text').attr('class', 'sub').attr('x', width - m.r).attr('y', height - 6).attr('text-anchor', 'end').text('cohort percentile of the pre-cutoff score');
      svg.append('text').attr('class', 'sub').attr('x', m.l).attr('y', m.t - 6).text('new coauthors (sqrt scale)');
      const rho = T.table.find((t) => t.score === score)?.[`spearman_${target}`];
      svg.append('text').attr('class', 'mark-label').attr('x', width - m.r).attr('y', m.t + 4).attr('text-anchor', 'end').text(`Spearman ρ = ${fmt.num(rho, 2)}`);
      svg.append('g').selectAll('circle').data(pts.slice().sort((a, b) => a.guideline_panel - b.guideline_panel)).join('circle')
        .attr('cx', (h) => x(h._x)).attr('cy', (h) => y(h[target])).attr('r', (h) => r(h.pre_publications))
        .attr('fill', (h) => h.guideline_panel ? seriesColor(0) : css('--muted')).attr('opacity', (h) => h.guideline_panel ? 0.95 : 0.45).attr('stroke', css('--surface')).attr('stroke-width', 1)
        .on('mousemove', (e, h) => showTip(e, `<b>${esc(h.name)}</b><br>${scoreName(score)} percentile ${fmt.pct(h._x)}<br>${h.pre_publications} papers through ${cutoff} → ${h[target]} new coauthors${h.guideline_panel ? '<br>2023 guideline panelist' : ''}`)).on('mouseleave', hideTip);
    };
    T.score_columns.forEach((s) => picker.append(el('option', { value: s, text: scoreName(s) })));
    drawScatter(T.score_columns.find((s) => s.startsWith('network_connector (no industry)')) || T.score_columns[0]);

    const sec3 = section(root, `Pre-${cutoff} scores against the 2023 guideline panel`, 'The panel was published in 2023, so a score built from papers through 2022 is a genuinely prospective test of recognition. AUC with 95% bootstrap CI.');
    const c3 = el('div', { class: 'card' }); sec3.append(c3);
    dotWhisker(c3, T.table.slice().sort((a, b) => b.guideline_panel_auc - a.guideline_panel_auc).map((r) => ({ label: scoreName(r.score), value: r.guideline_panel_auc, lo: r.guideline_panel_auc_ci_low, hi: r.guideline_panel_auc_ci_high, color: colorFor(r.score), hl: r.score.startsWith('publications'),
      tip: `<b>${esc(scoreName(r.score))}</b><br>AUC ${fmt.num(r.guideline_panel_auc, 2)} [${fmt.num(r.guideline_panel_auc_ci_low, 2)}, ${fmt.num(r.guideline_panel_auc_ci_high, 2)}]<br>Precision@25 ${fmt.pct(r.guideline_panel_precision_at_25)}` })),
      { domain: [0.4, 1], ref: 0.5, left: 260, width: 640, rowH: 24, title: 'AUC against 2023 guideline panel', sub: 'bold = publication-count null', xLabel: 'AUC' });
  });

  /* ================= Data sources ================= */
  KOL.register('sources', function (data) {
    const root = document.getElementById('sources-content');
    const Sx = data.sources, m = data.meta;
    document.getElementById('sources-lede').textContent = `Every number on this site traces to public sources collected on dated snapshots: the cohort on ${Sx.cohort_snapshot_date}, Medicare utilization on ${Sx.utilization_snapshot_date}. No private, scraped, or patient-level data is used.`;

    /* Pipeline */
    const sec0 = section(root, 'From sources to scores');
    const c0 = el('div', { class: 'card pipeline' }); sec0.append(c0);
    c0.append(KOL.h(pipelineSvg(Sx, m)));

    /* Source cards */
    const sec1 = section(root, 'Sources', 'What each source contributes, how much of it there is, and what it cannot tell you.');
    const grid = el('div', { class: 'grid-2' }); sec1.append(grid);
    Sx.sources.forEach((s) => {
      const dl = el('dl');
      const add = (k, v) => { if (v != null && v !== '') { dl.append(el('dt', { text: k })); dl.append(el('dd', { text: v })); } };
      if (s.window) add('Window', `${s.window[0]} to ${s.window[1]}`);
      if (s.observed) add('Observed', s.observed.join(' to '));
      if (s.release_date) add('Source release', s.release_date);
      if (s.collected) add('Collected', s.collected);
      if (s.program_year) add('Program year', s.program_year);
      if (s.service_year) add('Service year', s.service_year);
      if (s.api_version) add('API version', s.api_version);
      if (s.dataset_id) add('Dataset id', s.dataset_id);
      if (s.dataset_version_id) add('Dataset version', s.dataset_version_id);
      if (s.pmid) add('PMID', s.pmid);
      Object.entries(s.counts || {}).forEach(([k, v]) => add(fmt.title(k.replace(/_/g, ' ')), typeof v === 'number' ? v.toLocaleString() : v));
      if (s.eligibility) add('Eligibility', `${s.eligibility.npi_type}, status ${s.eligibility.npi_status}, ${s.eligibility.country}; taxonomies ${s.eligibility.taxonomies.join(', ')}; ≥ ${s.eligibility.minimum_publications} supported papers`);
      grid.append(el('div', { class: 'card src-card' }, [
        el('h3', { text: s.name }), el('div', { class: 'role', text: s.role }), dl,
        s.query ? el('details', {}, [el('summary', { class: 'small', text: 'Publication query' }), el('pre', { text: s.query })]) : null,
        s.identity_rule ? el('p', { class: 'small muted', text: `Identity rule: ${s.identity_rule.replace(/_/g, ' ')}` }) : null,
        s.notes ? el('ul', {}, s.notes.map((n) => el('li', { text: n }))) : null,
        s.url ? el('p', { class: 'small' }, el('a', { href: s.url, target: '_blank', rel: 'noopener', text: 'Source documentation ↗' })) : null,
      ]));
    });
    sec1.append(el('p', { class: 'note', text: Sx.legacy_pilot }));

    /* Tables */
    const sec2 = section(root, 'Published tables', 'Identifiers are strings. Join authorships to HCPs on hcp_id and to publications on pmid; join utilization features on hcp_id and npi one-to-one.');
    const c2 = el('div', { class: 'card' }); sec2.append(c2);
    table(c2, Sx.tables, [{ label: 'Table', value: (r) => el('code', { text: r.table }) }, { label: 'Rows', value: (r) => r.rows.toLocaleString(), num: true }, { label: 'One row is', key: 'grain' }]);

    /* Glossary */
    const sec3 = section(root, 'Derived features', 'How the raw tables become the numbers on the other tabs.');
    const gl = [
      ['Fractional edge weight', 'For each shared paper, 1 ÷ (author count − 1), summed over shared papers. Two people on a 30-author consortium paper barely count; two people on a three-author paper count a lot.'],
      ['PageRank', 'Weighted PageRank on the coauthorship graph, damping 0.85. Being connected to well-connected people. Reported in per-mille.'],
      ['Betweenness', 'Share of shortest paths (distance = 1 ÷ weight) that pass through the HCP. Sitting between otherwise separate groups.'],
      ['Participation coefficient', 'One minus the sum of squared shares of an HCP\'s edge weight going to each community. Zero means all ties stay inside one community.'],
      ['Hub role', 'Guimerà–Amaral taxonomy from within-community strength z-score and participation: provincial hub, connector hub, kinless hub, connector, peripheral, ultra-peripheral.'],
      ['Citation-year percentile', 'Each paper\'s Europe PMC citation count ranked among papers from the same year, then averaged per HCP. Citations were observed in 2026.'],
      ['Senior-author rate and recent share', 'First-or-last-author papers ÷ papers, and 2023–2025 papers ÷ papers, each shrunk toward the cohort rate with a prior worth four papers so two papers cannot dominate.'],
      ['Axes and composites', `Each axis is the mean cohort percentile of its features; each composite is 100 × a weighted sum of axes. Axes: ${Object.entries(m.axes).map(([a, f]) => `${a} (${f.join(', ')})`).join('; ')}.`],
      ['Communities', `Leiden with the RB-configuration objective at resolution ${m.resolution}, run ${m.seeds} times; the partition most similar to the others is kept. Tiers come from seed consensus and literature-drop stability.`],
      ['Movers', 'Share of runs each HCP spends in each community, across seeds and literature-drop replicates aligned to the primary partition.'],
    ];
    const c3 = el('div', { class: 'card' }); sec3.append(c3);
    table(c3, gl.map(([k, v]) => ({ k, v })), [{ label: 'Feature', key: 'k' }, { label: 'Definition', key: 'v' }]);

    /* Composite weights */
    const sec4 = section(root, 'Composite weights', 'Weights are stated, sum to one, and every HCP\'s score decomposes into per-axis contributions.');
    const c4 = el('div', { class: 'card' }); sec4.append(c4);
    const axes = Object.keys(m.axes);
    table(c4, axes.map((a) => ({ a, ...Object.fromEntries(Object.entries(m.composites).map(([c, w]) => [c, w[a] ?? 0])) })), [{ label: 'Axis', key: 'a' }, ...Object.keys(m.composites).map((c) => ({ label: COMPOSITE_LABEL[c], value: (r) => fmt.num(r[c], 2), num: true }))]);

    /* Reproduction */
    const sec5 = section(root, 'Reproduce this site');
    const c5 = el('div', { class: 'card' }); sec5.append(c5);
    c5.append(el('pre', { text: `git clone https://github.com/Renriviera/KOLProject.git && cd KOLProject\nuv sync --locked\nuv run pytest\nuv run kolproject-kol                                  # rewrites models/kol_runs/${m.run_id}/\nuv run kolproject-kol-site models/kol_runs/${m.run_id}  # writes docs/data/site.json` }));
    c5.append(el('p', { class: 'small muted', text: `Run ${m.run_id} was computed ${m.created_at_utc} with igraph ${m.library_versions.igraph}, leidenalg ${m.library_versions.leidenalg}, networkx ${m.library_versions.networkx}, scikit-learn ${m.library_versions['scikit-learn']}. Input SHA-256:` }));
    c5.append(el('pre', { text: Object.entries(m.inputs_sha256).map(([k, v]) => `${v}  ${k}`).join('\n') }));

    /* Limits */
    const sec6 = section(root, 'Scope limitations recorded with the data');
    sec6.append(el('ul', { class: 'small' }, Sx.scope_limitations.map((l) => el('li', { text: l }))));
    sec6.append(el('p', { class: 'small muted', text: 'Third-party source content retains its original terms. Names and locations come from public registries and indexed publications and may be out of date.' }));
  });

  function pipelineSvg(Sx, m) {
    const pmc = Sx.sources.find((s) => s.name.startsWith('Europe PMC')) || { counts: {} };
    const npi = Sx.sources.find((s) => s.name.includes('NPPES')) || { counts: {} };
    const pay = Sx.sources.find((s) => s.name === 'CMS Open Payments') || { counts: {} };
    const box = (x, y, w, title, sub, date) => `<rect class="box" x="${x}" y="${y}" width="${w}" height="54" rx="8"/><text x="${x + w / 2}" y="${y + 20}" text-anchor="middle" font-weight="600">${esc(title)}</text><text x="${x + w / 2}" y="${y + 36}" text-anchor="middle" class="date">${esc(sub)}</text>${date ? `<text x="${x + w / 2}" y="${y + 48}" text-anchor="middle" class="date">${esc(date)}</text>` : ''}`;
    const arrow = (x1, y1, x2, y2) => `<path class="arrow" d="M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}"/>`;
    return `<svg viewBox="0 0 960 300" role="img" aria-label="Pipeline from sources to scores">
      <defs><marker id="arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="${css('--ink-2')}"/></marker></defs>
      ${box(10, 20, 190, 'Europe PMC / PubMed', `${(pmc.counts.records || 0).toLocaleString()} records · ${(pmc.counts.included || 0).toLocaleString()} included`, `observed ${(pmc.observed || []).join('–')}`)}
      ${box(10, 100, 190, 'NPPES NPI Registry', `${npi.counts.qualified_pool || ''} qualified → ${npi.counts.hcps || ''} HCPs`, 'identity verified per paper')}
      ${box(10, 180, 190, 'Open Payments 2024', `${(pay.counts.records || 0).toLocaleString()} records · ${pay.counts.hcps_with_payments || ''} HCPs`, `release ${pay.release_date || ''}`)}
      ${arrow(200, 47, 260, 127)}${arrow(200, 127, 260, 127)}${arrow(200, 207, 260, 127)}
      ${box(260, 100, 170, 'Cohort of 500 HCPs', `${(pmc.counts.authorship_links || 0).toLocaleString()} authorship links`, `snapshot ${Sx.cohort_snapshot_date}`)}
      ${arrow(430, 127, 490, 127)}
      ${box(490, 100, 170, 'Coauthorship graph', `${(pmc.counts.coauthor_edges || 0).toLocaleString()} fractional edges`, 'rebuilt from authorships')}
      ${arrow(660, 127, 720, 67)}${arrow(660, 127, 720, 187)}
      ${box(720, 40, 230, 'Leiden communities', `resolution ${m.resolution} · ${m.seeds} seeds · tiers`, `run ${m.run_id}`)}
      ${box(720, 160, 230, 'Features and composites', 'six axes · three composite scores', 'plus 11 robustness experiments')}
      <text x="10" y="275" class="date">Medicare utilization (2024 services, collected ${Sx.utilization_snapshot_date}) is published alongside but feeds no score: only 7 of 500 HCPs have a selected drug code.</text>
    </svg>`;
  }
})();
