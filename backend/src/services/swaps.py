"""换装交接领域服务。

口径要点：
- 车表读数属于车辆；部件寿命按每段「卸车读数 − 上车读数」逐段累加。
- 交接单是幂等聚合根：同一 handover_no 重试不重复累计；换内容直接拒绝。
- 安装占位与里程结算随整单在同一事务内生效，任一冲突整单回滚不动。
- 迟到交接（读数落后于车辆当前读数，或事件时间倒退）拒绝，不覆盖现状。
"""
import hashlib
import json
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models import Vehicle, Component, SwapOrder, SwapEvent

VALID_ACTIONS = {'remove', 'install'}
VALID_OUTCOMES = {'spare', 'failed'}


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def _payload_of(data) -> dict:
    return {
        'handover_no': data.handover_no,
        'note': data.note,
        'events': [e.model_dump() for e in data.events],
    }


def _conflict(message: str) -> HTTPException:
    return HTTPException(409, message)


def _validate_shape(data):
    events = data.events
    removes = [e for e in events if e.action == 'remove']
    installs = [e for e in events if e.action == 'install']
    if len(events) != len(removes) + len(installs):
        raise HTTPException(422, '交接动作只能是 remove（卸车）或 install（上车）')
    if not removes and not installs:
        raise HTTPException(422, '交接单至少包含一段装卸动作')
    # 同一辆车在单内只能出现一次卸车与一次上车（成对调换口径）
    for bucket, label in ((removes, '卸车'), (installs, '上车')):
        plates = [e.plate_no for e in bucket]
        if len(plates) != len(set(plates)):
            raise HTTPException(422, f'同一车辆在一张交接单内不能重复{label}')
    # 单车换装：一卸一装同车；成对调换：两卸两装，车辆集合一致
    r_plates = {e.plate_no for e in removes}
    i_plates = {e.plate_no for e in installs}
    if removes and installs:
        if r_plates != i_plates:
            raise HTTPException(422, '成对调换须在相同两辆车之间交换部件，装卸车辆集合不一致')
        if len(r_plates) not in (1, 2):
            raise HTTPException(422, '仅支持单车换装或两辆车成对调换')
    for index, e in enumerate(events):
        if e.action == 'remove':
            if not e.asset_no:
                raise HTTPException(422, f'第 {index + 1} 段卸车必须指明部件资产号')
            if e.reading is None:
                raise HTTPException(422, f'第 {index + 1} 段卸车缺少车表读数，无法结算里程')
            if e.outcome not in VALID_OUTCOMES:
                raise HTTPException(422, f'第 {index + 1} 段卸车须标明 spare（待用）或 failed（失效）')
        else:
            if not e.asset_no:
                raise HTTPException(422, f'第 {index + 1} 段上车必须指明部件资产号')
            if e.reading is None:
                raise HTTPException(422, f'第 {index + 1} 段上车缺少车表读数，无法建立占位')
            if e.outcome is not None:
                raise HTTPException(422, f'第 {index + 1} 段上车不允许填写卸下结论')
    # 一件部件在单内至多卸一次、装一次，且不能同一动作重复
    touched = {}
    for e in events:
        slot = touched.setdefault(e.asset_no, set())
        if e.action in slot:
            raise HTTPException(422, f'部件 {e.asset_no} 在单内重复{e.action}，一件部件一次交接只能占一个位置')
        slot.add(e.action)


def _lock_vehicle(db, plate_no, for_update=True) -> Vehicle:
    stmt = select(Vehicle).where(Vehicle.plate_no == plate_no)
    if for_update:
        stmt = stmt.with_for_update()
    vehicle = db.scalar(stmt)
    if not vehicle:
        raise HTTPException(422, f'车辆 {plate_no} 不存在，请先登记车辆')
    return vehicle


def _lock_component(db, asset_no) -> Component:
    component = db.scalar(select(Component).where(Component.asset_no == asset_no).with_for_update())
    if not component:
        raise HTTPException(422, f'部件 {asset_no} 不存在，请先登记部件资产')
    return component


def _apply_order(db, data, user) -> SwapOrder:
    now = time.time()
    order_type = 'pair' if len({e.plate_no for e in data.events}) == 2 and \
        any(e.action == 'remove' for e in data.events) and \
        any(e.action == 'install' for e in data.events) else 'single'
    order = SwapOrder(
        handover_no=data.handover_no, order_type=order_type, note=data.note,
        status='applied', content_fingerprint=_fingerprint(_payload_of(data)),
        created_by=user.id, created_at=now,
    )
    db.add(order)
    db.flush()

    vehicles = {p: _lock_vehicle(db, p) for p in {e.plate_no for e in data.events}}
    components = {a: _lock_component(db, a) for a in {e.asset_no for e in data.events if e.asset_no}}

    # 先卸车：结算本段里程、释放占位；全部校验通过后再上车占位。
    for seq, e in enumerate(x for x in data.events if x.action == 'remove'):
        vehicle = vehicles[e.plate_no]
        component = components[e.asset_no]
        if component.vehicle_id != vehicle.id:
            raise _conflict(
                f'部件 {e.asset_no} 当前不在车辆 {e.plate_no} 上'
                + ('（已被其他交接移动）' if component.vehicle_id else '（处于库内）')
            )
        if component.installed_reading is None:
            raise _conflict(f'部件 {e.asset_no} 缺少上车读数，无法结算本段里程')
        if e.reading < vehicle.current_reading:
            raise _conflict(
                f'迟到交接被拒绝：{e.plate_no} 卸车读数 {e.reading} 低于当前车表 {vehicle.current_reading}，'
                '不得回退覆盖现状'
            )
        if e.reading < component.installed_reading:
            raise _conflict(
                f'卸车读数 {e.reading} 低于该部件上车读数 {component.installed_reading}，里程不能为负'
            )
        segment = round(e.reading - component.installed_reading, 6)
        component.accumulated_km = round(component.accumulated_km + segment, 6)
        component.vehicle_id = None
        component.installed_reading = None
        component.installed_at = None
        if e.outcome == 'failed':
            component.status = 'failed'
            component.failed_at = now
            component.failed_km = component.accumulated_km
        else:
            component.status = 'spare'
        vehicle.current_reading = e.reading
        db.add(SwapEvent(
            order_id=order.id, seq=seq, action='remove', vehicle_id=vehicle.id,
            component_id=component.id, reading=e.reading, segment_km=segment,
            outcome=e.outcome, payload=e.model_dump(), created_at=now,
        ))

    for seq, e in enumerate(x for x in data.events if x.action == 'install'):
        vehicle = vehicles[e.plate_no]
        component = components[e.asset_no]
        if component.status == 'failed':
            raise _conflict(f'失效部件 {e.asset_no} 不能再次上车，请改用待用件或新件')
        if component.vehicle_id is not None:
            raise _conflict(f'部件 {e.asset_no} 已在另一辆车上，一件部件同时只能在一辆车')
        # 车辆占位：unique(vehicle_id) 兜底，显式判断给出可读错误
        occupant = db.scalar(select(Component).where(Component.vehicle_id == vehicle.id).with_for_update())
        if occupant is not None:
            raise _conflict(f'车辆 {e.plate_no} 已装部件 {occupant.asset_no}，一车同时只装一件')
        if e.reading < vehicle.current_reading:
            raise _conflict(
                f'迟到交接被拒绝：{e.plate_no} 上车读数 {e.reading} 低于当前车表 {vehicle.current_reading}，'
                '不得回退覆盖现状'
            )
        component.vehicle_id = vehicle.id
        component.status = 'in_service'
        component.installed_reading = e.reading
        component.installed_at = now
        vehicle.current_reading = e.reading
        db.add(SwapEvent(
            order_id=order.id, seq=seq, action='install', vehicle_id=vehicle.id,
            component_id=component.id, reading=e.reading, segment_km=0.0,
            outcome=None, payload=e.model_dump(), created_at=now,
        ))
    return order


def submit_handover(db, data, user) -> tuple[SwapOrder, bool]:
    """登记交接单。返回 (订单, 是否新建)；重试同一单号返回既有单。"""
    _validate_shape(data)
    payload = _payload_of(data)
    fingerprint = _fingerprint(payload)

    existing = db.scalar(select(SwapOrder).where(SwapOrder.handover_no == data.handover_no))
    if existing is not None:
        if existing.content_fingerprint != fingerprint:
            raise _conflict(
                f'交接单号 {data.handover_no} 已存在且内容不同；交接不可改写，'
                '请使用新单号登记，或按原内容重试'
            )
        return existing, False

    try:
        order = _apply_order(db, data, user)
        db.flush()
    except IntegrityError as exc:
        # 并发下唯一约束（一车一件 / 单号唯一）兜底
        raise _conflict('交接与正在处理的其他换装冲突，整单未生效，请重试') from exc
    return order, True


# ---------------------------------------------------------------- 履历查询

def history(db, asset_no: str | None = None, plate_no: str | None = None):
    stmt = select(SwapEvent).join(SwapOrder, SwapEvent.order_id == SwapOrder.id)
    if asset_no:
        component = db.scalar(select(Component).where(Component.asset_no == asset_no))
        if not component:
            raise HTTPException(404, f'部件 {asset_no} 不存在')
        stmt = stmt.where(SwapEvent.component_id == component.id)
    if plate_no:
        vehicle = db.scalar(select(Vehicle).where(Vehicle.plate_no == plate_no))
        if not vehicle:
            raise HTTPException(404, f'车辆 {plate_no} 不存在')
        stmt = stmt.where(SwapEvent.vehicle_id == vehicle.id)
    events = list(db.scalars(stmt.order_by(SwapEvent.id.asc())))
    vehicle_map = {v.id: v for v in db.scalars(select(Vehicle))}
    component_map = {c.id: c for c in db.scalars(select(Component))}
    order_map = {o.id: o for o in db.scalars(select(SwapOrder))}
    rows = []
    for ev in events:
        vehicle = vehicle_map.get(ev.vehicle_id)
        component = component_map.get(ev.component_id) if ev.component_id else None
        order = order_map.get(ev.order_id)
        rows.append({
            'id': ev.id,
            'handover_no': order.handover_no if order else '',
            'order_status': order.status if order else '',
            'action': ev.action,
            'plate_no': vehicle.plate_no if vehicle else '',
            'asset_no': component.asset_no if component else '',
            'model': component.model if component else '',
            'reading': ev.reading,
            'segment_km': ev.segment_km,
            'outcome': ev.outcome,
            'at': ev.created_at,
        })
    return rows


def component_ledger(db):
    vehicles = {v.id: v for v in db.scalars(select(Vehicle))}
    out = []
    for c in db.scalars(select(Component).order_by(Component.id.asc())):
        vehicle = vehicles.get(c.vehicle_id) if c.vehicle_id else None
        out.append({
            'id': c.id,
            'asset_no': c.asset_no,
            'model': c.model,
            'status': c.status,
            'accumulated_km': c.accumulated_km,
            'plate_no': vehicle.plate_no if vehicle else None,
            'installed_reading': c.installed_reading,
            'failed_km': c.failed_km,
        })
    return out


def vehicle_board(db):
    return [{
        'id': v.id, 'plate_no': v.plate_no, 'name': v.name,
        'current_reading': v.current_reading,
    } for v in db.scalars(select(Vehicle).order_by(Vehicle.id.asc()))]


def state_at(db, cutoff_at: float):
    """按交接事件流水重放到 cutoff_at，还原截止时刻的部件与车辆状态。

    截止之后登记的交接一律不参与，保证估计样本只含截止时点可见信息。
    """
    vehicles = {v.id: {'id': v.id, 'plate_no': v.plate_no, 'reading': None}
                for v in db.scalars(select(Vehicle))}
    components = {c.id: {
        'id': c.id, 'asset_no': c.asset_no, 'model': c.model,
        'status': 'spare', 'accumulated_km': 0.0, 'vehicle_id': None,
        'installed_reading': None, 'failed_km': None,
    } for c in db.scalars(select(Component))}

    events = list(db.scalars(
        select(SwapEvent).join(SwapOrder, SwapEvent.order_id == SwapOrder.id)
        .where(SwapOrder.status == 'applied', SwapEvent.created_at <= cutoff_at)
        .order_by(SwapEvent.id.asc())
    ))
    for ev in events:
        vehicle = vehicles.get(ev.vehicle_id)
        component = components.get(ev.component_id) if ev.component_id else None
        if not vehicle or not component:
            continue
        if ev.action == 'remove':
            component['accumulated_km'] = round(component['accumulated_km'] + ev.segment_km, 6)
            component['vehicle_id'] = None
            component['installed_reading'] = None
            if ev.outcome == 'failed':
                component['status'] = 'failed'
                component['failed_km'] = component['accumulated_km']
            else:
                component['status'] = 'spare'
        else:
            component['vehicle_id'] = vehicle['id']
            component['status'] = 'in_service'
            component['installed_reading'] = ev.reading
        vehicle['reading'] = ev.reading
    return components, vehicles
