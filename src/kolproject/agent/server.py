"""Local web server for the CAR-T KOL view agent.

    uv run kolproject-agent-serve            # http://127.0.0.1:8790

It serves the interface, the map layers, and a chat endpoint that streams the agent's steps, the map and the
panels as each is ready, and then the final view. It reads the pack read-only and talks to the model through the local Ollama server; nothing leaves
this machine.
"""
from __future__ import annotations

import argparse
import json
import threading
import traceback
import urllib.error

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import loop, mapdata, prompt
from .packbase import connect
from .paths import DB, MANIFEST, WEB

HOST, PORT = '127.0.0.1', 8790
EXAMPLES = [
    'Show me the Moffitt orbit and who leads it.',
    'Which centers anchor the Penn / Abramson orbit?',
    'Where does MD Anderson get its shared patients from, and how has that changed since 2017?',
    'Payments naming Yescarta to the Stanford orbit by year, with approval dates marked.',
    'Prescribing of oral lymphoma drugs by the City of Hope orbit, 2017 to 2024.',
    'Compare the Mayo Clinic and Memorial Sloan Kettering orbits.',
    'When was Breyanzi approved, and for what?',
    'What can I ask?',
]

app = FastAPI(title='CAR-T KOL Navigator', docs_url=None, redoc_url=None)
sessions = {}
model_lock = threading.Lock()  # one question at a time: the model runs on this machine


class Question(BaseModel):
    question: str
    session: str = 'default'


class MapRequest(BaseModel):
    spec: dict


def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args)]


@app.get('/api/meta')
def meta():
    con = connect(readonly=True)
    try:
        manifest = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {}
        return {
            'communities': rows(con, 'select community_id, label, short_label, kind, color_light, color_dark, region, core_members, attached_hcps, centers from dim_community order by community_id'),
            'products': rows(con, 'select brand, generic, short_name, company_group, target, lymphoma_product, first_us_approval from dim_product order by first_us_approval'),
            'events': rows(con, "select date, brand, event_type, label, lymphoma from product_event where verified = 1 or (select count(*) from product_event where verified = 1) = 0 order by date"),
            'caveats': prompt.CAVEATS, 'examples': EXAMPLES,
            'data_as_of': manifest.get('data_as_of'), 'products_verified': manifest.get('products_verified', False),
            'notice': 'Academic, non-commercial demonstration.',
        }
    finally:
        con.close()


@app.get('/api/map/default')
def map_default():
    return mapdata.default_view()


@app.post('/api/map')
def map_view(request: MapRequest):
    con = connect(readonly=True)
    try:
        spec = {'show': 'focus_only', 'edges': 'none', 'highlight': 'none', **request.spec}
        problem = mapdata.validate(con, spec)
        if problem:
            raise HTTPException(400, problem)
        return mapdata.view(con, spec)
    finally:
        con.close()


@app.get('/api/geo/states')
def geo_states():
    from . import geo
    return JSONResponse(geo.states_geojson(), headers={'Cache-Control': 'max-age=86400'})


@app.get('/api/geo/zcta')
def geo_zcta(bbox: str):
    from . import geo
    try:
        min_lon, min_lat, max_lon, max_lat = (float(x) for x in bbox.split(','))
    except ValueError:
        raise HTTPException(400, 'bbox must be min_lon,min_lat,max_lon,max_lat') from None
    if max_lon - min_lon > 4 or max_lat - min_lat > 4:
        return {'type': 'FeatureCollection', 'features': [], 'too_wide': True}
    return geo.zcta_features(min_lon, min_lat, max_lon, max_lat)


@app.post('/api/chat')
def chat(request: Question):
    """Stream one JSON object per line: steps while the agent works, then the final view."""
    question = request.question.strip()[:600]
    if not question:
        raise HTTPException(400, 'Ask a question.')

    def stream():
        with model_lock:
            try:
                for event in loop.events(question, memory=sessions.get(request.session)):
                    if event['type'] == 'final':
                        sessions[request.session] = event.pop('memory')
                    yield json.dumps(event, ensure_ascii=False, default=str) + '\n'
            except urllib.error.URLError:
                yield json.dumps({'type': 'error', 'message': f'The model server is not answering. Start Ollama (model {loop.MODEL}) and ask again.'}) + '\n'
            except Exception as error:  # the interface must always get a final line it can show
                traceback.print_exc()
                yield json.dumps({'type': 'error', 'message': f'The agent could not finish this answer ({type(error).__name__}). Ask again or rephrase the question.'}) + '\n'
    return StreamingResponse(stream(), media_type='application/x-ndjson')


@app.post('/api/reset')
def reset(request: Question):
    sessions.pop(request.session, None)
    return {'ok': True}


class FreshFiles(StaticFiles):
    """Interface files are checked against the disk on every load, so an edit shows on the next reload."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers['Cache-Control'] = 'no-cache'
        return response


@app.get('/')
def index():
    return FileResponse(WEB / 'index.html', headers={'Cache-Control': 'no-cache'})


app.mount('/web', FreshFiles(directory=WEB), name='web')


def warm_model():
    """Load the model and have it read the prompt before the first question, which then answers at full speed."""
    with model_lock:
        seconds = loop.warm()
    print(f'Model {loop.MODEL} is loaded and has read the prompt ({seconds} s).' if seconds is not None
          else f'The model server is not answering at {loop.OLLAMA}. Start Ollama and pull {loop.MODEL}; questions will fail until then.', flush=True)


def main():
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--port', type=int, default=PORT)
    args = parser.parse_args()
    if not DB.exists():
        raise SystemExit('The data pack is missing. Build it first: uv run kolproject-agent-pack')
    threading.Thread(target=warm_model, daemon=True).start()
    uvicorn.run(app, host=HOST, port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
