import os
import tempfile

# Disk files are stored on disk: keep test blobs out of the repository.
os.environ.setdefault("LEGAL_HUB_DRIVE_BLOB_DIR", tempfile.mkdtemp(prefix="lh-drive-"))
