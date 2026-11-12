# Utilities

`security.py` contains shared file-path, file-type, file-size, and user-input
validation helpers used by document workflows. `limits.py` parses and
validates shared file/image size, upload-count, image-count, and prompt-length
settings for both the FastAPI and Streamlit interfaces. Keep validation
consistent with the supported document loaders and the vision model's image
capacity.

Add cross-cutting helpers here only when they are genuinely shared; API
request handling and agent orchestration belong in their respective packages.
See the [project guide](../README.md) for architecture and setup.
