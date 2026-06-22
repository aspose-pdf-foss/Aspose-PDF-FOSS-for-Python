"""Compatibility ``Document`` mirroring a subset of the Aspose.PDF API.

Load/save error semantics are aligned with :mod:`aspose_pdf.document`
(no silent swallowing on construction or load).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO, List, Optional, Union

from aspose_pdf.engine.simple_pdf import SimplePdf, _effective_encryption_password
from aspose_pdf.exceptions import AsposePdfException, PdfSecurityException


class Document:
    """Pythonic wrapper for PDF document lifecycle and core operations.

    Mirrors the Aspose.PDF ``Document`` API.
    """

    # ---------------------------------------------------------------------
    # Construction & internal state
    # ---------------------------------------------------------------------
    def __init__(self, *args, **kwargs) -> None:
        """Create a new :class:`Document` instance.

        Stores the constructor arguments for possible later inspection and
        initializes a minimal in‑memory representation of a PDF document.

        If the first positional argument is a load source (path-like or
        bytes-like), :meth:`load_from` is called and **errors propagate** (no
        silent fallback to an empty document).
        """
        self._init_args = args
        self._init_kwargs = kwargs
        self._disposed: bool = False
        self._engine_doc: Optional[SimplePdf] = SimplePdf()

        # Initialize defaults
        self._pages: List[Any] = (
            self._engine_doc.pages if self._engine_doc is not None else []
        )
        self._encrypted: bool = False
        self._password: Optional[str] = None
        self._metadata: dict = {}
        self._optimizations: List[str] = []

        # Public attributes mirroring the Aspose.PDF API surface. Defaults are
        # assigned before any load so that loading populates them rather than
        # clobbering the freshly loaded state.
        self.file_name: Any = None  # maps_from=FileName
        self.info: Any = None  # maps_from=Info
        self.metadata: Any = None  # maps_from=Metadata
        self.id: Any = None  # maps_from=Id
        self.version: Any = None  # maps_from=Version
        self.pages: Any = self._pages  # maps_from=Pages, type=PageCollection
        self.outlines: Any = None  # maps_from=Outlines
        self.named_destinations: Any = None  # maps_from=NamedDestinations
        self.form: Any = None  # maps_from=Form
        self.is_encrypted: Any = self._encrypted  # maps_from=IsEncrypted
        self.permissions: Any = None  # maps_from=Permissions
        self.is_pdfa_compliant: Any = None  # maps_from=IsPdfaCompliant
        self.is_pdfua_compliant: Any = None  # maps_from=IsPdfUaCompliant

        if args and args[0]:
            pwd = kwargs.get("password")
            self.load_from(args[0], password=pwd)

    # ---------------------------------------------------------------------
    # Core document operations
    # ---------------------------------------------------------------------
    def load_from(
        self,
        source: Union[str, bytes, bytearray, Path, BinaryIO],
        *,
        password: Optional[str] = None,
    ) -> "Document":
        """Load a document from *source* using native engine."""
        eff_pwd = _effective_encryption_password(password)
        self._password = eff_pwd if eff_pwd is not None else password

        self._disposed = False

        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.is_file():
                raise FileNotFoundError(f"File not found: {path}")
            self._engine_doc = SimplePdf.from_file(path, password=password)
            self.file_name = str(path)
        elif isinstance(source, (bytes, bytearray)):
            self._engine_doc = SimplePdf.from_bytes(bytes(source), password=password)
            self.file_name = None
        elif hasattr(source, "read"):
            data = source.read()
            if not isinstance(data, (bytes, bytearray)):
                raise TypeError("stream read() must return bytes")
            self._engine_doc = SimplePdf.from_bytes(bytes(data), password=password)
            self.file_name = getattr(source, "name", None)
        else:
            raise TypeError(
                "source must be str, Path, bytes, or a readable binary stream"
            )

        if eff_pwd and self._engine_doc:
            self._engine_doc.decrypt(eff_pwd)

        self._encrypted = self._engine_doc.encrypted if self._engine_doc else False
        self._pages = self._engine_doc.pages
        self.pages = self._pages
        self.is_encrypted = self._encrypted
        return self

    def save(self, destination: Any, *, overwrite: bool = False) -> "Document":
        """Save the document to *destination* using native engine."""
        if self._disposed:
            raise AsposePdfException("Cannot save a disposed document")

        if self._engine_doc is None:
            raise AsposePdfException("No document loaded")

        # Sync pages back to engine doc
        self._engine_doc.pages = self._pages
        self._engine_doc.encrypted = self._encrypted
        self._engine_doc.password = self._password

        if hasattr(destination, "write"):
            destination.write(self._engine_doc.to_bytes())
        else:
            path = Path(destination)
            if path.exists() and not overwrite:
                raise FileExistsError(f"File already exists: {path}")
            self._engine_doc.save(path)
        return self

    def close(self, *args, **kwargs):
        """Alias of :meth:`dispose`."""
        return self.dispose(*args, **kwargs)

    def dispose(self, *args, **kwargs) -> None:
        """Release resources associated with this document.

        After disposal, most operations will raise ``RuntimeError``.
        """
        if self._disposed:
            return
        self._disposed = True
        if self._engine_doc is not None:
            self._engine_doc.dispose()
            self._engine_doc = None
        self._pages = []
        self._metadata.clear()
        self._optimizations.clear()

    def merge(self, *documents: "Document") -> "Document":
        """Merge the supplied *documents* into the current one.

        Pages from each provided document are appended to ``self._pages``.
        The method returns ``self`` to enable method chaining.
        """
        for doc in documents:
            if not isinstance(doc, Document):
                raise TypeError("merge expects Document instances")
            self._pages.extend(doc._pages)
        return self

    def optimize(self, *, compress_images: bool = True) -> "Document":
        """Perform generic optimizations on the document.

        Delegates to the underlying engine and records the request for
        introspection.
        """
        if self._disposed:
            raise AsposePdfException("Cannot optimize a disposed document")
        if self._engine_doc is None:
            raise AsposePdfException("No document loaded")
        self._engine_doc.optimize()
        self._optimizations.append("optimize:compress_images=" + str(compress_images))
        return self

    def optimize_resources(self, *, remove_unused: bool = True) -> "Document":
        """Optimize shared resources such as fonts and images."""
        if self._disposed:
            raise AsposePdfException("Cannot optimize a disposed document")
        if self._engine_doc is None:
            raise AsposePdfException("No document loaded")
        self._engine_doc.optimize()
        self._optimizations.append(
            "optimize_resources:remove_unused=" + str(remove_unused)
        )
        return self

    def repair(self) -> "Document":
        """Attempt to repair the document's structure via the engine."""
        if self._disposed:
            raise AsposePdfException("Cannot repair a disposed document")
        if self._engine_doc is None:
            raise AsposePdfException("No document loaded")
        self._engine_doc.repair()
        self._metadata["repaired"] = True
        return self

    def flatten(self) -> "Document":
        """Flatten form fields and annotations via the engine."""
        if self._disposed:
            raise AsposePdfException("Cannot flatten a disposed document")
        if self._engine_doc is None:
            raise AsposePdfException("No document loaded")
        self._engine_doc.flatten()
        self._metadata["flattened"] = True
        return self

    def free_memory(self) -> None:
        """Clear any cached data that might hold onto memory."""
        # Clear cached page and optimization data.
        self._pages = []
        self._optimizations = []

    def encrypt(self, password: str) -> "Document":
        """Encrypt the document with the given *password*."""
        if self._engine_doc is None:
            raise AsposePdfException("No document loaded")
        self._encrypted = True
        self._password = password
        self._engine_doc.encrypt(password)
        return self

    def decrypt(self, password: str) -> "Document":
        """Decrypt the document if *password* matches the stored one."""
        if not self._encrypted:
            return self
        if password != self._password:
            raise PdfSecurityException("Incorrect password for decryption")
        self._encrypted = False
        self._password = None
        if self._engine_doc is not None:
            self._engine_doc.decrypt(password)
        return self

    def change_passwords(self, old_password: str, new_password: str) -> "Document":
        """Change the document's password from *old_password* to *new_password*."""
        if not self._encrypted:
            raise PdfSecurityException("Document is not encrypted")
        if old_password != self._password:
            raise PdfSecurityException("Old password does not match")
        self._password = new_password
        if self._engine_doc is not None:
            self._engine_doc.change_passwords(old_password, new_password)
        return self

    def validate(self) -> bool:
        """Return ``True`` if the document is structurally valid."""
        if self._disposed or self._engine_doc is None:
            return False
        return self._engine_doc.validate()

    def check(self) -> bool:
        """Alias for :meth:`validate` kept for API compatibility."""
        return self.validate()

    # ---------------------------------------------------------------------
    # Helper properties
    # ---------------------------------------------------------------------
    @property
    def is_disposed(self) -> bool:
        """Expose the disposal state of the document."""
        return self._disposed

    @property
    def page_count(self) -> int:
        """Number of pages currently in the document."""
        return len(self._pages)

    # The public attributes initialised in __init__ are retained for
    # Aspose.PDF API compatibility.
