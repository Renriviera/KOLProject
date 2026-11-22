/* The flexible canvas below the map: fact boxes, tables, bar and line charts, timelines, ranked lists, text.
   A panel never carries data itself: it names a query result (handle) and this file draws the rows. */

import { barChart, el, fmt, legendKeys, lineChart, seriesColor } from './charts.js';

const ID_COLUMNS = new Set(['npi', 'ccn', 'pmid', 'community_id', 'record_id']);
const NAME_COLUMNS = ['name', 'hcp', 'center', 'center_name', 'community', 'label', 'title', 'manufacturer_group', 'brand', 'drug', 'family', 'region', 'study'];
const DEFAULT_SPAN = { kpis: 'full', table: 'full', line: 'half', bar: 'half', ranked_list: 'half', timeline: 'half', text: 'half' };
const isNumber = (column) => ['usd', 'count', 'share', 'rank', 'days', 'index'].includes(column.unit);
const isTime = (name) => ['year', 'month', 'program_year', 'publication_year', 'service_year', 'date'].includes(name);

function nameColumn(columns) {
  return columns.find((c) => NAME_COLUMNS.includes(c.name)) || columns.find((c) => c.unit === 'text' && !ID_COLUMNS.has(c.name)) || columns[0];
}

function followUp(row, ask) {
  if (row.npi && row.name) return () => ask(`Show the profile of ${row.name}.`);
  if (row.ccn && (row.name || row.center || row.center_name)) return () => ask(`Tell me about ${row.name || row.center || row.center_name}.`);
  return null;
}

function tableNode(result, ask, limit = 12) {
  const columns = result.columns.filter((c) => !ID_COLUMNS.has(c.name) || !result.columns.some((d) => NAME_COLUMNS.includes(d.name)));
  const body = el('tbody');
  const draw = (count) => {
    body.replaceChildren(...result.rows.slice(0, count).map((row) => {
      const go = followUp(row, ask);
      return el('tr', { class: go ? 'link' : '', onclick: go || (() => {}) }, columns.map((c) => {
        const blank = row[c.name] === null || row[c.name] === undefined || row[c.name] === '';
        return el('td', { class: isNumber(c) ? 'num' : '' }, blank ? el('span', { class: 'na', title: 'Not shown or not available', text: '–' }) : document.createTextNode(fmt(row[c.name], c.unit)));
      }));
    }));
  };
  draw(limit);
  const wrap = el('div', {}, [el('div', { class: 'table-wrap' }, el('table', {}, [
    el('thead', {}, el('tr', {}, columns.map((c) => el('th', { class: isNumber(c) ? 'num' : '', text: c.label })))), body]))]);
  if (result.rows.length > limit) {
    const more = el('button', { class: 'chip', text: `Show all ${result.rows.length} rows`, onclick: () => { draw(result.rows.length); more.remove(); } });
    wrap.append(el('div', { class: 'chips' }, more));
  }
  return wrap;
}

function kpis(result) {
  const row = result.rows[0] || {};
  const tiles = result.columns.filter((c) => !ID_COLUMNS.has(c.name)).map((c) => {
    const words = !isNumber(c) && c.unit !== 'year';
    return el('div', { class: 'kpi' }, [el('div', { class: 'label', text: c.label }),
      el('div', { class: 'value' + (words ? ' words' : ''), text: fmt(row[c.name], c.unit, true) })]);
  });
  return el('div', { class: 'kpis' }, tiles);
}

function rankedList(result, options, ask) {
  const name = nameColumn(result.columns);
  const value = result.columns.find((c) => (options.y || []).includes(c.name)) || result.columns.find((c) => isNumber(c) && c.unit !== 'rank');
  const detail = result.columns.filter((c) => c !== name && c !== value && c.unit === 'text' && !ID_COLUMNS.has(c.name)).slice(0, 3);
  return el('ol', { class: 'ranked' }, result.rows.slice(0, options.top_n || 10).map((row) => {
    const go = followUp(row, ask);
    return el('li', { class: go ? 'link' : '', onclick: go || (() => {}) }, [
      el('div', { class: 'who' }, [document.createTextNode(String(row[name.name] ?? '–')),
        el('small', { text: detail.map((c) => row[c.name]).filter(Boolean).join(' · ') })]),
      el('div', { class: 'val', text: value ? `${fmt(row[value.name], value.unit, true)} ${value.unit === 'usd' ? '' : value.label.toLowerCase()}`.trim() : '' })]);
  }));
}

function timeline(result, meta) {
  const date = result.columns.find((c) => c.name === 'date') || result.columns.find((c) => isTime(c.name)) || result.columns[0];
  const label = result.columns.find((c) => c.name === 'label') || nameColumn(result.columns);
  return el('ol', { class: 'timeline' }, result.rows.map((row) => el('li', { style: row.brand ? `--dot:${seriesColor(row.brand, 0)}` : '' }, [
    el('time', { text: String(row[date.name] ?? '') }), document.createTextNode(String(row[label.name] ?? ''))])));
}

/* Decide what a line panel draws: series split by a column, or one series per measure (same unit only). */
function linePlan(result, options) {
  const x = options.x && result.columns.some((c) => c.name === options.x) ? options.x : (result.columns.find((c) => isTime(c.name)) || {}).name;
  if (!x) return null;
  let measures = (options.y || []).map((n) => result.columns.find((c) => c.name === n)).filter(Boolean).filter(isNumber);
  if (!measures.length) measures = result.columns.filter((c) => isNumber(c) && c.name !== x && c.unit !== 'rank').slice(0, 2);
  const split = options.split_by && result.columns.some((c) => c.name === options.split_by) ? options.split_by
    : (['brand', 'drug', 'family', 'manufacturer_group', 'division_group'].find((n) => result.columns.some((c) => c.name === n) && new Set(result.rows.map((r) => r[n])).size > 1));
  const xs = [...new Set(result.rows.map((r) => String(r[x])))];
  const charts = [];
  if (split) {
    const m = measures[0];
    const names = [...new Set(result.rows.map((r) => r[split]))].filter((n) => n !== null && n !== undefined);
    const totals = new Map(names.map((n) => [n, result.rows.filter((r) => r[split] === n).reduce((a, r) => a + (Number(r[m.name]) || 0), 0)]));
    const top = names.sort((a, b) => totals.get(b) - totals.get(a)).slice(0, 8);
    charts.push({ title: m.label, unit: m.unit, series: top.map((n) => ({ name: String(n), points: xs.map((v) => {
      const row = result.rows.find((r) => String(r[x]) === v && r[split] === n);
      return [v, row ? row[m.name] : null];
    }) })) });
  } else {
    const byUnit = new Map();
    measures.forEach((m) => byUnit.set(m.unit, (byUnit.get(m.unit) || []).concat(m)));
    for (const [unit, group] of byUnit) {  // one axis per chart: different units become separate small charts
      charts.push({ title: group.map((m) => m.label).join(', '), unit, series: group.map((m) => ({ name: m.label, points: xs.map((v) => {
        const row = result.rows.find((r) => String(r[x]) === v);
        return [v, row ? row[m.name] : null];
      }) })) });
    }
  }
  return { charts, monthly: /^\d{4}-\d{2}$/.test(xs[0] || '') };
}

function lineNode(result, options, meta) {
  const plan = linePlan(result, options);
  if (!plan || !plan.charts.length) return null;
  const wrap = el('div', { class: plan.charts.length > 1 ? 'multi' : '' });
  for (const chart of plan.charts) {
    const box = el('div', { class: 'chart' });
    if (plan.charts.length > 1) box.append(el('h3', { text: chart.title }));
    if (chart.series.length > 1) box.append(legendKeys(chart.series.map((s) => s.name)));
    wrap.append(box);
    queueMicrotask(() => lineChart(box, { ...chart, yLabel: chart.title, markers: options.markers, events: meta.events, monthly: plan.monthly }));
  }
  return wrap;
}

function barNode(result, options) {
  const name = options.x && result.columns.find((c) => c.name === options.x && !isNumber(c)) || nameColumn(result.columns);
  const asked = (options.y || []).map((n) => result.columns.find((c) => c.name === n)).filter((c) => c && isNumber(c));
  const varies = (c) => new Set(result.rows.map((r) => r[c.name])).size > 1;  // a measure that is the same in every row says nothing as bars
  const value = asked.find(varies) || asked[0] || result.columns.find((c) => isNumber(c) && c.unit !== 'rank');
  if (!value) return null;
  const rows = result.rows.slice(0, options.top_n || 15).map((r) => ({ label: r[name.name] ?? '–', value: r[value.name], color: ['brand', 'drug'].includes(name.name) ? seriesColor(r[name.name], 0) : null }));
  const box = el('div', { class: 'chart' });
  queueMicrotask(() => barChart(box, { rows, unit: value.unit, valueLabel: value.label }));
  return { node: box, sub: value.label };
}

const SOURCES = [
  [/^ref_/, 'CareSet DocGraph shared Medicare patients'], [/^pay_|^ownership|^manufacturer/, 'CMS Open Payments'],
  [/^tie_/, 'conflict-of-interest statements on papers (PubMed)'], [/^mcr_partd/, 'Medicare Part D'], [/^mcr_cart|^mcr_/, 'Medicare Part B'],
  [/^mcd_/, 'Medicaid provider spending (HHS)'], [/^center_medicare/, 'Medicare inpatient file'], [/^national_spending/, 'CMS spending by drug'],
  [/^dim_product|^product_event/, 'FDA approval letters and CMS code files'], [/^pub$|^authorship|^coauthor|^hcp_pub/, 'publications (Europe PMC)'],
  [/^dim_community/, 'co-authorship communities'], [/^dim_hcp|^hcp_center/, 'CMS clinician files'], [/^dim_center/, 'CAR-T center list'],
];
function sourceNames(tables) {
  const names = [...new Set((tables || []).map((t) => (SOURCES.find(([pattern]) => pattern.test(t)) || [null, t])[1]))];
  return names.join('; ') || 'project database';
}

/* Widths in grid order. A half-width panel left alone in its row takes the whole row, so no row is half empty. */
function spans(panels) {
  const out = panels.map((p) => p.span || DEFAULT_SPAN[p.type] || 'full');
  let open = false;  // true while the first half of a row is filled and the second is free
  out.forEach((span, i) => {
    if (span !== 'half') { open = false; return; }
    if (open) { open = false; return; }
    if (out[i + 1] === 'half') open = true; else out[i] = 'full';
  });
  return out;
}

function card(panel, width, result, meta, ask) {
  const options = panel.options || {};
  const node = el('article', { class: 'panel ' + width });
  node.append(el('h2', { text: panel.title }));
  if (panel.type === 'text') { node.append(el('p', { text: options.body || '' })); return node; }
  if (!result) return null;
  const sub = el('p', { class: 'sub', text: result.scope_label || '' });
  node.append(sub);
  let body = null, chart = false;
  if (!result.rows.length) body = el('p', { class: 'empty', text: 'Nothing in the database for this.' });
  else if (panel.type === 'kpis') body = kpis(result);
  else if (panel.type === 'ranked_list') body = rankedList(result, options, ask);
  else if (panel.type === 'timeline') body = timeline(result, meta);
  else if (panel.type === 'line') { body = lineNode(result, options, meta); chart = true; }
  else if (panel.type === 'bar') { const bar = barNode(result, options); if (bar) { body = bar.node; sub.textContent = [result.scope_label, bar.sub].filter(Boolean).join(' · '); chart = true; } }
  if (!body) { body = tableNode(result, ask); chart = false; }
  node.append(body);
  const source = `Source: ${sourceNames(result.source.tables)}${result.source.years ? ' · ' + result.source.years : ''}`;
  const foot = el('div', { class: 'foot' }, [el('span', { text: source })]);
  if (chart) {  // every chart has a table view, so no value depends on hovering
    let table = null;
    foot.append(el('button', { type: 'button', text: 'Show as table', onclick: (event) => {
      if (table) { table.remove(); table = null; event.target.textContent = 'Show as table'; return; }
      table = tableNode(result, ask); node.insertBefore(table, foot); event.target.textContent = 'Hide table';
    } }));
  }
  node.append(foot);
  return node;
}

const drawn = new WeakMap();  // canvas -> the cards on it, by what they show

/* Draw the panels of a view. Panels arrive one at a time while the agent works, so a card that shows the same
   thing as before is kept as it is: a chart the user is reading does not redraw when its neighbour arrives.
   `fresh` redraws everything, for a change of colour theme. */
export function renderPanels(host, view, results, meta, ask, fresh = false) {
  const before = fresh ? new Map() : (drawn.get(host) || new Map());
  const now = new Map();
  const widths = spans(view.panels);
  for (const [index, panel] of view.panels.entries()) {
    const result = panel.handle ? results[panel.handle] : null;
    const key = JSON.stringify([panel, widths[index], result ? result.row_count : null]);
    const node = before.get(key) || card(panel, widths[index], result, meta, ask);
    if (node) now.set(key, node);
  }
  drawn.set(host, now);
  const cards = [...now.values()];
  if (cards.length !== host.children.length || cards.some((node, i) => host.children[i] !== node)) host.replaceChildren(...cards);
}
