"""Recipients of a public-key (``/Adobe.PubSec``) encrypted document.

The standard security handler gates a document behind a password everyone with
access has to know and share. The public-key handler gates it behind
*certificates*: the producer names the recipients, and each one opens the file
with the private key they already hold. Nothing is shared, nothing is typed,
and revoking access to future documents means dropping a certificate from the
list.

Each recipient also gets its **own** permissions -- one reader may print and
another only read the same file -- which no password-based scheme can express::

    from cryptography import x509
    from aspose_pdf import Document, Permissions, Recipient

    auditor = x509.load_pem_x509_certificate(Path("auditor.pem").read_bytes())
    reviewer = x509.load_pem_x509_certificate(Path("reviewer.pem").read_bytes())

    with Document("report.pdf") as document:
        document.encrypt_for_recipients(
            [
                Recipient(auditor),                       # everything
                Recipient(
                    reviewer,
                    permissions=Permissions.allowing("print", "copy"),
                ),
            ]
        )
        document.save("report-sealed.pdf")

Opening one needs the certificate *and* its private key::

    with Document("report-sealed.pdf", certificate=cert, private_key=key) as doc:
        print(doc.pages[0].content)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Recipient"]

# Every permission bit granted. The public-key handler's word is not the
# standard handler's /P: bit 1 is required, bit 2 means "may change encryption
# settings", and bit 13 means "a missing PDF 2.0 MAC is acceptable", which this
# library's documents need because they carry no /AuthCode.
ALL_PERMISSIONS = -1


@dataclass(frozen=True)
class Recipient:
    """One certificate that may open the document, and what it may then do.

    Parameters
    ----------
    certificate:
        The recipient's ``cryptography`` :class:`~cryptography.x509.Certificate`.
        It must carry an RSA or EC public key. When it declares a ``keyUsage``
        extension, RSA certificates must permit ``keyEncipherment`` or
        ``dataEncipherment`` and EC certificates must permit ``keyAgreement``.
    permissions:
        This recipient's access flags, defaulting to every permission. A signed
        32-bit integer, or a :class:`~aspose_pdf.permissions.Permissions` built
        by name -- the eight permissions it knows sit at the same bit positions
        in this word as in the standard handler's ``/P`` (bit 3 print, 4 modify,
        5 copy, 6 annotate, 9 fill forms, 10 accessible extraction, 11 assemble,
        12 high-quality print). The differences are in the bits noted above, and
        they are normalised for you, so ``Permissions.denying("copy")`` says here
        what it says there.
    """

    certificate: Any
    permissions: int = ALL_PERMISSIONS

    def as_pair(self) -> tuple[Any, int]:
        """Return ``(certificate, permissions)`` for the engine."""
        return self.certificate, int(self.permissions)
