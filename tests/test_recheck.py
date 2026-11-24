from concurrent.futures import ThreadPoolExecutor
import json
from app.bundles import sample_set
from app.ledger.replay import lineage,list_shifts,recheck,replay

NEW_RULES = {'name':'ramp-1.1','thermal_coefficient':'0.0012','reference_temp_c':15}


def update_rules(monkeypatch):
    monkeypatch.setenv('FUEL_RULESET','ramp-1.1')
    monkeypatch.setenv('FUEL_THERMAL_COEFFICIENT','0.0012')


def archived(client):
    return client.post('/api/shifts',json={'sample':'dirty'}).json


def test_recheck_keeps_old_slip_verifiable_and_posts_new_shortfall(client,monkeypatch):
    first = archived(client)
    mark = client.post(f"/api/shifts/{first['batch']}/sign").json
    before = client.get(f"/api/shifts/{first['batch']}/export").json
    assert before['verified'] and first['totals']['difference_g']==1400

    update_rules(monkeypatch)
    response = client.post(f"/api/shifts/{first['batch']}/recheck",json={'sample':'dirty'})
    assert response.status_code==201
    new = response.json
    assert new['batch']!=first['batch'] and new['shift']==first['shift']
    assert (new['rules'],new['parameters']['thermal_coefficient'])==('ramp-1.1','0.0012')
    assert new['totals']['difference_g']==1720

    after = client.get(f"/api/shifts/{first['batch']}/export").json
    assert after['verified'] and after['checksum']==mark['checksum'] and after['slip']==before['slip']

    stages = client.get(f"/api/shifts/{new['batch']}").json['stages']
    t07 = next(row for row in stages['balance']['receipts'] if row['ticket']=='T07')
    assert (t07['state'],t07['variance_g'],t07['allowance_g'])==('SIGN_DIFF',360,198)


def test_recheck_retry_adds_no_new_result(client,ramp,monkeypatch):
    first = archived(client)
    update_rules(monkeypatch)
    url = f"/api/shifts/{first['batch']}/recheck"
    one = client.post(url,json={'sample':'dirty'})
    two = client.post(url,json={'sample':'dirty'})
    assert (one.status_code,two.status_code)==(201,200)
    assert two.json['batch']==one.json['batch'] and two.json['reused']
    assert len(list_shifts(ramp.book))==2

    monkeypatch.delenv('FUEL_THERMAL_COEFFICIENT')
    monkeypatch.delenv('FUEL_RULESET')
    back = client.post(url,json={'sample':'dirty'})
    assert back.json['batch']==first['batch'] and back.json['reused']
    assert len(list_shifts(ramp.book))==2


def test_lineage_tracks_basis_and_applicable_time(client,monkeypatch):
    first = archived(client)
    client.post(f"/api/shifts/{first['batch']}/sign")
    update_rules(monkeypatch)
    second = client.post(f"/api/shifts/{first['batch']}/recheck",json={'sample':'dirty'}).json

    found = client.get(f"/api/shifts/{first['batch']}").json['lineage']
    assert found['shift']==first['shift'] and found['current']==second['batch']
    one,two = found['revisions']
    assert (one['revision'],two['revision'])==(1,2)
    assert one['batch']==first['batch'] and two['batch']==second['batch']
    assert one['signed'] and not two['signed']
    assert one['superseded_at']==two['archived_at'] and two['superseded_at'] is None
    assert (one['rules'],one['parameters']['thermal_coefficient'])==('ramp-1.0','0.0008')
    assert (two['rules'],two['parameters']['thermal_coefficient'])==('ramp-1.1','0.0012')
    assert one['fleet_sha256']==two['fleet_sha256']
    assert client.get(f"/api/shifts/{second['batch']}").json['lineage']==found


def test_recheck_rejects_bundle_of_another_shift(client):
    first = archived(client)
    result = client.post(f"/api/shifts/{first['batch']}/recheck",json={'sample':'clean'})
    assert (result.status_code,result.json['error'])==(409,'SHIFT_INPUT')


def test_recheck_unknown_batch(client):
    result = client.post('/api/shifts/no-such/recheck',json={'sample':'dirty'})
    assert (result.status_code,result.json['error'])==(404,'NO_BATCH')


def test_concurrent_recheck_commits_one_revision(ramp):
    first = replay(ramp.book,sample_set('dirty'))
    with ThreadPoolExecutor(5) as pool:
        results = list(pool.map(lambda _:recheck(ramp.book,first['batch'],sample_set('dirty'),NEW_RULES),range(7)))
    assert sum(not row['reused'] for row in results)==1
    assert len(lineage(ramp.book,first['shift'])['revisions'])==2


def test_legacy_manifest_without_shift_key_still_lineages(ramp):
    first = replay(ramp.book,sample_set('dirty'))
    with ramp.book.connection() as db:
        manifest = json.loads(db.execute('SELECT manifest FROM shifts WHERE digest=?',(first['batch'],)).fetchone()[0])
        manifest.pop('shift')
        db.execute('UPDATE shifts SET manifest=? WHERE digest=?',(json.dumps(manifest),first['batch']))
    result = recheck(ramp.book,first['batch'],sample_set('dirty'),NEW_RULES)
    assert result['lineage']['current']==result['batch']
    assert [row['revision'] for row in result['lineage']['revisions']]==[1,2]


def test_sign_page_offers_recheck_and_lineage(client):
    archived(client)
    page = client.get('/ops/sign').get_data(as_text=True)
    assert '同班次口径沿革' in page and '按现行规则复算本班次' in page
