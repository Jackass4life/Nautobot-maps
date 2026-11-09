"""The data contract with Nautobot and LibreNMS (#309, #598)."""

from fastapi import APIRouter

from app.services import contract_service
from app.session import DbSession
from app.v1 import sections
from app.v1.contract.dtos import ContractDto

router = APIRouter(prefix="/contract", tags=sections.tag("administration"))


@router.get("", operation_id="getContract", response_model=ContractDto)
def get_contract(db: DbSession) -> ContractDto:
    """The data contract and the last check: per upstream endpoint, the fields read,
    their types and what each becomes; per endpoint, when the last sync checked it,
    how many records, and the fields that didn't match."""
    checks = contract_service.latest_checks(db.connection())
    return ContractDto.model_validate({"contract": contract_service.as_json(), "checks": checks})
