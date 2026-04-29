from abc import ABC, abstractmethod
from typing import Optional, Sequence, Tuple
from domain.entities import User


class RoleRepositoryPort(ABC):
    @abstractmethod
    async def list_all(self) -> Sequence[dict]:

        pass

    @abstractmethod
    async def get_by_id(self, role_id: int) -> Optional[dict]:
        pass

    @abstractmethod
    async def get_by_name(self, name: str) -> Optional[dict]:
        pass

    @abstractmethod
    async def create(self, name: str) -> dict:
        pass

    @abstractmethod
    async def update(self, role_id: int, name: str) -> Optional[dict]:
        pass

    @abstractmethod
    async def delete(self, role_id: int) -> bool:
        pass

    @abstractmethod
    async def count_users_with_role(self, role_name: str) -> int:
        pass

    @abstractmethod
    async def get_permissions(self, role_id: int) -> dict:

        pass

    @abstractmethod
    async def set_permissions(self, role_id: int, permissions: dict) -> None:

        pass


class UserRepositoryPort(ABC):
    @abstractmethod
    async def get_by_azure_oid(self, azure_oid: str) -> Optional[User]:
        pass

    @abstractmethod
    async def get_by_id(self, user_id: int) -> Optional[User]:
        pass

    @abstractmethod
    async def get_all(
        self,
        include_archived: bool = False,
    ) -> Sequence[User]:
        pass

    @abstractmethod
    async def create(
        self,
        azure_oid: str,
        email: str,
        display_name: Optional[str],
        picture: Optional[str],
        role: str,
    ) -> User:
        pass

    @abstractmethod
    async def update_profile(
        self,
        user_id: int,
        display_name: Optional[str],
        picture: Optional[str],
        role: Optional[str],
    ) -> Optional[User]:
        pass

    @abstractmethod
    async def set_role(self, user_id: int, role: str) -> Optional[User]:
        pass

    @abstractmethod
    async def set_blocked(self, user_id: int, is_blocked: bool) -> Optional[User]:
        pass

    @abstractmethod
    async def set_archived(self, user_id: int, is_archived: bool) -> Optional[User]:
        pass

    @abstractmethod
    async def set_time_tracking_role(self, user_id: int, time_tracking_role: Optional[str]) -> Optional[User]:
        pass

    @abstractmethod
    async def set_position(self, user_id: int, position: Optional[str]) -> Optional[User]:
        pass

    @abstractmethod
    async def set_desktop_background(self, user_id: int, path: Optional[str]) -> Optional[User]:
        pass

    @abstractmethod
    async def set_active_session_jti(self, user_id: int, jti: Optional[str]) -> Optional[User]:

        pass

    @abstractmethod
    async def get_local_admin_credentials(self) -> Optional[Tuple[str, str]]:

        pass

    @abstractmethod
    async def save_local_admin_credentials(self, username: str, password_hash: str) -> None:
        pass


class TokenServicePort(ABC):
    @abstractmethod
    def create_access_token(self, user_id: int, azure_oid: str, jti: str) -> str:
        pass

    @abstractmethod
    def decode_token(self, token: str) -> Optional[dict]:
        pass
