import os
import tempfile

# Uploaded files are stored on disk: keep test blobs out of the repository.
os.environ.setdefault("FILE_MANAGER_BLOB_DIR", tempfile.mkdtemp(prefix="fm-blobs-"))
