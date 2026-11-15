from pydantic import BaseModel, Field


class HandoverEventInput(BaseModel):
    action: str = Field(description="remove=卸车 install=上车")
    plate_no: str = Field(min_length=1, max_length=40)
    reading: float | None = Field(default=None, ge=0)
    asset_no: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=40)
    outcome: str | None = Field(default=None, description="remove 时：spare=卸下待用 failed=失效")


class HandoverInput(BaseModel):
    handover_no: str = Field(min_length=2, max_length=60)
    note: str = Field(default='', max_length=500)
    events: list[HandoverEventInput] = Field(min_length=1, max_length=20)


class SampleInput(BaseModel):
    cutoff_at: float = Field(description="截止时刻（epoch 秒）")
    cutoff_readings: dict[str, float] = Field(
        default_factory=dict,
        description="截止时仍在车部件所在车辆的车表读数：plate_no -> reading",
    )
    name: str | None = Field(default=None, max_length=160)
