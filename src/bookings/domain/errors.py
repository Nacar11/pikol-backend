from typing import Literal

ErrorKind = Literal["invalid", "conflict"]


class BookingError(Exception):
    """A booking rule said no.

    HTTP-free on purpose: `kind` says whether the request was malformed for
    this booking ("invalid") or the booking's current state forbids it
    ("conflict"). The service layer maps those to 422 and 409.
    """

    def __init__(self, code: str, detail: str, kind: ErrorKind) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.kind = kind
