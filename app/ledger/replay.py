from datetime import datetime, timezone
import json
from time import perf_counter

from app.configuration import current_rules
from app.diagnostics import RampError
from app.meters.catalog import fleet
from app.shift import compute
from .manifest import digest,lock,serialize


def shift_key(input_hashes):
    return lock({'inputs':input_hashes})


def identity(manifest):
    return manifest.get('shift') or shift_key(manifest['input_sha256'])


def archive(book, inputs, rules):
    begin = perf_counter()
    catalog, reference = fleet()
    report = compute(inputs,catalog=catalog,rules=rules)
    hashes = {name:digest(data) for name,data in inputs.items()}
    setup = {'rules':rules['name'],'input_sha256':hashes,
             'fleet_sha256':reference,'parameters':{'reference_temp_c':rules['reference_temp_c'],
                                                   'thermal_coefficient':rules['thermal_coefficient'],
                                                   'mass_unit':'g','shift':'one_bundle'}}
    batch = lock(setup)
    manifest = dict(setup,batch=batch,shift=shift_key(hashes),archived_at=datetime.now(timezone.utc).isoformat(),
                    elapsed_ms=round((perf_counter()-begin)*1000),totals=report['balance']['totals'])
    with book.connection() as db:
        stored = db.execute('INSERT INTO shifts VALUES (?,?) ON CONFLICT(digest) DO NOTHING',(batch,serialize(manifest))).rowcount
        if stored:
            db.executemany('INSERT INTO stage_rows VALUES (?,?,?)',
                           [(batch,phase,serialize(values)) for phase,values in report.items()])
        saved = db.execute('SELECT manifest FROM shifts WHERE digest=?',(batch,)).fetchone()[0]
    return json.loads(saved),not bool(stored)


def replay(book, inputs, rules=None):
    manifest,reused = archive(book,inputs,rules or current_rules())
    return dict(manifest,reused=reused)


def recheck(book, batch, inputs, rules=None):
    with book.connection() as db:
        original = db.execute('SELECT manifest FROM shifts WHERE digest=?',(batch,)).fetchone()
    if original is None:
        raise RampError('NO_BATCH',404)
    if {name:digest(data) for name,data in inputs.items()}!=json.loads(original[0])['input_sha256']:
        raise RampError('SHIFT_INPUT',409)
    manifest,reused = archive(book,inputs,rules or current_rules())
    return dict(manifest,reused=reused,lineage=lineage(book,identity(manifest)))


def lineage(book, shift):
    with book.connection() as db:
        rows = db.execute('SELECT digest,manifest FROM shifts ORDER BY rowid').fetchall()
        signed = {row[0] for row in db.execute('SELECT digest FROM signatures')}
    revisions = []
    for batch,raw in rows:
        manifest = json.loads(raw)
        if identity(manifest)!=shift:
            continue
        revisions.append({'batch':batch,'revision':len(revisions)+1,'rules':manifest['rules'],
                          'parameters':manifest['parameters'],'fleet_sha256':manifest['fleet_sha256'],
                          'archived_at':manifest['archived_at'],'superseded_at':None,
                          'signed':batch in signed,'totals':manifest['totals']})
    for previous,followed in zip(revisions,revisions[1:]):
        previous['superseded_at'] = followed['archived_at']
    return {'shift':shift,'current':revisions[-1]['batch'] if revisions else None,'revisions':revisions}


def list_shifts(book):
    with book.connection() as db:
        return [json.loads(item[0]) for item in db.execute('SELECT manifest FROM shifts ORDER BY rowid DESC LIMIT 25')]


def shift(book, batch):
    with book.connection() as db:
        manifest = db.execute('SELECT manifest FROM shifts WHERE digest=?',(batch,)).fetchone()
        if manifest is None:
            raise RampError('NO_BATCH',404)
        rows = db.execute('SELECT phase,body FROM stage_rows WHERE digest=?',(batch,)).fetchall()
    manifest = json.loads(manifest[0])
    return {'manifest':manifest,'stages':{phase:json.loads(body) for phase,body in rows},
            'lineage':lineage(book,identity(manifest))}
