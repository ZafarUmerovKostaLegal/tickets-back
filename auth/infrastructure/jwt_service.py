from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from application.ports import TokenServicePort
from infrastructure.config import get_settings


class JWTService(TokenServicePort):
    def create_access_token(self, user_id: int, azure_oid: str, jti: str) -> str:
        settings = get_settings()
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "oid": azure_oid,
            "jti": jti,
            "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
            "iat": now,
        }
        return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    def decode_token(self, token: str) -> Optional[dict]:
        try:
            settings = get_settings()
            return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        except jwt.PyJWTError:
            return None
