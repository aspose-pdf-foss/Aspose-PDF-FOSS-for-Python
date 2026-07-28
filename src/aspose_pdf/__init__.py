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
from .exceptions import (
    FontEmbeddingException,
    PdfResourceLimitException,
    UnsupportedFeatureException,
)
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
from .forms import (
    Field,
    FieldType,
    Form,
    FormType,
    UnsignedContent,
    UnsignedContentAbsorber,
)
from .load_limits import PdfLoadLimits
from .lowcode import (
    ByteArrayDataSource,
    DataSource,
    FileDataSource,
    MergeOptions,
    Merger,
    OperationResult,
    OptimizeOptions,
    Optimizer,
    PdfPlugin,
    Plugin,
    PluginOptions,
    ResultContainer,
    SplitOptions,
    Splitter,
    StreamDataSource,
    TextExtractor,
    TextExtractorOptions,
)
from .optimization import OptimizationOptions
from .pages import Page, PageCollection
from .pdfa import PdfAValidateOptions, PdfAValidationResult, PdfAValidator
from .pdfua import PdfUaValidateOptions, PdfUaValidationResult, PdfUaValidator
from .signature import PdfSignature
from .tagged import StructureElement, TaggedContent
from .text_layout import TextLayoutOptions
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
)
from .xmp import (
    parse as parse_xmp,
)
from .xmp import (
    serialize as serialize_xmp,
)

__all__ = [
    "Annotation",
    "AnnotationCollection",
    "AnnotationFlags",
    "AnnotationType",
    "ByteArrayDataSource",
    "CertificationLevel",
    "DataSource",
    "Document",
    "Field",
    "FieldType",
    "FileDataSource",
    "FileFontSource",
    "FileSpecification",
    "FolderFontSource",
    "FontDescriptor",
    "FontEmbeddingException",
    "FontRepository",
    "FontSource",
    "Form",
    "FormType",
    "LinkAnnotation",
    "MarkupAnnotation",
    "MemoryFontSource",
    "MergeOptions",
    "Merger",
    "NamespaceProvider",
    "OperationResult",
    "OptimizationOptions",
    "OptimizeOptions",
    "Optimizer",
    "Page",
    "PageCollection",
    "PdfAValidateOptions",
    "PdfAValidationResult",
    "PdfAValidator",
    "PdfExtractor",
    "PdfFileEditor",
    "PdfLoadLimits",
    "PdfPlugin",
    "PdfResourceLimitException",
    "PdfSignature",
    "PdfUaValidateOptions",
    "PdfUaValidationResult",
    "PdfUaValidator",
    "Plugin",
    "PluginOptions",
    "RasterizedPage",
    "ResultContainer",
    "RevocationStatus",
    "SplitOptions",
    "Splitter",
    "StreamDataSource",
    "StructureElement",
    "SystemFontSource",
    "TaggedContent",
    "TextExtractor",
    "TextExtractorOptions",
    "TextLayoutOptions",
    "TrustStatus",
    "UnsignedContent",
    "UnsignedContentAbsorber",
    "UnsupportedFeatureException",
    "ValidationMethod",
    "ValidationMode",
    "ValidationOptions",
    "ValidationResult",
    "ValidationStatus",
    "XmpArray",
    "XmpField",
    "XmpPacket",
    "XmpProperty",
    "XmpStruct",
    "__version__",
    "parse_xmp",
    "serialize_xmp",
]
