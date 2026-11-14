ALLOWED_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
}

ALLOWED_FILE_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xlsx",
    ".xls",
}

INGEST_FILE_EXTENSIONS = ALLOWED_FILE_EXTENSIONS | {
    ".py",
    ".jpg",
    ".jpeg",
    ".png",
    "",
}
