from pydantic import BaseModel
from typing import Optional

class AnalyzeRequest(BaseModel):
    type: str = "content"
    text: str = ""
    device_id: Optional[str] = None

class AnalyzeResponse(BaseModel):
    status: str
    reason: str
    level: Optional[str] = None
