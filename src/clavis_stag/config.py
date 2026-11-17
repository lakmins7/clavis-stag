"""Flat STAG connection settings and expected firmware identity."""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class StagConfig(BaseModel):
    board_id: str = Field(min_length=1)
    board_type: Literal["stag"] = "stag"
    name: str = Field(min_length=1)
    host: str = Field(min_length=1)
    port: int = Field(default=4660, ge=1, le=65535)
    timeout: float = Field(default=0.1, gt=0, allow_inf_nan=False)
    spi_timeout: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    asic: Literal["AGASA_v3"] = "AGASA_v3"
    fpga: str = "Artix_Ultrascale_Plus"
    firmware_id: int = Field(default=0x5354, ge=0, le=0xFFFF)
    firmware_version: str = "1.0"

    @model_validator(mode="before")
    @classmethod
    def use_stag_metadata_defaults(cls, values: Any) -> Any:
        # Clavis's generic BoardConfig supplies "unknown" for omitted metadata
        # before passing the configuration to the plugin model.
        if isinstance(values, dict):
            values = dict(values)
            for key in ("asic", "fpga", "firmware_version"):
                if values.get(key) == "unknown":
                    values.pop(key)
        return values
