"""演示用换装履历：两台车、四件部件，覆盖待用/失效/成对调换。

直接复用生产域服务 submit_handover，保证 seed 与线上接口走同一套校验与结算口径。
"""
import time

from sqlalchemy import select

from src.models import User, Vehicle, Component
from src.schemas.swap import HandoverInput, HandoverEventInput
from src.services.swaps import submit_handover


def seed_fleet(db):
    if db.scalar(select(Vehicle).where(Vehicle.plate_no == '闽D-B001')):
        return
    now = time.time()
    admin = db.scalar(select(User).where(User.role == 'admin'))
    day = 86400
    v1 = Vehicle(plate_no='闽D-B001', name='12路 01 号车', current_reading=12000,
                 created_at=now - 90 * day)
    v2 = Vehicle(plate_no='闽D-B002', name='12路 02 号车', current_reading=12000,
                 created_at=now - 90 * day)
    db.add_all([v1, v2])
    db.flush()
    db.add_all([
        Component(asset_no='AC8-A001', model='AC-8', status='spare', accumulated_km=0,
                  created_at=now - 90 * day),
        Component(asset_no='AC8-A002', model='AC-8', status='spare', accumulated_km=0,
                  created_at=now - 90 * day),
        Component(asset_no='AC12-B001', model='AC-12', status='spare', accumulated_km=0,
                  created_at=now - 90 * day),
        Component(asset_no='AC12-B002', model='AC-12', status='spare', accumulated_km=0,
                  created_at=now - 90 * day),
    ])
    db.flush()

    def ev(action, plate, reading, asset, outcome=None):
        return HandoverEventInput(action=action, plate_no=plate, reading=reading,
                                  asset_no=asset, outcome=outcome)

    orders = [
        ('HO-202608-001', '新车首装 A001/B001', [
            ev('install', '闽D-B001', 12000, 'AC8-A001'),
            ev('install', '闽D-B002', 12000, 'AC12-B001'),
        ]),
        ('HO-202609-014', 'B001 压缩机异响失效，换待用 A002', [
            ev('remove', '闽D-B001', 41000, 'AC8-A001', 'failed'),
            ev('install', '闽D-B001', 41000, 'AC8-A002'),
        ]),
        ('HO-202610-027', '线路调整，两车压缩机成对调换', [
            ev('remove', '闽D-B001', 56000, 'AC8-A002', 'spare'),
            ev('remove', '闽D-B002', 53000, 'AC12-B001', 'spare'),
            ev('install', '闽D-B001', 56000, 'AC12-B001'),
            ev('install', '闽D-B002', 53000, 'AC8-A002'),
        ]),
        ('HO-202611-008', 'B002 例行到检，A002 转待用，换 B002 新件', [
            ev('remove', '闽D-B002', 64800, 'AC8-A002', 'spare'),
            ev('install', '闽D-B002', 64800, 'AC12-B002'),
        ]),
    ]
    for no, note, events in orders:
        submit_handover(db, HandoverInput(handover_no=no, note=note, events=events), admin)
