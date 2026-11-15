from sqlalchemy import String, Double, ForeignKey, UniqueConstraint, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class Vehicle(Base):
    """运营车辆；车表（里程表）读数属于车辆而不是部件。"""
    __tablename__ = 'vehicles'
    id: Mapped[int] = mapped_column(primary_key=True)
    plate_no: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120), default='')
    current_reading: Mapped[float] = mapped_column(Double, default=0.0)
    created_at: Mapped[float] = mapped_column(Double)


class Component(Base):
    """周转压缩机部件；累计里程按每段装卸车表差逐段累加。"""
    __tablename__ = 'components'
    __table_args__ = (UniqueConstraint('vehicle_id', name='uq_component_vehicle_one_slot'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    asset_no: Mapped[str] = mapped_column(String(60), unique=True)
    model: Mapped[str] = mapped_column(String(40))
    # spare=卸下待用 in_service=在车上 failed=已失效
    status: Mapped[str] = mapped_column(String(20), default='spare')
    accumulated_km: Mapped[float] = mapped_column(Double, default=0.0)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey('vehicles.id'), nullable=True)
    installed_reading: Mapped[float | None] = mapped_column(Double, nullable=True)
    installed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    failed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    failed_km: Mapped[float | None] = mapped_column(Double, nullable=True)
    created_at: Mapped[float] = mapped_column(Double)


class SwapOrder(Base):
    """换装交接单：幂等聚合根，整单生效，任一冲突整单回滚。"""
    __tablename__ = 'swap_orders'
    id: Mapped[int] = mapped_column(primary_key=True)
    handover_no: Mapped[str] = mapped_column(String(60), unique=True)
    order_type: Mapped[str] = mapped_column(String(20))  # single / pair
    note: Mapped[str] = mapped_column(Text, default='')
    # 仅 applied 会落库；迟到/冲突交接在事务内直接拒绝并回滚，不产生订单
    status: Mapped[str] = mapped_column(String(20), default='applied')
    content_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)


class SwapEvent(Base):
    """单段装卸履历：remove=卸车结算一段里程，install=上车占位。"""
    __tablename__ = 'swap_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('swap_orders.id'))
    seq: Mapped[int] = mapped_column(default=0)
    action: Mapped[str] = mapped_column(String(10))  # remove / install
    vehicle_id: Mapped[int] = mapped_column(ForeignKey('vehicles.id'))
    component_id: Mapped[int | None] = mapped_column(ForeignKey('components.id'), nullable=True)
    reading: Mapped[float | None] = mapped_column(Double, nullable=True)
    segment_km: Mapped[float] = mapped_column(Double, default=0.0)
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)  # spare / failed
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Double)
