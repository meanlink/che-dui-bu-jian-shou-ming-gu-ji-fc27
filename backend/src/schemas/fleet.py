from pydantic import BaseModel, Field


class VehicleInput(BaseModel):
    plate_no: str = Field(min_length=1, max_length=40)
    name: str = Field(default='', max_length=120)
    initial_reading: float = Field(default=0.0, ge=0)


class ComponentInput(BaseModel):
    asset_no: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=40)
