from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.schemas.common import ORMModel


# Trimmed, non-blank strings of bounded length.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Address = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
City = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
TestCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=30)
]
Pincode = Annotated[str, StringConstraints(pattern=r"^\d{6}$")]
Price = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2, examples=["499.00"])]


class DiagnosticTestCreate(BaseModel):
    code: TestCode = Field(examples=["CBC"])
    name: Name = Field(examples=["Complete Blood Count"])
    description: str | None = Field(None, max_length=2000)


class DiagnosticTestOut(ORMModel):
    id: int
    code: str
    name: str
    description: str | None


class CentreCreate(BaseModel):
    name: Name = Field(examples=["EVE Diagnostics Indiranagar"])
    address: Address = Field(examples=["100 Feet Rd, Indiranagar"])
    city: City = Field(examples=["Bengaluru"])
    pincode: Pincode | None = Field(None, examples=["560038"])


class _PartialUpdate(BaseModel):
    """PATCH body: omitted fields are left unchanged; explicit null is only
    allowed for fields listed in `_nullable`."""

    _nullable: tuple[str, ...] = ()

    @model_validator(mode="after")
    def reject_null_for_required_fields(self):
        bad = [
            name
            for name in self.model_fields_set
            if getattr(self, name) is None and name not in self._nullable
        ]
        if bad:
            raise ValueError(f"fields cannot be null: {', '.join(sorted(bad))}")
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        return self


class CentreUpdate(_PartialUpdate):
    _nullable = ("pincode",)

    name: Name | None = None
    address: Address | None = None
    city: City | None = None
    pincode: Pincode | None = None
    is_active: bool | None = None


class CentreTestCreate(BaseModel):
    test_id: int = Field(gt=0)
    price: Price


class CentreTestUpdate(_PartialUpdate):
    price: Price | None = None
    is_active: bool | None = None


class CentreTestOut(ORMModel):
    test: DiagnosticTestOut
    price: Decimal
    is_active: bool


class CentreOut(ORMModel):
    id: int
    name: str
    address: str
    city: str
    pincode: str | None
    is_active: bool


class CentreDetailOut(CentreOut):
    tests: list[CentreTestOut]
