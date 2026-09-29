from pydantic import BaseModel, Field


class SourceReference(BaseModel):
    title: str
    reference: str
    excerpt: str | None = None


class SpecialistResult(BaseModel):
    agent: str
    status: str  
    answer: str = ""
    sources: list[SourceReference] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    error: str | None = None


class JudgeResult(BaseModel):
    decision: str  
    reason: str
    unsupported_claims: list[str] = Field(default_factory=list)