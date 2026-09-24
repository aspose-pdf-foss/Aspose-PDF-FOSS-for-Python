"""Evidence-backed DocMDP and PAdES checks for actual PDF revisions."""

from __future__ import annotations

from aspose_pdf.engine import dss, timestamp
from aspose_pdf.engine import signature_revisions as rev
from aspose_pdf.engine.cos import PdfName, PdfString
from aspose_pdf.engine.pdf_parser_cos import pdf_header_version
from aspose_pdf.engine.signature_validator import (
    _load_der_certs,
    _normalise_trust_roots,
    embedded_long_term_valid,
)
from aspose_pdf.exceptions import PdfParseException, PdfResourceLimitException
from aspose_pdf.validation import ValidationMethod, ValidationMode


def inspect_signature(signature, options, material):
    """Return signed policy, policy errors, subfilter and verified time evidence.

    The dataclass also accepts detached CMS fixtures without a PDF container.
    These cannot acquire a document timestamp or archive level.
    """
    data = signature.reference_data
    if pdf_header_version(data) is None:
        if signature.docmdp_level is not None:
            raise ValueError("certification requires a complete PDF revision")
        return None, [], signature.sub_filter, None, False
    end = signature.byte_range[2] + signature.byte_range[3]
    kwargs = dict(budget=signature._load_budget, encryption=signature._decryption)
    signed = rev.revisions(data[:end], end, **kwargs)[0]
    values = rev.signature_values(signed.doc, signature._load_budget)
    value = next(
        (
            v
            for v in values
            if rev.covered_signature(
                data, signed, v, signature.contents, signature.byte_range
            )
        ),
        None,
    )
    if value is None:
        raise ValueError(
            "Signature ByteRange must cover its complete revision except its own Contents"
        )
    sub = rev.entry(signed.doc, value, "SubFilter")
    subfilter = sub.name.lstrip("/") if isinstance(sub, PdfName) else None
    level = rev.certification_level(signed, signature.contents, signature.byte_range)
    if level is None and signature.docmdp_level is not None:
        raise ValueError("certification is not bound by the signed catalog")
    try:
        chain = rev.revisions(data, end, **kwargs)
    except PdfResourceLimitException:
        raise
    except (ValueError, TypeError, KeyError, IndexError, PdfParseException) as exc:
        errors = [f"certification revision validation failed: {exc}"] if level else []
        return level, errors, subfilter, None, False
    errors = rev.check_docmdp(chain, level, signature._load_budget) if level else []
    if not options.check_timestamp or errors:
        return level, errors, subfilter, None, False

    roots = _normalise_trust_roots(options)
    check_revocation = (
        options.check_revocation or options.validation_method == ValidationMethod.LTIP
    )
    by_end = {revision.end: revision for revision in chain}
    evidence = None
    archive = False
    # Only actual AcroForm signatures qualify; strings in content or metadata
    # cannot advertise an archive timestamp. Inspect the timestamp's own
    # revision as well, so unsigned replacements of its dictionary do not count.
    latest = chain[-1].doc
    for candidate in rev.signature_values(latest, signature._load_budget):
        if rev.entry(latest, candidate, "SubFilter") != PdfName("ETSI.RFC3161"):
            continue
        try:
            br = rev.signature_range(latest, candidate)
            candidate_end = br[2] + br[3]
            revision = by_end.get(candidate_end)
            contents = rev.entry(latest, candidate, "Contents")
            if (
                revision is None
                or candidate_end <= end
                or br[1] < end
                or not isinstance(contents, PdfString)
            ):
                continue
            from .simple_pdf import _trim_der_padding

            token = _trim_der_padding(contents.value)
            signed_value = next(
                (
                    v
                    for v in rev.signature_values(revision.doc, signature._load_budget)
                    if rev.covered_signature(data, revision, v, token, br)
                ),
                None,
            )
            if signed_value is None or rev.entry(
                revision.doc, signed_value, "Type"
            ) != PdfName("DocTimeStamp"):
                continue
            if rev.entry(revision.doc, signed_value, "SubFilter") != PdfName(
                "ETSI.RFC3161"
            ):
                continue
            covered = data[: br[1]] + data[br[2] : candidate_end]
            ts = timestamp.validate_timestamp_token(
                token,
                covered,
                trust_roots=roots,
                extra_certs=_load_der_certs(material.certs),
                use_system_trust=options.use_system_trust,
                check_revocation=check_revocation,
                embedded_crls=material.crls,
                embedded_ocsps=material.ocsps,
                validation_mode=options.validation_mode,
                timeout=options.network_timeout,
            )
            if not ts.verified:
                continue
            offline_ts = timestamp.validate_timestamp_token(
                token,
                covered,
                trust_roots=roots,
                extra_certs=_load_der_certs(material.certs),
                use_system_trust=options.use_system_trust,
                check_revocation=True,
                embedded_crls=material.crls,
                embedded_ocsps=material.ocsps,
                validation_mode=ValidationMode.OFFLINE,
            )
            if offline_ts.verified:
                ts = offline_ts
            if evidence is None or ts.gen_time < evidence.gen_time:
                evidence = ts
            # Archive evidence is checked again with ONLY the DSS covered by
            # this token. Data added after it cannot retroactively confer LTA.
            held = dss.read_dss(data[:candidate_end], **kwargs)
            archive_ts = timestamp.validate_timestamp_token(
                token,
                covered,
                trust_roots=roots,
                extra_certs=_load_der_certs(held.certs),
                use_system_trust=options.use_system_trust,
                check_revocation=True,
                embedded_crls=held.crls,
                embedded_ocsps=held.ocsps,
                validation_mode=ValidationMode.OFFLINE,
            )
            if not archive_ts.verified:
                continue
            from . import cms

            info = cms.parse_signed_data(signature.contents)
            if info.timestamp_token_der:
                earlier = timestamp.verify_timestamp_token(
                    info.timestamp_token_der, info.signature
                )
                if not earlier.verified or earlier.gen_time > archive_ts.gen_time:
                    continue
            archive |= embedded_long_term_valid(
                signature.contents,
                options,
                held.certs,
                held.crls,
                held.ocsps,
                document_timestamp=archive_ts,
            )
        except PdfResourceLimitException:
            raise
        except (ValueError, TypeError, KeyError, IndexError):
            # An unusable later timestamp does not invalidate the original
            # approval signature, but cannot promote its profile either.
            continue
    return level, errors, subfilter, evidence, archive


def check_timestamp_coverage(signature):
    """Require an actual DocTimeStamp covering its complete PDF revision."""
    if pdf_header_version(signature.reference_data) is None:
        return
    end = signature.byte_range[2] + signature.byte_range[3]
    signed = rev.revisions(
        signature.reference_data[:end],
        end,
        budget=signature._load_budget,
        encryption=signature._decryption,
    )[0]
    for value in rev.signature_values(signed.doc, signature._load_budget):
        if (
            rev.entry(signed.doc, value, "Type") == PdfName("DocTimeStamp")
            and rev.entry(signed.doc, value, "SubFilter") == PdfName("ETSI.RFC3161")
            and rev.covered_signature(
                signature.reference_data,
                signed,
                value,
                signature.contents,
                signature.byte_range,
            )
        ):
            return
    raise ValueError(
        "Document timestamp ByteRange does not cover its complete revision and own Contents"
    )
