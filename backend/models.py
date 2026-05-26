from pydantic import BaseModel
from typing import Literal, Optional


class ExtractionResponse(BaseModel):
    id: str
    file_name: str
    status: Literal["processing", "pending_review", "completed", "failed"]
    page_count: int
    chunks_processed: int
    chunks_total: int
    created_at: str
    completed_at: Optional[str] = None
    metadata: Optional["MetadataResponse"] = None


class MetadataResponse(BaseModel):
    extraction_id: str
    contract_number: Optional[str] = None
    contracting_entity: Optional[str] = None
    contractor_name: Optional[str] = None
    contract_object: Optional[str] = None
    total_value: Optional[str] = None
    start_date: Optional[str] = None
    duration: Optional[str] = None
    supervisor: Optional[str] = None
    confirmed_by_user: bool


class MetadataUpdate(BaseModel):
    contract_number: Optional[str] = None
    contracting_entity: Optional[str] = None
    contractor_name: Optional[str] = None
    contract_object: Optional[str] = None
    total_value: Optional[str] = None
    start_date: Optional[str] = None
    duration: Optional[str] = None
    supervisor: Optional[str] = None
    confirmed_by_user: Optional[bool] = None


class ObligationResponse(BaseModel):
    id: str
    extraction_id: str
    obligation_type: Literal["deliverable", "report", "notification", "compliance", "penalty"]
    description: str
    responsible_party: Literal["contratista", "interventor", "entidad"]
    deadline: Optional[str] = None
    periodicity: Optional[str] = None
    source_clause: Optional[str] = None
    source_page: Optional[int] = None
    source_fragment: Optional[str] = None
    confidence: Literal["high", "medium", "low"]
    review_status: Literal["pending", "approved", "edited", "rejected"]


class ObligationUpdate(BaseModel):
    obligation_type: Optional[Literal["deliverable", "report", "notification", "compliance", "penalty"]] = None
    description: Optional[str] = None
    responsible_party: Optional[Literal["contratista", "interventor", "entidad"]] = None
    deadline: Optional[str] = None
    periodicity: Optional[str] = None
    source_clause: Optional[str] = None
    source_page: Optional[int] = None
    source_fragment: Optional[str] = None
    confidence: Optional[Literal["high", "medium", "low"]] = None
    review_status: Optional[Literal["pending", "approved", "edited", "rejected"]] = None
