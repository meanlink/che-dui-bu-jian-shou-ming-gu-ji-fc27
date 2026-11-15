"""车辆与周转部件的资产登记。"""
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models import Vehicle, Component


def register_vehicle(db, plate_no: str, name: str, initial_reading: float) -> Vehicle:
    plate_no = plate_no.strip()
    if not plate_no:
        raise HTTPException(422, '车牌号不能为空')
    if initial_reading < 0:
        raise HTTPException(422, '初始车表读数不能为负')
    if db.scalar(select(Vehicle).where(Vehicle.plate_no == plate_no)):
        raise HTTPException(409, f'车辆 {plate_no} 已登记')
    vehicle = Vehicle(plate_no=plate_no, name=name.strip(), current_reading=initial_reading,
                      created_at=time.time())
    db.add(vehicle)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, f'车辆 {plate_no} 已登记') from exc
    return vehicle


def register_component(db, asset_no: str, model: str) -> Component:
    asset_no = asset_no.strip()
    if not asset_no:
        raise HTTPException(422, '部件资产号不能为空')
    model = model.strip()
    if not model:
        raise HTTPException(422, '部件型号不能为空')
    if db.scalar(select(Component).where(Component.asset_no == asset_no)):
        raise HTTPException(409, f'部件 {asset_no} 已登记')
    component = Component(asset_no=asset_no, model=model, status='spare',
                          accumulated_km=0.0, created_at=time.time())
    db.add(component)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, f'部件 {asset_no} 已登记') from exc
    return component
