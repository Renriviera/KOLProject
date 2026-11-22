/* Page logic: send a question and show the answer as it is made. The agent's steps stream in; the map moves
   when the agent knows what the question is about, each panel appears when its query has run, and the written
   answer arrives sentence by sentence, each one already checked against the query results.
   The final view is the whole checked answer: it brings the caveats and replaces anything drawn before it
   that it does not confirm. */

import { el } from './charts.js';
import { createMap } from './map.js';
import { renderPanels } from './panels.js';

const $ = (id) => document.getElementById(id);
const session = 's' + Math.random().toString(36).slice(2, 10);
const state = { meta: null, map: null, spec: null, busy: false, pendingMap: null, last: null, question: '', live: null, mapData: null };

async function post(url, body) {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || response.statusText);
  return response;
}

function setBusy(busy) {
  state.busy = busy;
  $('send').disabled = busy;
  $('send').textContent = busy ? 'Working' : 'Ask';
}

function step(text, kind = 'now') {
  [...$('steps').children].forEach((li) => li.classList.remove('now'));
  $('steps').append(el('li', { class: kind, text }));
}

function syncTools(spec) {
  state.spec = spec;
  $('edges').value = spec.edges;
  $('year-wrap').hidden = spec.edges !== 'referral';
  if (spec.edges === 'referral') $('year').value = String(spec.year || 2022);
}

function applyMap(data) {
  if (!data) return;
  state.mapData = data;
  syncTools(data.spec);
  if (state.map) state.map.show(data); else state.pendingMap = data;  // the map may still be starting; draw as soon as it is ready
}

/* The first piece of a new answer clears the last answer, so its panels never sit under a map that has moved on. */
function live() {
  if (state.live) return state.live;
  state.live = { results: {}, map: '', before: state.mapData };
  state.last = null;
  $('answer').classList.remove('writing');
  $('answer').classList.add('pending');
  $('headline').textContent = state.question;
  $('text').textContent = 'Writing the answer…';
  $('choices').replaceChildren();
  $('caveats').replaceChildren();
  renderPanels($('panels'), { panels: [] }, {}, state.meta, ask);
  $('canvas').scrollTop = 0;
  return state.live;
}

function showMapEarly(event) {
  const now = live(), spec = JSON.stringify(event.map_data.spec);
  if (spec === now.map) return;
  now.map = spec;
  applyMap(event.map_data);
}

function showPanelsEarly(event) {
  const now = live();
  Object.assign(now.results, event.results);
  renderPanels($('panels'), { panels: event.panels }, now.results, state.meta, ask);
}

/* Sentences of the answer that are complete and checked, while the model is still writing the rest. */
function showAnswerEarly(event) {
  live();
  if (event.headline) {
    $('headline').textContent = event.headline;
    $('answer').classList.remove('pending');
  }
  $('text').textContent = (event.text ? event.text + ' ' : '') + '…';
  $('answer').classList.add('writing');
}

function applyView(final) {
  const { view, results } = final;
  const early = state.live || { map: '' };
  state.live = null;
  if (final.map_data && JSON.stringify(final.map_data.spec) !== early.map) applyMap(final.map_data);  // already there: do not move the map twice
  else if (!final.map_data && early.map) applyMap(early.before);  // the map moved early, but the answer shows nothing from the database: put it back
  $('answer').classList.remove('pending', 'writing');
  $('headline').textContent = view.answer.headline;
  $('text').textContent = view.answer.text;
  state.last = final;
  $('choices').replaceChildren(...(view.ask ? view.ask.choices.map((c) => el('button', { class: 'chip', type: 'button', text: c, onclick: () => ask(`${state.question} I mean ${c}.`) })) : []));  // the choice goes back with the question it answers
  renderPanels($('panels'), view, results, state.meta, ask);
  $('caveats').replaceChildren(...view.answer.caveats.map((code) => el('li', { text: state.meta.caveats[code] || code })));
  if (!early.results) $('canvas').scrollTop = 0;  // the user may already be reading the panels
}

function failed(message) {
  step(message, 'error');
  if (!state.live) return;
  if (state.live.map) applyMap(state.live.before);
  state.live = null;
  $('answer').classList.remove('pending', 'writing');
  $('text').textContent = message;
}

async function ask(question) {
  if (state.busy || !question.trim()) return;
  $('q').value = question;
  state.question = question.trim();
  state.live = null;
  $('steps').replaceChildren();
  $('choices').replaceChildren();
  $('examples').hidden = true;
  setBusy(true);
  step('Reading the question');
  try {
    const response = await post('/api/chat', { question, session });
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, cut).trim();
        buffer = buffer.slice(cut + 1);
        if (!line) continue;
        const event = JSON.parse(line);
        if (event.type === 'step') step(event.label);
        else if (event.type === 'map') showMapEarly(event);
        else if (event.type === 'panels') showPanelsEarly(event);
        else if (event.type === 'answer') showAnswerEarly(event);
        else if (event.type === 'error') failed(event.message);
        else if (event.type === 'final') { [...$('steps').children].forEach((li) => li.classList.remove('now')); applyView(event); }
      }
    }
  } catch (error) {
    failed(`Could not reach the agent: ${error.message}`);
  } finally {
    setBusy(false);
  }
}

async function changeMap(patch) {
  const spec = { ...(state.spec || { focus: { kind: 'usa', ids: [] }, show: 'all', highlight: 'none' }), ...patch };
  if (spec.edges !== 'referral') delete spec.year;
  delete spec.unchanged;
  try {
    applyMap(await (await post('/api/map', { spec })).json());
  } catch (error) {
    step(error.message, 'error');
  }
}

function splitter() {
  const wrap = $('map-wrap'), bar = $('splitter');
  const set = (px) => { wrap.style.height = Math.max(140, Math.min(window.innerHeight - 220, px)) + 'px'; if (state.map) state.map.resize(); };
  bar.addEventListener('pointerdown', (down) => {
    bar.setPointerCapture(down.pointerId);
    const start = wrap.getBoundingClientRect().height - down.clientY;
    const move = (event) => set(start + event.clientY);
    bar.addEventListener('pointermove', move);
    bar.addEventListener('pointerup', () => bar.removeEventListener('pointermove', move), { once: true });
  });
  bar.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') { set(wrap.getBoundingClientRect().height + (event.key === 'ArrowDown' ? 40 : -40)); event.preventDefault(); }
  });
}

async function start() {
  state.meta = await fetch('/api/meta').then((r) => r.json());
  $('notice').textContent = `${state.meta.notice} Data as of ${state.meta.data_as_of || 'the latest build'}.` + (state.meta.products_verified ? '' : ' Product dates not yet verified.');
  $('examples').replaceChildren(...state.meta.examples.map((text) => el('button', { class: 'chip', type: 'button', text, onclick: () => ask(text) })));
  $('year').replaceChildren(...[2017, 2018, 2019, 2020, 2021, 2022].map((y) => el('option', { value: y, text: String(y) })));
  $('ask').addEventListener('submit', (event) => { event.preventDefault(); ask($('q').value); });
  // Questions work as soon as the page is up; the map joins when its first frame has drawn.
  createMap($('map'), state.meta, {
    onPoint: (p) => ask(p.kind === 'center' ? `Tell me about ${p.name}.` : `Show the profile of ${p.name}.`),
  }).then((map) => {
    state.map = map;
    if (state.pendingMap) { map.show(state.pendingMap); state.pendingMap = null; }
  }).catch((error) => step(`The map could not start: ${error.message}`, 'error'));
  fetch('/api/map/default').then((r) => r.json()).then((data) => { if (!state.spec) applyMap(data); });
  $('edges').addEventListener('change', (event) => changeMap({ edges: event.target.value, show: state.spec && state.spec.focus.kind !== 'usa' ? 'focus_and_linked' : 'all' }));
  $('year').addEventListener('change', (event) => changeMap({ year: Number(event.target.value) }));
  $('everything').addEventListener('click', () => changeMap({ focus: { kind: 'usa', ids: [] }, show: 'all', edges: 'none', highlight: 'none' }));
  splitter();
  // Chart colours are read when a chart is drawn, so a change of light or dark setting redraws the panels.
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (state.last) renderPanels($('panels'), state.last.view, state.last.results, state.meta, ask, true);
  });
}

start().catch((error) => step(`The page could not start: ${error.message}`, 'error'));
