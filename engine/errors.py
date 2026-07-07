class EngineError(Exception):
    """Any engine-detected contract violation. The orchestrator maps an
    EngineError at S3 to KILLED(reason=infrastructure) per R4."""
