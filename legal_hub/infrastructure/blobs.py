"""On-disk storage of Disk files (see backend_common.blob_store)."""

from backend_common.blob_store import BlobStore, UploadGate

from infrastructure.config import get_settings

store = BlobStore(lambda: get_settings().drive_blob_dir)
gate = UploadGate(writers=6, finishers=2)


def link_secret() -> bytes:
    return store.link_secret("LEGAL_HUB_LINK_SECRET")
