from pydantic import BaseModel


class TextRequest(BaseModel):
    text: str
    device_id: str = None
    app: str = None


class UrlRequest(BaseModel):
    url: str
    device_id: str = None


class UsageRequest(BaseModel):
    package: str
    duration: int
    device_id: str = None


class AnalyzeSmartResponse(BaseModel):
    category: str
    risk_level: int
    is_safe: bool
    reason: str


class RegisterFcmTokenRequest(BaseModel):
    device_id: str
    fcm_token: str
    child_id: str = None


class AnalyzeScreenResponse(BaseModel):
    is_dangerous: bool
    label: str
    confidence: float
    extracted_text: str
class MarkReadRequest(BaseModel):
    parent_id: str
    alert_ids: list[str] = None
    mark_all: bool = False

class HeartbeatRequest(BaseModel):
    device_id: str
    status: str = "online"
