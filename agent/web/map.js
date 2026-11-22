/* The map: state outlines always, ZIP outlines when zoomed in, centers as squares and HCPs as circles at
   their geocoded locations, in the fixed community colours. `show` replaces everything on the map with what
   one directive keeps, flies to it, and rebuilds the legend. No street basemap and no outside tile service. */

import { css, el, hideTip, showTip } from './charts.js';

const maplibregl = window.maplibregl;
const EMPTY = { type: 'FeatureCollection', features: [] };
const ZIP_ZOOM = 7.5;
const UNASSIGNED = '#8a8f98';

function squareImage(fill, stroke, hollow) {
  const size = 28, canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = hollow ? 'rgba(0,0,0,0)' : fill;
  ctx.strokeStyle = stroke;
  ctx.lineWidth = hollow ? 3.5 : 2.5;
  ctx.beginPath();
  ctx.roundRect(3, 3, size - 6, size - 6, 4);
  ctx.fill();
  ctx.stroke();
  return ctx.getImageData(0, 0, size, size);
}

/* People at the same practice share coordinates; spread them on a small spiral so each can be seen and hovered. */
function fanOut(points) {
  const seen = new Map();
  return points.map((p) => {
    const key = `${p.lat.toFixed(5)},${p.lon.toFixed(5)}`;
    const n = seen.get(key) || 0;
    seen.set(key, n + 1);
    if (!n) return p;
    const angle = n * 2.39996, radius = 0.0022 * Math.sqrt(n);
    return { ...p, lat: p.lat + radius * Math.sin(angle), lon: p.lon + radius * Math.cos(angle) / Math.cos(p.lat * Math.PI / 180) };
  });
}

function arc(a, b, bend) {
  const dx = b[0] - a[0], dy = b[1] - a[1], mx = (a[0] + b[0]) / 2 - dy * bend, my = (a[1] + b[1]) / 2 + dx * bend, out = [];
  for (let i = 0; i <= 14; i += 1) {
    const t = i / 14, u = 1 - t;
    out.push([u * u * a[0] + 2 * u * t * mx + t * t * b[0], u * u * a[1] + 2 * u * t * my + t * t * b[1]]);
  }
  return out;
}

export async function createMap(container, meta, handlers) {
  const scheme = matchMedia('(prefers-color-scheme: dark)');
  const palette = () => new Map(meta.communities.map((c) => [c.community_id, scheme.matches ? c.color_dark : c.color_light]));
  let colors = palette();
  const colorOf = (id) => (id === null || id === undefined ? UNASSIGNED : colors.get(id) || UNASSIGNED);
  const map = new maplibregl.Map({
    container, attributionControl: false, dragRotate: false, pitchWithRotate: false, minZoom: 2.3, maxZoom: 14,
    center: [-96.5, 38.6], zoom: 3.3,
    style: { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': css('--map-water') } }] },
  });
  map.touchZoomRotate.disableRotation();
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-left');
  await new Promise((resolve) => map.on('load', resolve));

  const ink = css('--ink'), surface = css('--surface');
  for (const c of meta.communities) map.addImage(`sq-${c.community_id}`, squareImage(colorOf(c.community_id), surface, false));
  map.addImage('sq-none', squareImage(UNASSIGNED, UNASSIGNED, true));
  const states = await fetch('/api/geo/states').then((r) => (r.ok ? r.json() : EMPTY)).catch(() => EMPTY);
  map.addSource('states', { type: 'geojson', data: states });
  map.addSource('zcta', { type: 'geojson', data: EMPTY });
  map.addSource('links', { type: 'geojson', data: EMPTY });
  map.addSource('hcps', { type: 'geojson', data: EMPTY });
  map.addSource('centers', { type: 'geojson', data: EMPTY });
  map.addLayer({ id: 'states-fill', type: 'fill', source: 'states', paint: { 'fill-color': css('--map-land') } });
  map.addLayer({ id: 'zcta-line', type: 'line', source: 'zcta', minzoom: ZIP_ZOOM, paint: { 'line-color': css('--map-zip'), 'line-width': 0.7 } });
  map.addLayer({ id: 'states-line', type: 'line', source: 'states', paint: { 'line-color': css('--map-state'), 'line-width': ['interpolate', ['linear'], ['zoom'], 3, 0.7, 8, 1.4] } });
  map.addLayer({ id: 'links', type: 'line', source: 'links', layout: { 'line-cap': 'round' },
    paint: { 'line-color': ['get', 'color'], 'line-width': ['get', 'width'], 'line-opacity': ['get', 'opacity'] } });
  map.addLayer({ id: 'hcps', type: 'circle', source: 'hcps', paint: {
    'circle-radius': ['get', 'radius'], 'circle-color': ['get', 'color'], 'circle-opacity': ['case', ['get', 'hollow'], 0, 0.92],
    'circle-stroke-color': ['case', ['get', 'hollow'], UNASSIGNED, surface], 'circle-stroke-width': ['case', ['get', 'hollow'], 1.4, 1] } });
  map.addLayer({ id: 'rings', type: 'circle', source: 'hcps', filter: ['==', ['get', 'label'], true], paint: {
    'circle-radius': ['+', ['get', 'radius'], 3.5], 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': ink, 'circle-stroke-width': 1.6 } });
  map.addLayer({ id: 'centers', type: 'symbol', source: 'centers', layout: {
    'icon-image': ['get', 'icon'], 'icon-size': ['get', 'scale'], 'icon-allow-overlap': true, 'icon-ignore-placement': true } });

  let labels = [], current = null, zipTimer = null;
  const hover = (layer) => {
    map.on('mousemove', layer, (event) => {
      const p = event.features[0].properties;
      map.getCanvas().style.cursor = 'pointer';
      const rows = [{ label: p.place || '', value: '' }, { label: p.community || 'Not attached to a community', value: '' }];
      if (p.kind === 'hcp' && p.division) rows.push({ label: p.division, value: '' });
      showTip(event.originalEvent, p.name, rows.filter((r) => r.label));
    });
    map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; hideTip(); });
    map.on('click', layer, (event) => handlers.onPoint(event.features[0].properties));
  };
  hover('hcps');
  hover('centers');

  async function loadZips() {
    if (map.getZoom() < ZIP_ZOOM) return;
    const b = map.getBounds();
    const box = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(4)).join(',');
    const data = await fetch(`/api/geo/zcta?bbox=${box}`).then((r) => (r.ok ? r.json() : EMPTY)).catch(() => EMPTY);
    map.getSource('zcta').setData(data.features ? data : EMPTY);
  }
  /* Names never overprint: a name that would cover an earlier one moves up a line, up to five lines; past that it
     is hidden until the map is zoomed in. Hovering a point always names it. */
  function declutter() {
    const kept = [];
    const covers = (r) => kept.some((k) => r.left < k.right && r.right > k.left && r.top < k.bottom && r.bottom > k.top);
    for (const marker of labels) {
      const node = marker.getElement();
      let level = 0, box;
      do {
        marker.setOffset([0, -10 - level * 23]);
        box = node.getBoundingClientRect();
        level += 1;
      } while (covers(box) && level < 6);
      const hidden = covers(box);
      node.style.visibility = hidden ? 'hidden' : '';
      if (!hidden) kept.push(box);
    }
  }
  map.on('moveend', () => { declutter(); clearTimeout(zipTimer); zipTimer = setTimeout(loadZips, 250); });

  const DIVISIONS = { core: 'Core HCP', affiliates: 'Affiliate HCP', referring: 'Referring HCP', other: 'Other hematology-oncology HCP' };
  const communityName = new Map(meta.communities.map((c) => [c.community_id, c.short_label]));

  function show(data, { fly = true } = {}) {
    current = data;
    const points = fanOut(data.points);
    const at = new Map(points.map((p) => [p.id, [p.lon, p.lat]]));
    const people = points.filter((p) => p.kind === 'hcp'), places = points.filter((p) => p.kind === 'center');
    const maxPerson = Math.max(...people.map((p) => p.size || 0), 1e-9), maxPlace = Math.max(...places.map((p) => p.size || 0), 1e-9);
    const props = (p) => ({ id: p.id, kind: p.kind, name: p.name, place: p.place, label: !!p.label,
      community: p.community_id === null || p.community_id === undefined ? '' : (communityName.get(p.community_id > 13 ? 14 : p.community_id) || communityName.get(p.community_id) || ''),
      division: DIVISIONS[p.division_group] || '' });
    map.getSource('hcps').setData({ type: 'FeatureCollection', features: people.map((p) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
      properties: { ...props(p), color: colorOf(p.community_id), hollow: p.community_id === null || p.community_id === undefined,
        radius: p.size ? 3.2 + 9 * Math.sqrt(p.size / maxPerson) : 3.2 } })) });
    map.getSource('centers').setData({ type: 'FeatureCollection', features: places.map((p) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
      properties: { ...props(p), icon: p.community_id === null || p.community_id === undefined ? 'sq-none' : `sq-${p.community_id}`,
        scale: (p.label || p.focus ? 0.62 : 0.42) + 0.42 * Math.sqrt((p.size || 0) / maxPlace) } })) });
    const kind = data.spec.edges, community = new Map(points.map((p) => [p.id, p.community_id]));
    const maxWeight = Math.max(...data.links.map((l) => l.weight || 0), 1e-9), muted = css('--ink-2');
    map.getSource('links').setData({ type: 'FeatureCollection', features: data.links.filter((l) => at.has(l.source) && at.has(l.target)).map((l, i) => {
      const same = community.get(l.source) === community.get(l.target) && community.get(l.source) !== null && community.get(l.source) !== undefined;
      const color = kind === 'coauthor' ? (same ? colorOf(community.get(l.source)) : muted) : kind === 'referral' ? colorOf(community.get(l.target)) : muted;
      return { type: 'Feature', geometry: { type: 'LineString', coordinates: arc(at.get(l.source), at.get(l.target), kind === 'coauthor' ? (i % 2 ? 0.16 : -0.16) : 0.08) },
        properties: { color, width: 0.5 + 3.6 * Math.sqrt((l.weight || 0) / maxWeight), opacity: kind === 'coauthor' ? (same ? 0.42 : 0.2) : 0.38 } };
    }) });
    map.setPaintProperty('links', 'line-dasharray', kind === 'payments' ? [2, 2] : [1, 0]);
    labels.forEach((m) => m.remove());
    // Largest first, so the leading name keeps the place next to its point when names have to stack.
    labels = points.filter((p) => p.label || p.focus).sort((a, b) => (b.size || 0) - (a.size || 0)).slice(0, 28)
      .map((p) => new maplibregl.Marker({ element: el('div', { class: 'map-label', text: p.name }), anchor: 'bottom', offset: [0, -10] })
        .setLngLat([p.lon, p.lat]).addTo(map));
    setTimeout(declutter, 60);
    if (fly && data.bbox) {
      const side = document.getElementById('legend').offsetWidth + 24;
      map.fitBounds([[data.bbox[0], data.bbox[1]], [data.bbox[2], data.bbox[3]]], { padding: { top: 56, bottom: 30, left: 40, right: Math.min(side, container.clientWidth / 2) }, duration: 1300, maxZoom: 11 });
    }
    drawLegend(data);
  }

  function drawLegend(data) {
    const L = data.legend, host = document.getElementById('legend');
    const item = (key, text) => el('li', {}, [key, document.createTextNode(text)]);
    const nodes = [el('h2', { text: 'On the map' })];
    nodes.push(el('ul', {}, [item(el('span', { class: 'sw', style: `background:${css('--ink-2')}` }), 'HCP (circle)'), item(el('span', { class: 'sw sq' }), 'CAR-T center (square)'),
      item(el('span', { class: 'sw hollow' }), 'Not attached to a community')]));
    if (L.communities.length) {
      nodes.push(el('h2', { text: 'Community' }));
      nodes.push(el('ul', {}, L.communities.map((c) => item(el('span', { class: 'sw', style: `background:${colorOf(c.community_id)}` }), c.short_label))));
    }
    const extra = [];
    if (L.highlight) extra.push(item(el('span', { class: 'sw ring' }), L.highlight));
    if (L.links) { extra.push(item(el('span', { class: 'ln' + (L.links.kind === 'payments' ? ' dashed' : '') }), L.links.label)); extra.push(item(el('span', {}), L.links.width)); }
    if (L.size) extra.push(item(el('span', {}), L.size));
    if (extra.length) nodes.push(el('ul', {}, extra));
    if (L.note) nodes.push(el('p', { class: 'note', text: L.note }));
    const c = data.counts;
    nodes.push(el('p', { class: 'count', text: `${c.hcps.toLocaleString()} HCPs · ${c.centers.toLocaleString()} centers` + (c.links ? ` · ${c.links.toLocaleString()} links` : '')
      + (c.not_placed ? ` · ${c.not_placed} without a location` : '') }));
    host.replaceChildren(...nodes);
  }

  /* When the reader's light or dark setting changes, repaint the base, the square icons and whatever is on the map. */
  scheme.addEventListener('change', () => {
    colors = palette();
    const paper = css('--surface');
    map.setPaintProperty('bg', 'background-color', css('--map-water'));
    map.setPaintProperty('states-fill', 'fill-color', css('--map-land'));
    map.setPaintProperty('zcta-line', 'line-color', css('--map-zip'));
    map.setPaintProperty('states-line', 'line-color', css('--map-state'));
    map.setPaintProperty('hcps', 'circle-stroke-color', ['case', ['get', 'hollow'], UNASSIGNED, paper]);
    map.setPaintProperty('rings', 'circle-stroke-color', css('--ink'));
    for (const c of meta.communities) map.updateImage(`sq-${c.community_id}`, squareImage(colorOf(c.community_id), paper, false));
    if (current) show(current, { fly: false });
  });

  return { show, resize: () => map.resize(), current: () => current };
}
