"""按截止时刻生成 Weibull 寿命估计样本。

样本口径（与既有分析输入同构：asset/model/km/failed）：
- failed：截止前已失效件，km=失效时累计里程；
- spare：截止前已卸下待用的右删失件，km=截止时累计里程；
- in_service：截止时仍在车的右删失件，
  km=累计里程 + (截止车表读数 − 上车读数)；缺截止读数整批阻断，不产出半成品。
状态由交接流水重放到 cutoff_at 得到，截止之后的交接不参与。
"""
from fastapi import HTTPException
from sqlalchemy import select

from src.models import SwapOrder
from src.models.dataset import Dataset
from src.services.swaps import state_at
from src.utils.serialization import checksum


def build_sample(db, cutoff_at: float, cutoff_readings: dict, name: str | None = None):
    components, vehicles = state_at(db, cutoff_at)

    # 整批校验：任何一个在车部件缺截止读数都阻断整批，不产出半成品。
    missing, stale = [], []
    for c in components.values():
        if c['status'] != 'in_service' or c['vehicle_id'] is None:
            continue
        vehicle = vehicles.get(c['vehicle_id'])
        plate = vehicle['plate_no'] if vehicle else f"#{c['vehicle_id']}"
        if plate not in cutoff_readings:
            missing.append(f'{plate}（部件 {c["asset_no"]}）')
            continue
        reading = cutoff_readings[plate]
        try:
            reading = float(reading)
        except (TypeError, ValueError):
            stale.append(f'{plate} 截止读数不是数值')
            continue
        if reading < (c['installed_reading'] or 0):
            stale.append(f'{plate} 截止读数 {reading} 低于上车读数 {c["installed_reading"]}')
        elif vehicle and vehicle['reading'] is not None and reading < vehicle['reading']:
            stale.append(f'{plate} 截止读数 {reading} 低于截止时车表 {vehicle["reading"]}')
    if missing:
        raise HTTPException(409, '缺少截止车表读数，整批样本已阻断：' + '；'.join(missing))
    if stale:
        raise HTTPException(422, '截止读数不合法：' + '；'.join(stale))

    records, open_segments, excluded = [], [], []
    for c in components.values():
        if c['status'] == 'failed':
            km = c['failed_km'] if c['failed_km'] is not None else c['accumulated_km']
        elif c['status'] == 'spare':
            km = c['accumulated_km']
        else:
            vehicle = vehicles.get(c['vehicle_id'])
            reading = float(cutoff_readings[vehicle['plate_no']])
            segment = round(reading - (c['installed_reading'] or 0.0), 6)
            km = round(c['accumulated_km'] + segment, 1)
            open_segments.append({
                'asset_no': c['asset_no'], 'plate_no': vehicle['plate_no'],
                'installed_reading': c['installed_reading'], 'cutoff_reading': reading,
                'segment_km': segment, 'accumulated_km': km,
            })
        # 截止时无任何运行里程的库内件/新件不构成寿命观测，不进入估计样本（透明列明，不静默丢弃）
        if km <= 0:
            excluded.append({'asset_no': c['asset_no'], 'model': c['model'],
                             'status': c['status'], 'reason': '截止时累计里程为0，无运行暴露'})
            continue
        records.append({'asset': c['asset_no'], 'model': c['model'],
                        'km': round(float(km), 1),
                        'failed': 1 if c['status'] == 'failed' else 0})

    if not records:
        raise HTTPException(409, '截止时刻没有任何带运行里程的部件观测，无法生成样本')
    assets = [r['asset'] for r in records]
    if len(assets) != len(set(assets)):
        raise HTTPException(409, '样本出现重复资产号，整批阻断，请核对部件履历')

    later = db.scalar(select(SwapOrder).where(
        SwapOrder.status == 'applied', SwapOrder.created_at > cutoff_at))
    payload = {
        'cutoff_at': cutoff_at,
        'records': records,
        'open_segments': open_segments,
        'excluded_no_exposure': excluded,
        'counts': {
            'total': len(records),
            'failed': sum(1 for r in records if r['failed'] == 1),
            'censored': sum(1 for r in records if r['failed'] == 0),
            'in_service': len(open_segments),
            'excluded': len(excluded),
        },
        'has_later_handover': later is not None,
        'checksum': checksum(records),
    }

    if name:
        if db.scalar(select(Dataset).where(Dataset.name == name)):
            raise HTTPException(409, f'数据批次名 {name} 已存在，请换一个名称')
        dataset = Dataset(
            name=name, source='swap-cutoff-sample', records=records,
            checksum=payload['checksum'], created_at=cutoff_at,
        )
        db.add(dataset)
        db.flush()
        payload['dataset_id'] = dataset.id
    return payload
