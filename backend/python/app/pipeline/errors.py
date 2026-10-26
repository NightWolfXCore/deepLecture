class PipelineError(Exception):
    """An explicitly safe error suitable for processing_jobs.error_message."""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)
