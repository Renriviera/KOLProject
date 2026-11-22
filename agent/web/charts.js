/* Charts for the answer canvas. Marks follow one fixed spec: 2px lines, end dots with a surface ring,
   bars at most 24px with a rounded data end, hairline grid, one axis per chart, a legend for two or more
   series, and a hover readout that lists every series. Every label is inserted with textContent. */

const d3 = window.d3;
export const SERIES = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6', '--s7', '--s8'];
// Colour follows the entity: each brand, drug, company and division keeps its slot in every chart.
const FIXED = {
  Kymriah: 0, Yescarta: 1, Tecartus: 2, Breyanzi: 3, Abecma: 4, Carvykti: 5, Aucatzyl: 6,
  ibrutinib: 0, acalabrutinib: 1, zanubrutinib: 2, pirtobrutinib: 3, lenalidomide: 4, venetoclax: 5, tazemetostat: 6,
  core: 0, affiliates: 1, referring: 2, other: 3,
};
export const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
export const el = (tag, attrs = {}, children = []) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'text') node.textContent = v; else if (k === 'class') node.className = v; else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
};

export function seriesColor(name, index) {
  const key = String(name ?? '');
  const slot = key in FIXED ? FIXED[key] : (key.toLowerCase() in FIXED ? FIXED[key.toLowerCase()] : index);
  return css(SERIES[slot % SERIES.length]);
}

const whole = (text) => text.replace(/\.0+$/, '');  // "$200.0M" reads better as "$200M"

export function fmt(value, unit, compact = false) {
  if (value === null || value === undefined || value === '') return '–';
  if (unit === 'usd') {
    const v = Number(value);
    if (compact && Math.abs(v) >= 1e9) return '$' + whole((v / 1e9).toFixed(2)) + 'B';
    if (compact && Math.abs(v) >= 1e6) return '$' + whole((v / 1e6).toFixed(Math.abs(v) >= 1e7 ? 1 : 2)) + 'M';
    if (compact && Math.abs(v) >= 1e4) return '$' + (v / 1e3).toFixed(0) + 'K';
    return '$' + v.toLocaleString('en-US', { maximumFractionDigits: 0 });
  }
  if (unit === 'share') return (Number(value) * 100).toFixed(1) + '%';
  if (unit === 'count' || unit === 'rank' || unit === 'days') {
    const v = Number(value);
    if (compact && Math.abs(v) >= 1e6) return whole((v / 1e6).toFixed(1)) + 'M';
    if (compact && Math.abs(v) >= 1e4) return (v / 1e3).toFixed(0) + 'K';
    return v.toLocaleString('en-US', { maximumFractionDigits: unit === 'days' ? 1 : 0 });
  }
  if (unit === 'index') return Number(value).toFixed(3);
  return String(value);
}

const tip = () => document.getElementById('tip');
export function showTip(event, title, rows) {
  const node = tip();
  node.replaceChildren(el('div', { class: 't', text: title }), ...rows.map((r) => el('div', { class: 'r' }, [
    el('span', {}, [r.color ? el('i', { style: `--k:${r.color}` }) : null, document.createTextNode(r.label)]), el('b', { text: r.value })])));
  node.hidden = false;
  const pad = 14, box = node.getBoundingClientRect();
  node.style.left = Math.min(window.innerWidth - box.width - 8, event.clientX + pad) + 'px';
  node.style.top = Math.max(8, Math.min(window.innerHeight - box.height - 8, event.clientY - box.height - 8)) + 'px';
}
export const hideTip = () => { tip().hidden = true; };

/* x positions: a year, or a YYYY-MM month, as a decimal year so approval dates can sit between ticks. */
export function timeValue(v) {
  const s = String(v);
  const m = s.match(/^(\d{4})-(\d{2})(?:-(\d{2}))?$/);
  if (m) return Number(m[1]) + (Number(m[2]) - 1) / 12 + (m[3] ? (Number(m[3]) - 1) / 365 : 0);
  return Number(s);
}

function markerRows(markers, events, domain) {
  if (!markers || markers === 'none' || !events) return [];
  const want = (e) => e.event_type !== 'coding' && e.event_type !== 'coverage' && e.event_type !== 'safety' && e.event_type !== 'regulatory' && e.brand
    && (markers === 'all_products' || (markers === 'lymphoma_products' && e.lymphoma) || (Array.isArray(markers) && markers.includes(e.brand)));
  return events.filter(want).map((e) => ({ x: timeValue(e.date), label: e.label, brand: e.brand, date: e.date, first: e.event_type === 'first_approval' }))
    .filter((e) => e.x >= domain[0] && e.x <= domain[1]);
}

/* Multi-series line chart. series = [{name, points: [[xLabel, y|null], ...]}], all in one unit.
   The x axis runs over whole years. A yearly value sits at the middle of its year, so an approval date
   falls where it belongs: after the point of the year it happened in only if it came in the second half. */
export function lineChart(host, { series, unit, yLabel, markers, events, monthly }) {
  const longest = Math.max(...series.map((s) => String(s.name).length));
  const direct = series.length > 1 && series.length <= 4 && longest <= 16;  // longer names stay in the legend only
  const W = Math.max(360, host.clientWidth || 640), H = 250, m = { top: markersTop(markers), right: direct ? longest * 6.2 + 18 : 20, bottom: 26, left: 52 };
  const xs = [...new Set(series.flatMap((s) => s.points.map((p) => p[0])))].sort((a, b) => timeValue(a) - timeValue(b));
  if (!xs.length) { host.append(el('p', { class: 'empty', text: 'No values to draw.' })); return; }
  const span = monthly ? 1 / 12 : 1, at = (v) => timeValue(v) + span / 2;
  const domain = [timeValue(xs[0]), timeValue(xs[xs.length - 1]) + span];
  const x = d3.scaleLinear().domain(domain).range([m.left, W - m.right]);
  const max = d3.max(series, (s) => d3.max(s.points, (p) => p[1])) || 1;
  const y = d3.scaleLinear().domain([0, max]).nice(4).range([H - m.bottom, m.top]);
  const svg = d3.select(host).append('svg').attr('viewBox', `0 0 ${W} ${H}`).attr('role', 'img').attr('aria-label', yLabel);
  svg.append('g').selectAll('line').data(y.ticks(4)).join('line').attr('class', 'gridline').attr('x1', m.left).attr('x2', W - m.right).attr('y1', (d) => y(d)).attr('y2', (d) => y(d));
  svg.append('g').attr('class', 'axis').selectAll('text').data(y.ticks(4)).join('text').attr('x', m.left - 8).attr('y', (d) => y(d) + 4).attr('text-anchor', 'end')
    .text((d) => fmt(d, unit, true));
  const years = d3.range(Math.floor(domain[0]), Math.ceil(domain[1] - 1e-9)).filter((d) => d + 0.5 >= domain[0] && d + 0.5 <= domain[1]);
  const step = Math.ceil(years.length / Math.max(2, Math.floor((W - m.left - m.right) / 54)));
  svg.append('g').attr('class', 'axis').selectAll('text').data(years.filter((_, i) => i % step === 0)).join('text').attr('x', (d) => x(d + 0.5)).attr('y', H - 8)
    .attr('text-anchor', 'middle').text((d) => d);
  svg.append('line').attr('class', 'gridline').attr('x1', m.left).attr('x2', W - m.right).attr('y1', y(0)).attr('y2', y(0)).style('stroke', css('--axis'));

  const marks = markerRows(markers, events, domain);
  const lanes = [];
  const g = svg.append('g').attr('class', 'marker');
  marks.sort((a, b) => a.x - b.x).forEach((e) => {
    const px = x(e.x), text = e.first ? e.brand : e.brand + ' +', width = text.length * 5.8 + 4;
    const flip = px + width > W - 2;  // near the right edge the label sits left of its line
    const from = flip ? px - width : px, to = flip ? px : px + width;
    let lane = lanes.findIndex((end) => end + 4 < from);
    if (lane < 0 && lanes.length < LANES) lane = lanes.length;
    const item = g.append('g');
    item.append('title').text(`${e.date}: ${e.label}`);
    if (lane < 0) {  // no room for another name: keep the line, the name is in the hover readout
      item.append('line').attr('x1', px).attr('x2', px).attr('y1', m.top - 4).attr('y2', y(0));
      return;
    }
    lanes[lane] = to;
    const top = 10 + lane * 12;
    item.append('line').attr('x1', px).attr('x2', px).attr('y1', top + 3).attr('y2', y(0));
    item.append('text').attr('x', flip ? px - 3 : px + 3).attr('y', top).attr('text-anchor', flip ? 'end' : 'start').text(text);
  });

  const line = d3.line().defined((p) => p[1] !== null && p[1] !== undefined).x((p) => x(at(p[0]))).y((p) => y(p[1])).curve(d3.curveMonotoneX);
  const surface = css('--surface');
  const ends = [];
  series.forEach((s, i) => {
    const color = seriesColor(s.name, i), pts = s.points.filter((p) => p[1] !== null && p[1] !== undefined);
    svg.append('path').attr('d', line(s.points)).attr('fill', 'none').attr('stroke', color).attr('stroke-width', 2).attr('stroke-linejoin', 'round').attr('stroke-linecap', 'round');
    if (pts.length === 1 || !monthly) svg.append('g').selectAll('circle').data(pts.length <= 12 ? pts : pts.slice(-1)).join('circle')
      .attr('cx', (p) => x(at(p[0]))).attr('cy', (p) => y(p[1])).attr('r', 4).attr('fill', color).attr('stroke', surface).attr('stroke-width', 2);
    if (pts.length) ends.push({ name: s.name, y: y(pts[pts.length - 1][1]), x: x(at(pts[pts.length - 1][0])) });
  });
  if (direct) {  // direct end labels, only when they do not collide
    ends.sort((a, b) => a.y - b.y);
    const clear = ends.every((e, i) => i === 0 || e.y - ends[i - 1].y >= 12);
    if (clear) svg.append('g').selectAll('text').data(ends).join('text').attr('x', (d) => d.x + 8).attr('y', (d) => d.y + 4).style('fill', css('--ink')).text((d) => d.name);
  }

  const cross = svg.append('line').attr('class', 'cross').attr('y1', m.top).attr('y2', y(0)).style('display', 'none');
  svg.append('rect').attr('x', m.left).attr('y', m.top).attr('width', W - m.left - m.right).attr('height', H - m.top - m.bottom).attr('fill', 'transparent')
    .on('pointermove', (event) => {
      const [px] = d3.pointer(event);
      const nearest = xs.reduce((best, v) => Math.abs(x(at(v)) - px) < Math.abs(x(at(best)) - px) ? v : best, xs[0]);
      cross.style('display', null).attr('x1', x(at(nearest))).attr('x2', x(at(nearest)));
      const from = timeValue(nearest), near = marks.filter((e) => e.x >= from && e.x < from + span);
      const rows = series.map((s, i) => ({ label: s.name, color: seriesColor(s.name, i), raw: (s.points.find((p) => p[0] === nearest) || [])[1] }))
        .map((r) => ({ ...r, value: r.raw === null || r.raw === undefined ? 'not shown' : fmt(r.raw, unit) }));
      showTip(event, String(nearest), rows.concat(near.map((e) => ({ label: `${e.date}: ${e.label}`, value: '' }))));
    }).on('pointerleave', () => { cross.style('display', 'none'); hideTip(); });
}
const LANES = 4;
const markersTop = (markers) => (!markers || markers === 'none' ? 14 : 14 + LANES * 12);

/* Horizontal bars, one measure. rows = [{label, value, sub}] already ordered. */
export function barChart(host, { rows, unit, valueLabel, color }) {
  const W = Math.max(360, host.clientWidth || 640), rowH = 30, left = Math.min(240, Math.max(110, d3.max(rows, (r) => String(r.label).length) * 6.6 + 12));
  const H = rows.length * rowH + 8, right = 70;
  const x = d3.scaleLinear().domain([0, d3.max(rows, (r) => r.value) || 1]).range([left, W - right]);
  const svg = d3.select(host).append('svg').attr('viewBox', `0 0 ${W} ${H}`).attr('role', 'img').attr('aria-label', valueLabel);
  const fill = color || css('--s1'), ink = css('--ink');
  const g = svg.selectAll('g').data(rows).join('g').attr('transform', (_, i) => `translate(0,${i * rowH + 4})`);
  g.append('text').attr('x', left - 8).attr('y', 15).attr('text-anchor', 'end').text((d) => String(d.label).length > 34 ? String(d.label).slice(0, 33) + '…' : d.label);
  g.append('path').attr('fill', (d) => d.color || fill).attr('d', (d) => {  // square at the baseline, 4px rounded at the data end
    const w = Math.max(0, x(d.value || 0) - left), h = 16, r = Math.min(4, w);
    return `M${left},3h${w - r}a${r},${r} 0 0 1 ${r},${r}v${h - 2 * r}a${r},${r} 0 0 1 ${-r},${r}h${-(w - r)}z`;
  });
  g.append('text').attr('x', (d) => x(d.value || 0) + 6).attr('y', 15).style('fill', ink).text((d) => d.value === null || d.value === undefined ? 'not shown' : fmt(d.value, unit, true));
  g.append('rect').attr('x', 0).attr('y', 0).attr('width', W).attr('height', rowH - 4).attr('fill', 'transparent')
    .on('pointermove', (event, d) => showTip(event, String(d.label), [{ label: valueLabel, value: d.value === null || d.value === undefined ? 'not shown' : fmt(d.value, unit) }]
      .concat(d.sub ? [{ label: d.sub, value: '' }] : [])))
    .on('pointerleave', hideTip);
}

export function legendKeys(names, kind = 'ln') {
  return el('ul', { class: 'keys' }, names.map((name, i) => el('li', {}, [el('span', { class: kind, style: `--k:${seriesColor(name, i)}` }), document.createTextNode(String(name))])));
}
