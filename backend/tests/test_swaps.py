"""换装履历领域规则端到端测试（SQLite，不依赖 Redis/Postgres）。

运行：cd backend && python -m pytest tests/test_swaps.py -q
"""
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

TEST_DB = os.path.join(os.path.dirname(__file__), 'test_swap.db')
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)
os.environ['DATABASE_URL'] = f'sqlite:///{TEST_DB}'
os.environ['SEED_DEMO'] = 'false'
os.environ['JWT_SECRET'] = 'test-secret-0123456789abcdef'

from src.main import app  # noqa: E402
from src.config.database import Session, engine  # noqa: E402
from src.models import User, Vehicle, Component, SwapOrder, SwapEvent, Dataset  # noqa: E402
from src.utils.security import hash_password  # noqa: E402
from migrations.initial import migrate  # noqa: E402


# SQLite 默认写锁会让并发用例直接报 locked；WAL + busy_timeout 让败者排队后撞上业务约束
@event.listens_for(engine, 'connect')
def _sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute('PRAGMA journal_mode=WAL')
    cur.execute('PRAGMA busy_timeout=8000')
    cur.close()


@pytest.fixture(scope='session')
def client():
    migrate()
    with Session() as db:
        if not db.scalar(select(User).where(User.email == 'admin@demo.local')):
            db.add(User(email='admin@demo.local', name='管理员', role='admin',
                        password=hash_password('Demo123!')))
        if not db.scalar(select(User).where(User.email == 'viewer@demo.local')):
            db.add(User(email='viewer@demo.local', name='观察员', role='viewer',
                        password=hash_password('Demo123!')))
        db.commit()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope='session')
def auth(client):
    r = client.post('/auth/login', json={'email': 'admin@demo.local', 'password': 'Demo123!'})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['access_token']}


@pytest.fixture(scope='session')
def viewer_headers(client):
    r = client.post('/auth/login', json={'email': 'viewer@demo.local', 'password': 'Demo123!'})
    return {'Authorization': 'Bearer ' + r.json()['access_token']}


VEHICLES = [('V-1', '一号车'), ('V-2', '二号车'), ('V-3', '三号车'), ('V-4', '四号车'),
            ('V-5', '五号车'), ('V-6', '六号车'), ('V-7', '七号车'), ('V-8', '八号车')]
COMPONENTS = [('C-1', 'AC-8'), ('C-2', 'AC-8'), ('C-3', 'AC-12'), ('C-4', 'AC-12'),
              ('C-5', 'AC-8'), ('C-6', 'AC-8'), ('C-7', 'AC-8'), ('C-8', 'AC-12'),
              ('C-9', 'AC-8'), ('C-10', 'AC-8'), ('C-11', 'AC-12'), ('C-12', 'AC-12')]


@pytest.fixture(scope='session')
def fleet_ready(client, auth):
    for plate, name in VEHICLES:
        client.post('/fleet/vehicles', headers=auth,
                    json={'plate_no': plate, 'name': name, 'initial_reading': 1000})
    for asset, model in COMPONENTS:
        client.post('/fleet/components', headers=auth, json={'asset_no': asset, 'model': model})


def install(client, auth, no, plate, asset, reading, note=''):
    return client.post('/fleet/handovers', headers=auth, json={
        'handover_no': no, 'note': note,
        'events': [{'action': 'install', 'plate_no': plate, 'reading': reading, 'asset_no': asset}],
    })


def remove(client, auth, no, plate, asset, reading, outcome):
    return client.post('/fleet/handovers', headers=auth, json={
        'handover_no': no,
        'events': [{'action': 'remove', 'plate_no': plate, 'reading': reading,
                    'asset_no': asset, 'outcome': outcome}],
    })


# ------------------------------------------------------------- 基础里程累加

def test_01_segment_mileage_accumulates_per_odometer_diff(client, auth, fleet_ready):
    assert install(client, auth, 'HO-001', 'V-1', 'C-1', 1000).status_code == 201
    r = remove(client, auth, 'HO-002', 'V-1', 'C-1', 5600, 'spare')
    assert r.status_code == 201, r.text
    with Session() as db:
        c = db.scalar(select(Component).where(Component.asset_no == 'C-1'))
        assert c.status == 'spare'
        assert c.accumulated_km == pytest.approx(4600.0)  # 5600-1000，而非车表读数本身
        assert c.vehicle_id is None
        assert db.scalar(select(Vehicle).where(Vehicle.plate_no == 'V-1')).current_reading == 5600
    # 第二段继续累加
    assert install(client, auth, 'HO-003', 'V-1', 'C-1', 6000).status_code == 201
    assert remove(client, auth, 'HO-004', 'V-1', 'C-1', 8300, 'spare').status_code == 201
    with Session() as db:
        c = db.scalar(select(Component).where(Component.asset_no == 'C-1'))
        assert c.accumulated_km == pytest.approx(4600 + 2300)


def test_02_failed_vs_spare_distinguished(client, auth, fleet_ready):
    assert install(client, auth, 'HO-010', 'V-2', 'C-2', 1000).status_code == 201
    assert remove(client, auth, 'HO-011', 'V-2', 'C-2', 9000, 'failed').status_code == 201
    with Session() as db:
        c = db.scalar(select(Component).where(Component.asset_no == 'C-2'))
        assert c.status == 'failed'
        assert c.failed_km == pytest.approx(8000.0)
    r = install(client, auth, 'HO-012', 'V-2', 'C-2', 9100)
    assert r.status_code == 409 and '失效' in r.json()['detail']


# ------------------------------------------------------------- 成对调换

def test_03_pair_swap_two_vehicles(client, auth, fleet_ready):
    assert install(client, auth, 'HO-020', 'V-3', 'C-3', 1000).status_code == 201
    assert install(client, auth, 'HO-021', 'V-4', 'C-4', 1000).status_code == 201
    r = client.post('/fleet/handovers', headers=auth, json={
        'handover_no': 'HO-022', 'note': '成对调换',
        'events': [
            {'action': 'remove', 'plate_no': 'V-3', 'reading': 8000, 'asset_no': 'C-3', 'outcome': 'spare'},
            {'action': 'remove', 'plate_no': 'V-4', 'reading': 7000, 'asset_no': 'C-4', 'outcome': 'spare'},
            {'action': 'install', 'plate_no': 'V-3', 'reading': 8000, 'asset_no': 'C-4'},
            {'action': 'install', 'plate_no': 'V-4', 'reading': 7000, 'asset_no': 'C-3'},
        ],
    })
    assert r.status_code == 201, r.text
    with Session() as db:
        c3 = db.scalar(select(Component).where(Component.asset_no == 'C-3'))
        c4 = db.scalar(select(Component).where(Component.asset_no == 'C-4'))
        v3 = db.scalar(select(Vehicle).where(Vehicle.plate_no == 'V-3'))
        v4 = db.scalar(select(Vehicle).where(Vehicle.plate_no == 'V-4'))
        assert c3.vehicle_id == v4.id and c4.vehicle_id == v3.id
        assert c3.accumulated_km == pytest.approx(7000.0)  # 8000-1000
        assert c4.accumulated_km == pytest.approx(6000.0)  # 7000-1000
        assert v3.current_reading == 8000 and v4.current_reading == 7000


def test_04_pair_swap_rejects_mismatched_vehicle_sets(client, auth, fleet_ready):
    r = client.post('/fleet/handovers', headers=auth, json={
        'handover_no': 'HO-023',
        'events': [
            {'action': 'remove', 'plate_no': 'V-3', 'reading': 8100, 'asset_no': 'C-4', 'outcome': 'spare'},
            {'action': 'install', 'plate_no': 'V-5', 'reading': 8100, 'asset_no': 'C-4'},
        ],
    })
    assert r.status_code == 422 and '集合不一致' in r.json()['detail']


# ------------------------------------------------------------- 并发占位约束

def test_05_one_component_one_vehicle_one_slot(client, auth, fleet_ready):
    assert install(client, auth, 'HO-030', 'V-5', 'C-5', 1000).status_code == 201
    r = install(client, auth, 'HO-031', 'V-6', 'C-5', 1200)
    assert r.status_code == 409 and '一辆车' in r.json()['detail']
    r = install(client, auth, 'HO-032', 'V-5', 'C-6', 1200)
    assert r.status_code == 409 and '一车同时只装一件' in r.json()['detail']


def test_06_concurrent_orders_only_one_wins(client, auth, fleet_ready):
    """两个并发交接抢同一空车位，只有一个成功，败者整单不动。"""
    import threading

    # V-6 此时为空，C-6 为库内待用件
    results, errors = [], []

    def fire(no):
        try:
            r = client.post('/fleet/handovers', headers=auth, json={
                'handover_no': no,
                'events': [{'action': 'install', 'plate_no': 'V-6', 'reading': 1500, 'asset_no': 'C-6'}],
            })
            results.append((no, r.status_code))
        except Exception as exc:  # pragma: no cover - 仅用于暴露并发异常
            errors.append(exc)

    t1 = threading.Thread(target=fire, args=('HO-040',))
    t2 = threading.Thread(target=fire, args=('HO-041',))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert not errors
    assert sorted(code for _, code in results) == [201, 409]
    with Session() as db:
        orders = db.scalars(select(SwapOrder).where(SwapOrder.handover_no.in_(['HO-040', 'HO-041']))).all()
        assert len([o for o in orders if o.status == 'applied']) == 1
        loser_no = next(no for no, code in results if code != 201)
        loser_events = db.scalars(
            select(SwapEvent).join(SwapOrder).where(SwapOrder.handover_no == loser_no)).all()
        assert loser_events == []  # 败者整单不动，没有任何事件落库
        c6 = db.scalar(select(Component).where(Component.asset_no == 'C-6'))
        assert c6.vehicle_id is not None and c6.installed_reading == 1500


# ------------------------------------------------------------- 迟到与整单原子性

def test_07_late_handover_rejected_keeps_current_state(client, auth, fleet_ready):
    assert install(client, auth, 'HO-050', 'V-7', 'C-7', 1000).status_code == 201
    before = client.get('/fleet/components', headers=auth).json()
    r = remove(client, auth, 'HO-051', 'V-7', 'C-7', 900, 'spare')
    assert r.status_code == 409 and '迟到' in r.json()['detail']
    after = client.get('/fleet/components', headers=auth).json()
    assert before == after  # 现状未被覆盖


def test_08_any_conflict_rolls_back_whole_order(client, auth, fleet_ready):
    """两张不同空车/占车位的上车单：第一段合法、第二段撞占位，整单回滚，第一段也不落库。"""
    # V-5 上是 C-5（占车位）；C-9、C-10 为库内待用件，V-1 为空车
    with Session() as db:
        v1_before = db.scalar(select(Vehicle).where(Vehicle.plate_no == 'V-1')).current_reading
        v5_before = db.scalar(select(Vehicle).where(Vehicle.plate_no == 'V-5')).current_reading
    r = client.post('/fleet/handovers', headers=auth, json={
        'handover_no': 'HO-053',
        'events': [
            {'action': 'install', 'plate_no': 'V-1', 'reading': 9000, 'asset_no': 'C-9'},
            {'action': 'install', 'plate_no': 'V-5', 'reading': 9000, 'asset_no': 'C-10'},
        ],
    })
    assert r.status_code == 409 and '一车同时只装一件' in r.json()['detail']
    with Session() as db:
        c9 = db.scalar(select(Component).where(Component.asset_no == 'C-9'))
        c10 = db.scalar(select(Component).where(Component.asset_no == 'C-10'))
        c5 = db.scalar(select(Component).where(Component.asset_no == 'C-5'))
        v5 = db.scalar(select(Vehicle).where(Vehicle.plate_no == 'V-5'))
        v1 = db.scalar(select(Vehicle).where(Vehicle.plate_no == 'V-1'))
        assert c9.vehicle_id is None and c10.vehicle_id is None  # 两段占位随整单回滚
        assert c5.vehicle_id == v5.id                            # 既有占位不变
        assert v1.current_reading == v1_before and v5.current_reading == v5_before  # 车表未变
        assert not db.scalar(select(SwapOrder).where(SwapOrder.handover_no == 'HO-053'))
        assert db.scalars(select(SwapEvent).join(SwapOrder)
                          .where(SwapOrder.handover_no == 'HO-053')).all() == []


# ------------------------------------------------------------- 幂等

def test_09_retry_same_handover_no_duplicate(client, auth, fleet_ready):
    payload = {
        'handover_no': 'HO-060',
        'events': [{'action': 'install', 'plate_no': 'V-1', 'reading': 9800, 'asset_no': 'C-1'}],
    }
    r1 = client.post('/fleet/handovers', headers=auth, json=payload)
    assert r1.status_code == 201 and r1.json()['created'] is True
    r2 = client.post('/fleet/handovers', headers=auth, json=payload)
    assert r2.status_code == 201 and r2.json()['created'] is False
    with Session() as db:
        assert len(db.scalars(select(SwapOrder).where(SwapOrder.handover_no == 'HO-060')).all()) == 1
        events = db.scalars(select(SwapEvent).join(SwapOrder)
                            .where(SwapOrder.handover_no == 'HO-060')).all()
        assert len(events) == 1
        assert db.scalar(select(Component).where(Component.asset_no == 'C-1')).installed_reading == 9800


def test_10_same_no_different_content_rejected(client, auth, fleet_ready):
    r = client.post('/fleet/handovers', headers=auth, json={
        'handover_no': 'HO-060',
        'events': [{'action': 'install', 'plate_no': 'V-1', 'reading': 9900, 'asset_no': 'C-1'}],
    })
    assert r.status_code == 409 and '内容不同' in r.json()['detail']


# ------------------------------------------------------------- 中断恢复一致性

def test_11_replay_state_after_interruption_is_consistent(client, auth, fleet_ready):
    """仅靠事件流水即可重放出一致占位与累计里程，不依赖进程内存状态。"""
    from src.services.swaps import state_at
    components, vehicles = state_at(Session(), time.time() + 60)
    with Session() as db:
        for c in db.scalars(select(Component)):
            view = components[c.id]
            assert view['vehicle_id'] == c.vehicle_id
            assert view['status'] == c.status
            assert view['accumulated_km'] == pytest.approx(c.accumulated_km)
    mounted = [c for c in components.values() if c['vehicle_id'] is not None]
    assert len(mounted) == len({c['id'] for c in mounted})       # 一件部件只在一车
    assert len(mounted) == len({c['vehicle_id'] for c in mounted})  # 一车只装一件


# ------------------------------------------------------------- 截止样本

def test_12_sample_missing_reading_blocks_whole_batch(client, auth, fleet_ready):
    r = client.post('/fleet/samples/preview', headers=auth,
                    json={'cutoff_at': time.time() + 120, 'cutoff_readings': {}})
    assert r.status_code == 409 and '整批样本已阻断' in r.json()['detail']


def test_13_sample_generation_and_open_segment(client, auth, fleet_ready):
    with Session() as db:
        all_components = list(db.scalars(select(Component)))
        mounted = [(c.asset_no, db.get(Vehicle, c.vehicle_id).plate_no, c.installed_reading)
                   for c in all_components if c.vehicle_id]
        expected_failed = {c.asset_no for c in all_components if c.status == 'failed'}
        # 库内零里程件不进入估计样本
        expected_excluded = {c.asset_no for c in all_components
                             if c.status == 'spare' and c.accumulated_km == 0 and not c.vehicle_id}
        expected_total = len(all_components) - len(expected_excluded)
    cutoff = time.time() + 180
    readings = {plate: round(installed + 5000, 1) for _, plate, installed in mounted}
    r = client.post('/fleet/samples/preview', headers=auth,
                    json={'cutoff_at': cutoff, 'cutoff_readings': readings})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['counts']['total'] == expected_total
    failed_assets = {row['asset'] for row in body['records'] if row['failed'] == 1}
    assert failed_assets == expected_failed
    assert {x['asset_no'] for x in body['excluded_no_exposure']} == expected_excluded
    assert body['counts']['in_service'] == len(mounted)
    by_asset = {row['asset']: row for row in body['records']}
    for asset, plate, _ in mounted:
        seg = next(s for s in body['open_segments'] if s['asset_no'] == asset)
        assert seg['segment_km'] == pytest.approx(5000.0)
        assert by_asset[asset]['failed'] == 0
    assert all(1 <= row['km'] <= 10_000_000 for row in body['records'])
    assert len({row['asset'] for row in body['records']}) == expected_total


def test_14_sample_persists_as_dataset_compatible_with_analyze(client, auth, fleet_ready):
    from src.services.business import analyze
    with Session() as db:
        mounted = [(db.get(Vehicle, c.vehicle_id).plate_no, c.installed_reading)
                   for c in db.scalars(select(Component)) if c.vehicle_id]
    cutoff = time.time() + 240
    readings = {plate: round(installed + 12000, 1) for plate, installed in mounted}
    r = client.post('/fleet/samples', headers=auth,
                    json={'cutoff_at': cutoff, 'cutoff_readings': readings,
                          'name': '截止样本-验收批次'})
    assert r.status_code == 201, r.text
    dataset_id = r.json()['dataset_id']
    with Session() as db:
        d = db.get(Dataset, dataset_id)
        assert set(d.records[0].keys()) == {'asset', 'model', 'km', 'failed'}
        assert d.source == 'swap-cutoff-sample'
        try:
            analyze(d.records, {'model': 'AC-8', 'reliability': 0.9})
        except ValueError as e:
            assert '至少需要' in str(e)  # 结构与既有估计完全同构，仅样本量不足


def test_15_cutoff_excludes_later_handovers(client, auth, fleet_ready):
    """截止之后发生的交接不计入样本：截止点仍在车的部件按在车件处理。"""
    from src.services.swaps import state_at
    assert install(client, auth, 'HO-069', 'V-8', 'C-11', 1600).status_code == 201
    with Session() as db:
        v8 = db.scalar(select(Vehicle).where(Vehicle.plate_no == 'V-8'))
        comp = db.scalar(select(Component).where(Component.vehicle_id == v8.id,
                                                 Component.asset_no == 'C-11'))
        asset, installed = comp.asset_no, comp.installed_reading
    # 截止时刻钉在卸车交接之前
    cutoff = time.time() + 1
    time.sleep(1.1)
    later = client.post('/fleet/handovers', headers=auth, json={
        'handover_no': 'HO-070',
        'events': [{'action': 'remove', 'plate_no': 'V-8', 'reading': installed + 3000,
                    'asset_no': asset, 'outcome': 'spare'}],
    })
    assert later.status_code == 201
    components, _ = state_at(Session(), cutoff)
    view = next(c for c in components.values() if c['asset_no'] == asset)
    assert view['status'] == 'in_service'  # 截止时还在车上，后续交接不参与
    # 用更晚的截止重放，同一部件已转待用
    components, _ = state_at(Session(), time.time() + 10)
    view = next(c for c in components.values() if c['asset_no'] == asset)
    assert view['status'] == 'spare'


# ------------------------------------------------------------- 履历与权限

def test_16_history_query_and_viewer_permissions(client, auth, viewer_headers, fleet_ready):
    r = client.get('/fleet/history?asset_no=C-1', headers=auth)
    assert r.status_code == 200
    actions = [row['action'] for row in r.json()]
    assert 'remove' in actions and 'install' in actions
    r = client.post('/fleet/handovers', headers=viewer_headers, json={
        'handover_no': 'HO-080',
        'events': [{'action': 'install', 'plate_no': 'V-2', 'reading': 9999, 'asset_no': 'C-9'}],
    })
    assert r.status_code == 403
    assert client.get('/fleet/components', headers=viewer_headers).status_code == 200
    assert client.get('/fleet/handovers', headers=viewer_headers).status_code == 200
    assert client.get('/fleet/history', headers=viewer_headers).status_code == 200


def test_17_validation_errors(client, auth, fleet_ready):
    r = client.post('/fleet/handovers', headers=auth, json={
        'handover_no': 'HO-090',
        'events': [{'action': 'remove', 'plate_no': 'V-3', 'reading': 9999, 'asset_no': 'C-4'}],
    })
    assert r.status_code == 422  # 卸车缺 outcome
    r = client.post('/fleet/handovers', headers=auth, json={
        'handover_no': 'HO-091',
        'events': [{'action': 'install', 'plate_no': 'GHOST', 'reading': 1, 'asset_no': 'C-9'}],
    })
    assert r.status_code == 422  # 车辆不存在
    r = client.post('/fleet/handovers', headers=auth, json={
        'handover_no': 'HO-092',
        'events': [{'action': 'remove', 'plate_no': 'V-8', 'reading': 9999,
                    'asset_no': 'C-7', 'outcome': 'spare'}],
    })
    assert r.status_code == 409  # C-7 不在 V-8 上
