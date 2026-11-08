"""Typed simulator settings, loaded from config/simulator.yaml."""

from pathlib import Path
from typing import Annotated, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.db.models import RouteMode

Probability = Annotated[float, Field(ge=0, le=1)]
Positive = Annotated[float, Field(gt=0)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Range(_Strict):
    """A `[low, high]` pair in YAML."""

    low: float
    high: float

    @model_validator(mode="before")
    @classmethod
    def _from_pair(cls, value: object) -> object:
        if isinstance(value, list | tuple) and len(value) == 2:
            return {"low": value[0], "high": value[1]}
        return value

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.low > self.high:
            raise ValueError(f"range low {self.low} is above high {self.high}")
        return self


class WeightConfig(_Strict):
    median: Positive
    sigma: Positive
    min: int = Field(gt=0)
    max: int = Field(gt=0)


class DemandConfig(_Strict):
    shipments_per_day: int = Field(ge=0)
    daily_noise: Probability
    weekday_factor: tuple[float, float, float, float, float, float, float]
    booking_hours: Range
    express_share: Probability
    local_share: Probability
    hub_weights: dict[str, Positive] = {}
    weight_grams: WeightConfig


class Sla(_Strict):
    local: int = Field(ge=1)
    intercity: int = Field(ge=1)


class SlaConfig(_Strict):
    express: Sla
    standard: Sla


class TimingConfig(_Strict):
    pickup_hours: Range
    last_pickup_hour: int = Field(ge=0, le=23)
    hub_dwell_minutes: Range
    transit_jitter: Range
    delivery_start_hour: int = Field(ge=0, le=23)
    delivery_hours: Range


class RateConfig(_Strict):
    rate: Probability


class DelayFault(RateConfig):
    hours: Range


class MissedConnectionFault(RateConfig):
    hours: dict[RouteMode, Positive]


class FailedDeliveryFault(RateConfig):
    attempts_weights: tuple[float, ...] = Field(min_length=1)


class FaultsConfig(_Strict):
    pickup_delay: DelayFault
    hub_stuck: DelayFault
    missed_connection: MissedConnectionFault
    misroute: RateConfig
    failed_delivery: FailedDeliveryFault
    lost: RateConfig

    @model_validator(mode="after")
    def _rates_leave_room_for_no_fault(self) -> Self:
        total = sum(getattr(self, name).rate for name in type(self).model_fields)
        if total >= 1:
            raise ValueError(f"fault rates sum to {total:.3f}; they must sum to less than 1")
        return self


class SimulatorConfig(_Strict):
    seed: int
    timezone: str
    demand: DemandConfig
    sla_days: SlaConfig
    promise_hour: int = Field(ge=0, le=23)
    timing: TimingConfig
    faults: FaultsConfig
    max_delivery_attempts: int = Field(ge=1)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {self.timezone!r}") from exc
        if len(self.faults.failed_delivery.attempts_weights) > self.max_delivery_attempts:
            raise ValueError("failed_delivery.attempts_weights has more entries than max_delivery_attempts")
        return self


def load_simulator_config(path: Path) -> SimulatorConfig:
    return SimulatorConfig.model_validate(yaml.safe_load(path.read_text()))
