from fastapi import APIRouter, Depends
from sqlalchemy import select

from src.config.database import get_db
from src.middlewares.auth import current_user, can_write
from src.models import Vehicle, Component, SwapOrder, SwapEvent
from src.schemas.fleet import VehicleInput, ComponentInput
from src.schemas.swap import HandoverInput, SampleInput
from src.services import fleet, swaps, samples
from src.services.audit import record

router = APIRouter(prefix='/fleet')


@router.get('/vehicles')
def list_vehicles(db=Depends(get_db), u=Depends(current_user)):
    return swaps.vehicle_board(db)


@router.post('/vehicles', status_code=201)
def create_vehicle(data: VehicleInput, db=Depends(get_db), u=Depends(can_write)):
    v = fleet.register_vehicle(db, data.plate_no, data.name, data.initial_reading)
    record(db, u.email, 'fleet.vehicle.register', {'id': v.id, 'plate_no': v.plate_no})
    db.commit()
    return {'id': v.id, 'plate_no': v.plate_no, 'name': v.name, 'current_reading': v.current_reading}


@router.get('/components')
def list_components(db=Depends(get_db), u=Depends(current_user)):
    return swaps.component_ledger(db)


@router.post('/components', status_code=201)
def create_component(data: ComponentInput, db=Depends(get_db), u=Depends(can_write)):
    c = fleet.register_component(db, data.asset_no, data.model)
    record(db, u.email, 'fleet.component.register', {'id': c.id, 'asset_no': c.asset_no})
    db.commit()
    return {'id': c.id, 'asset_no': c.asset_no, 'model': c.model, 'status': c.status}


@router.get('/handovers')
def list_handovers(db=Depends(get_db), u=Depends(current_user)):
    rows = []
    for o in db.scalars(select(SwapOrder).order_by(SwapOrder.id.desc()).limit(200)):
        events = list(db.scalars(
            select(SwapEvent).where(SwapEvent.order_id == o.id).order_by(SwapEvent.id.asc())))
        rows.append({
            'id': o.id, 'handover_no': o.handover_no, 'order_type': o.order_type,
            'note': o.note, 'status': o.status, 'created_at': o.created_at,
            'events': [{
                'action': e.action, 'reading': e.reading, 'segment_km': e.segment_km,
                'outcome': e.outcome, 'payload': e.payload,
            } for e in events],
        })
    return rows


@router.post('/handovers', status_code=201)
def submit_handover(data: HandoverInput, db=Depends(get_db), u=Depends(can_write)):
    order, created = swaps.submit_handover(db, data, u)
    if created:
        record(db, u.email, 'fleet.handover.apply',
               {'handover_no': order.handover_no, 'type': order.order_type})
        db.commit()
        db.refresh(order)
    return {
        'handover_no': order.handover_no, 'order_type': order.order_type,
        'status': order.status, 'created': created,
        'message': '交接已生效' if created else '相同交接已受理，本次重试未重复累计里程',
    }


@router.get('/history')
def history(asset_no: str | None = None, plate_no: str | None = None,
            db=Depends(get_db), u=Depends(current_user)):
    return swaps.history(db, asset_no, plate_no)


@router.post('/samples/preview')
def preview_sample(data: SampleInput, db=Depends(get_db), u=Depends(current_user)):
    return samples.build_sample(db, data.cutoff_at, data.cutoff_readings, None)


@router.post('/samples', status_code=201)
def create_sample(data: SampleInput, db=Depends(get_db), u=Depends(can_write)):
    name = data.name or f'换装截止样本-{int(data.cutoff_at)}'
    payload = samples.build_sample(db, data.cutoff_at, data.cutoff_readings, name)
    record(db, u.email, 'fleet.sample.generate',
           {'dataset_id': payload.get('dataset_id'), 'counts': payload['counts']})
    db.commit()
    return payload
