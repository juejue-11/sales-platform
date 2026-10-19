"""Application errors shared by HTTP and business operations."""

class ServiceError(RuntimeError):
    pass


class NotFoundError(ServiceError):
    pass


class ConflictError(ServiceError):
    pass
