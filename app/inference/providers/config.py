from pydantic import BaseModel


class ProviderConfig(BaseModel):
    api_key: str
    base_url: str
    timeout: float = 60.0
