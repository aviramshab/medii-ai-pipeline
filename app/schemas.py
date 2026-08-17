from pydantic import BaseModel, Field
from typing import Optional


class TranslateResponse(BaseModel):
    """Response schema for translation endpoint."""
    
    engine: str = Field(description="Translation engine used")
    file_name: str = Field(description="Output file name")
    time_sec: float = Field(description="Translation time in seconds")
    tokens: int = Field(description="Total tokens used")
    download_url: str = Field(description="URL to download translated file")


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "healthy"
    service: str = "translator"