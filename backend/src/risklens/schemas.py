"""Boundary models. CSV parsing is explicit; unexpected fields are rejected."""
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Instrument(Model):
    instrument: str = Field(min_length=1)
    sector: str = Field(min_length=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

class Price(Model):
    date: date
    instrument: str = Field(min_length=1)
    adjusted_close: float = Field(gt=0)

class Weight(Model):
    instrument: str
    weight: float = Field(ge=0, le=1)

class CorporateAction(Model):
    date: date
    instrument: str
    action: Literal["split", "dividend"]
    value: float = Field(gt=0)
    evidence: str = Field(min_length=1)

class Provenance(Model):
    mode: Literal["synthetic", "historical_csv"]
    source: str = Field(min_length=1)
    acquired_at: datetime
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    start_date: date
    end_date: date
    adjustment_basis: str = Field(min_length=1)
    calendar_assumption: str = Field(min_length=1)
    observation_timestamps_available: bool
    @model_validator(mode="after")
    def check_dates(self):
        if self.start_date > self.end_date:
            raise ValueError("start_date exceeds end_date")
        if self.acquired_at.tzinfo is None:
            raise ValueError("acquired_at must include a timezone")
        return self

class Portfolio(Model):
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")
    valuation: float = Field(gt=0)
    weights_as_of: date
    convention: Literal["constant_weight_daily_rebalanced"]
    weights: list[Weight] = Field(min_length=1)
    @model_validator(mode="after")
    def check_weights(self):
        names = [w.instrument for w in self.weights]
        if len(set(names)) != len(names):
            raise ValueError("duplicate portfolio instruments")
        if abs(sum(w.weight for w in self.weights) - 1.0) > 1e-10:
            raise ValueError("weights must sum to one within 1e-10")
        return self
