from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from application.ports import TokenServicePort
from infrastructure.config import get_settings

OUTLOOK_TOKEN_TYP = "outlook_fm"
OUTLOOK_TOKEN_AUD = "file-manager"
OUTLOOK_TOKEN_SCOPE = "fm.outlook.upload"


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

    def create_outlook_access_token(
        self,
        user_id: int,
        azure_oid: str,
        *,
        jti: str,
        device_id: str,
        family_id: str,
    ) -> str:
        settings = get_settings()
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "oid": azure_oid,
            "jti": jti,
            "typ": OUTLOOK_TOKEN_TYP,
            "aud": OUTLOOK_TOKEN_AUD,
            "scope": OUTLOOK_TOKEN_SCOPE,
            "did": device_id,
            "fid": family_id,
            "exp": now + timedelta(minutes=settings.outlook_access_expire_minutes),
            "iat": now,
        }
        return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    def decode_token(self, token: str) -> Optional[dict]:
        try:
            settings = get_settings()
            # Hub JWTs have no aud; Outlook tokens set aud=file-manager (validated by callers).
            return jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            return None
