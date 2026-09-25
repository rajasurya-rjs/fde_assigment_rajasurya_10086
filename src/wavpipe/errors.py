"""Failure taxonomy. Each class maps to a distinct, explainable operator action."""


class PipelineError(Exception):
    """Base class: the month cannot be published."""


class RetrievalError(PipelineError):
    """A source could not be retrieved completely (network, truncated body, bad status)."""


class RawIntegrityError(PipelineError):
    """A preserved raw file no longer matches its manifest checksum."""


class SchemaContractError(PipelineError):
    """A required column is missing or changed type. Stop: metrics would be wrong silently."""


class CompletenessError(PipelineError):
    """The file is readable but incomplete or is the wrong file (row count, day coverage, month)."""


class ReconciliationError(PipelineError):
    """File trip counts disagree with independently published control totals."""


class ValidationThresholdError(PipelineError):
    """Too many rows fail ERROR-level business rules for the file to be trusted."""
