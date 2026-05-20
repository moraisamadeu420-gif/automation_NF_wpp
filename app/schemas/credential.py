"""
app/schemas/credential.py
"""
from pydantic import BaseModel


class CredentialCreate(BaseModel):
    portal_type: str = "nacional"
    municipality: str
    portal_url: str
    username: str
    password: str
    prestador_nome: str | None = None
    prestador_cnpj: str | None = None
    tomador_cnpj: str | None = None
    tomador_razao_social: str | None = None
    service_description: str | None = None
    service_favorite_name: str | None = None
    service_aliquota_iss: float = 2.0


class CredentialResponse(BaseModel):
    id: int
    user_id: int
    portal_type: str
    municipality: str
    username: str
    is_active: bool

    model_config = {"from_attributes": True}
