"""Production-facing public API for the ``aspose_pdf`` package."""

from ._version import __version__
from .annotations import (
    Annotation,
    AnnotationCollection,
    AnnotationFlags,
    AnnotationType,
    LinkAnnotation,
    MarkupAnnotation,
)
from .attachments import FileSpecification
from .document import Document
from .facades import PdfExtractor, PdfFileEditor
from .font_registry import FontDescriptor
from .font_repository import (
    FileFontSource,
    FolderFontSource,
    FontRepository,
    FontSource,
    MemoryFontSource,
    SystemFontSource,
)
from .forms import Field, Form, UnsignedContent, UnsignedContentAbsorber
from .lowcode import (
    ByteArrayDataSource,
    DataSource,
    FileDataSource,
    Merger,
    MergeOptions,
    OperationResult,
    Optimizer,
    OptimizeOptions,
    PdfPlugin,
    Plugin,
    PluginOptions,
    ResultContainer,
    Splitter,
    SplitOptions,
    StreamDataSource,
    TextExtractor,
    TextExtractorOptions,
)
from .optimization import OptimizationOptions
from .pages import Page, PageCollection
from .pdfa import PdfAValidateOptions, PdfAValidationResult, PdfAValidator
from .pdfua import PdfUaValidateOptions, PdfUaValidationResult, PdfUaValidator
from .signature import PdfSignature
from .validation import (
    CertificationLevel,
    RevocationStatus,
    TrustStatus,
    ValidationMethod,
    ValidationMode,
    ValidationOptions,
    ValidationResult,
    ValidationStatus,
)
from .visualization import RasterizedPage
from .xmp import (
    NamespaceProvider,
    XmpArray,
    XmpField,
    XmpPacket,
    XmpProperty,
    XmpStruct,
    parse as parse_xmp,
    serialize as serialize_xmp,
)

__all__ = [
    "__version__",
    "Annotation",
    "AnnotationCollection",
    "AnnotationFlags",
    "AnnotationType",
    "ByteArrayDataSource",
    "DataSource",
    "Document",
    "Field",
    "FileDataSource",
    "FileFontSource",
    "FileSpecification",
    "FolderFontSource",
    "FontDescriptor",
    "FontRepository",
    "FontSource",
    "Form",
    "LinkAnnotation",
    "MarkupAnnotation",
    "MemoryFontSource",
    "Merger",
    "MergeOptions",
    "OperationResult",
    "OptimizationOptions",
    "Optimizer",
    "OptimizeOptions",
    "PdfPlugin",
    "Plugin",
    "PluginOptions",
    "ResultContainer",
    "Splitter",
    "SplitOptions",
    "StreamDataSource",
    "SystemFontSource",
    "TextExtractor",
    "TextExtractorOptions",
    "Page",
    "PageCollection",
    "PdfAValidateOptions",
    "PdfAValidationResult",
    "PdfAValidator",
    "PdfExtractor",
    "PdfFileEditor",
    "PdfSignature",
    "PdfUaValidateOptions",
    "PdfUaValidationResult",
    "PdfUaValidator",
    "RasterizedPage",
    "UnsignedContent",
    "UnsignedContentAbsorber",
    "CertificationLevel",
    "RevocationStatus",
    "TrustStatus",
    "ValidationMethod",
    "ValidationMode",
    "ValidationOptions",
    "ValidationResult",
    "ValidationStatus",
    "NamespaceProvider",
    "XmpArray",
    "XmpField",
    "XmpPacket",
    "XmpProperty",
    "XmpStruct",
    "parse_xmp",
    "serialize_xmp",
]
