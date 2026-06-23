"""Request / response models for the agent API."""

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(..., description="The user's natural-language question.")
    policy_id: str | None = Field(default=None, description="Optional policy identifier (e.g. POL-001).")
    customer_id: str | None = Field(default=None, description="Optional customer identifier (e.g. CUST-001).")
    user_id: str | None = Field(default=None, description="Optional calling user identifier.")


class Citation(BaseModel):
    source: str
    title: str | None = None
    snippet: str | None = None
    score: float | None = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
