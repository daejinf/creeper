class RevisionConflict(ValueError):
    """An action, message, or expected record version conflicts with stored data."""

class RecordNotFound(LookupError):pass

class OperationBusy(RuntimeError):pass

class RecoveryRequired(RuntimeError):pass
