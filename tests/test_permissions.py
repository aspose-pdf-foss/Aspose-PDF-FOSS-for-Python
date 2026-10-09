"""Access permissions as named flags over the ``/P`` word.

``/P`` is a signed 32-bit integer whose unused bits are all 1, so "everything
allowed" is ``-4`` and "nothing allowed" is ``-3904`` -- and combining two
permissions by hand gives ``20``, a ``/P`` with every reserved bit cleared,
which Table 22 does not allow. These tests cover the names, the arithmetic the
class does so a caller does not have to, and the fact that the result is still
the integer every existing caller passed around.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aspose_pdf import Document, PageSize, PdfFileSecurity, Permissions
from aspose_pdf.exceptions import PdfValidationException

ALL = -4
NOTHING = -3904
#: Every bit Table 22 defines: 4 + 8 + 16 + 32 + 256 + 512 + 1024 + 2048.
GRANTS = 3900

NAMES = (
    "print",
    "modify",
    "copy",
    "annotate",
    "fill_forms",
    "accessibility",
    "assemble",
    "print_high_resolution",
)


def _saved(**encrypt: object) -> bytes:
    document = Document()
    document.pages.add(PageSize.A4)
    document.pages[0].add_text("protected", 50, 700)
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    with Document(buffer.getvalue()) as reopened:
        reopened.encrypt("", "owner", **encrypt)
        out = io.BytesIO()
        reopened.save(out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# The numbers behind the names
# ---------------------------------------------------------------------------
class TestValues:
    def test_everything_is_minus_four(self):
        """Not ``-1``: Table 22 says bits 1 and 2 *shall be 0*."""
        assert Permissions.all() == ALL
        assert Permissions.all().is_everything
        assert ALL & 0b11 == 0

    def test_nothing_keeps_the_reserved_bits(self):
        value = Permissions.none()
        assert value == NOTHING
        assert value.is_nothing
        assert value & ~GRANTS == ALL & ~GRANTS

    @pytest.mark.parametrize(
        ("constant", "bit"),
        [
            ("PRINT", 4),
            ("MODIFY", 8),
            ("COPY", 16),
            ("ANNOTATE", 32),
            ("FILL_FORMS", 256),
            ("ACCESSIBILITY", 512),
            ("ASSEMBLE", 1024),
            ("PRINT_HIGH_RESOLUTION", 2048),
        ],
    )
    def test_the_constants_are_table_22_s_bits(self, constant, bit):
        assert getattr(Permissions, constant) == bit

    def test_the_constants_cover_every_defined_bit(self):
        assert Permissions.GRANTS == GRANTS

    def test_ored_constants_alone_would_be_a_broken_p(self):
        """Which is the mistake the class exists to make impossible."""
        by_hand = Permissions.PRINT | Permissions.COPY
        assert by_hand == 20
        assert by_hand & ~GRANTS == 0  # every reserved bit cleared
        assert Permissions.allowing(by_hand) != by_hand
        assert Permissions.allowing(by_hand).allowed == ("print", "copy")


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------
class TestBuilding:
    def test_allowing(self):
        value = Permissions.allowing("print", "print_high_resolution")
        assert value.allowed == ("print", "print_high_resolution")
        assert value.denied == (
            "modify",
            "copy",
            "annotate",
            "fill_forms",
            "accessibility",
            "assemble",
        )

    def test_denying(self):
        value = Permissions.denying("copy")
        assert not value.can_copy
        assert value.can_print and value.can_modify
        assert value == ALL & ~16

    def test_with_and_without(self):
        assert Permissions.none().with_("print").allowed == ("print",)
        assert not Permissions.all().without("assemble").can_assemble
        assert Permissions.all().without("copy", "modify").allowed == (
            "print",
            "annotate",
            "fill_forms",
            "accessibility",
            "assemble",
            "print_high_resolution",
        )

    def test_the_constants_work_as_arguments(self):
        assert Permissions.allowing(Permissions.PRINT).allowed == ("print",)
        assert Permissions.allowing(
            Permissions.PRINT, Permissions.COPY
        ).allowed == ("print", "copy")

    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            ("print", "print"),
            ("PRINT", "print"),
            (" Print ", "print"),
            ("fill_forms", "fill_forms"),
            ("fill-forms", "fill_forms"),
            ("fill forms", "fill_forms"),
            ("extract", "copy"),
            ("extract_for_accessibility", "accessibility"),
            ("print-high-resolution", "print_high_resolution"),
        ],
    )
    def test_spellings(self, spelling, expected):
        assert Permissions.allowing(spelling).allowed == (expected,)

    def test_of_passes_a_raw_value_through(self):
        assert Permissions.of(-3844) == -3844
        assert Permissions.of(Permissions.all()) == ALL

    @pytest.mark.parametrize("bad", ["fly", "", "printing"])
    def test_an_unknown_name_is_refused(self, bad):
        with pytest.raises(PdfValidationException, match="is not a permission"):
            Permissions.allowing(bad)

    @pytest.mark.parametrize("bad", [0, 3, 7, -4, 4096])
    def test_a_number_outside_table_22_is_refused(self, bad):
        with pytest.raises(PdfValidationException, match="Table 22 permission bit"):
            Permissions.allowing(bad)

    @pytest.mark.parametrize("bad", [True, 4.0, None, "4"])
    def test_an_unusable_flag_is_refused(self, bad):
        with pytest.raises(PdfValidationException):
            Permissions.allowing(bad)

    @pytest.mark.parametrize("bad", [True, 4.0, None, "-4"])
    def test_of_refuses_what_is_not_an_integer(self, bad):
        with pytest.raises(PdfValidationException, match="integer /P value"):
            Permissions.of(bad)

    def test_the_error_names_the_method(self):
        with pytest.raises(PdfValidationException, match="denying:"):
            Permissions.denying("fly")
        with pytest.raises(PdfValidationException, match="without:"):
            Permissions.all().without("fly")


# ---------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------
class TestAsking:
    def test_every_predicate(self):
        for name in NAMES:
            value = Permissions.allowing(name)
            predicate = {
                "accessibility": "can_extract_for_accessibility",
            }.get(name, f"can_{name}")
            assert getattr(value, predicate) is True, name
            assert getattr(Permissions.none(), predicate) is False, name

    def test_high_resolution_printing_qualifies_printing(self):
        """Bit 12 does not stand on its own, and qpdf reads it the same way.

        Granting it while bit 3 is clear is a document nobody may print; the
        value is written as asked rather than silently corrected, and
        ``can_print_faithfully`` is the question most callers mean.
        """
        alone = Permissions.allowing("print_high_resolution")
        assert alone.can_print_high_resolution
        assert not alone.can_print
        assert not alone.can_print_faithfully

        both = Permissions.allowing("print", "print_high_resolution")
        assert both.can_print_faithfully
        assert Permissions.all().can_print_faithfully
        assert not Permissions.denying("print").can_print_faithfully

    def test_allows_takes_several(self):
        value = Permissions.allowing("print", "copy")
        assert value.allows("print")
        assert value.allows("print", "copy")
        assert not value.allows("print", "assemble")

    def test_to_dict_covers_every_permission(self):
        value = Permissions.allowing("assemble")
        assert set(value.to_dict()) == set(NAMES)
        assert value.to_dict()["assemble"] is True
        assert value.to_dict()["print"] is False

    def test_allowed_and_denied_are_in_table_order(self):
        value = Permissions.of(-3844)
        assert value.allowed + value.denied == NAMES

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (Permissions.all(), "Permissions(-4: everything)"),
            (Permissions.none(), "Permissions(-3904: nothing)"),
            (Permissions.allowing("print"), "Permissions(-3900: print)"),
        ],
    )
    def test_repr_says_what_is_allowed(self, value, expected):
        assert repr(value) == expected


# ---------------------------------------------------------------------------
# It is still the integer it was
# ---------------------------------------------------------------------------
class TestStillAnInteger:
    def test_type_and_comparison(self):
        value = Permissions.denying("copy")
        assert isinstance(value, int)
        assert value == -20
        assert value < 0 and value != ALL

    def test_masking_and_combining(self):
        value = Permissions.all()
        assert value & Permissions.PRINT == Permissions.PRINT
        assert value & ~Permissions.COPY == -20

    def test_it_hashes_as_its_number(self):
        assert hash(Permissions.all()) == hash(ALL)
        assert {Permissions.all(): "x"}[ALL] == "x"

    def test_it_formats_as_its_number(self):
        assert f"{Permissions.all():d}" == "-4"
        assert int(Permissions.none()) == NOTHING


# ---------------------------------------------------------------------------
# Through the document
# ---------------------------------------------------------------------------
class TestDocument:
    def test_an_unencrypted_document_withholds_nothing(self):
        document = Document()
        document.pages.add(PageSize.A4)
        assert document.permissions == Permissions.all()
        assert document.permissions.is_everything
        document.close()

    def test_a_typed_value_round_trips(self):
        data = _saved(permissions=Permissions.denying("copy", "modify"))
        with Document(data, password="owner") as document:
            value = document.permissions
        assert isinstance(value, Permissions)
        assert value.denied == ("modify", "copy")

    def test_a_raw_value_still_works_and_comes_back_typed(self):
        data = _saved(permissions=-3904)
        with Document(data, password="owner") as document:
            assert document.permissions.is_nothing
            assert isinstance(document.permissions, Permissions)

    @pytest.mark.parametrize("name", NAMES)
    def test_each_permission_round_trips_on_its_own(self, name):
        data = _saved(permissions=Permissions.allowing(name))
        with Document(data, password="owner") as document:
            assert document.permissions.allowed == (name,)

    def test_a_bad_permissions_value_is_refused_before_encrypting(self):
        document = Document()
        document.pages.add(PageSize.A4)
        buffer = io.BytesIO()
        document.save(buffer)
        document.close()
        with Document(buffer.getvalue()) as reopened:
            with pytest.raises(PdfValidationException, match="integer /P value"):
                reopened.encrypt("", "owner", permissions="everything")

    def test_change_passwords_can_change_them(self):
        data = _saved(permissions=Permissions.all())
        with Document(data, password="owner") as document:
            document.change_passwords(
                "owner", "", "owner2", permissions=Permissions.denying("print")
            )
            out = io.BytesIO()
            document.save(out)
        with Document(out.getvalue(), password="owner2") as document:
            assert not document.permissions.can_print

    def test_change_passwords_keeps_them_by_default(self):
        data = _saved(permissions=Permissions.denying("print"))
        with Document(data, password="owner") as document:
            document.change_passwords("owner", "", "owner2")
            out = io.BytesIO()
            document.save(out)
        with Document(out.getvalue(), password="owner2") as document:
            assert not document.permissions.can_print

    def test_change_passwords_can_change_the_cipher_too(self):
        data = _saved(permissions=Permissions.all())
        with Document(data, password="owner") as document:
            document.change_passwords("owner", "", "owner2", algorithm="AES-128")
            out = io.BytesIO()
            document.save(out)
        with Document(out.getvalue(), password="owner2") as document:
            assert document.permissions.is_everything

    def test_the_facade_change_password_can_set_them(self):
        """It offered the argument all along, and could not pass it on."""
        data = _saved(permissions=Permissions.all())
        with Document(data, password="owner") as document:
            security = PdfFileSecurity()
            security.bind_pdf(document)
            assert security.change_password(
                "owner", "", "owner2", permissions=Permissions.denying("print")
            ), security.last_exception
            assert security.last_exception is None
            out = io.BytesIO()
            document.save(out)
        with Document(out.getvalue(), password="owner2") as reopened:
            assert not reopened.permissions.can_print

    def test_the_facade_change_password_can_set_the_cipher(self):
        data = _saved(permissions=Permissions.all())
        with Document(data, password="owner") as document:
            security = PdfFileSecurity()
            security.bind_pdf(document)
            assert security.change_password(
                "owner", "", "owner2", algorithm="AES-128"
            ), security.last_exception

    def test_the_security_facade_reports_them(self):
        data = _saved(permissions=Permissions.denying("assemble"))
        with Document(data, password="owner") as document:
            security = PdfFileSecurity()
            security.bind_pdf(document)
            assert isinstance(security.permissions, Permissions)
            assert not security.permissions.can_assemble

    def test_the_security_facade_can_set_them(self, tmp_path):
        source = tmp_path / "plain.pdf"
        document = Document()
        document.pages.add(PageSize.A4)
        document.save(source)
        document.close()
        security = PdfFileSecurity()
        security.bind_pdf(source)
        assert security.encrypt_file(
            "", "owner", permissions=Permissions.allowing("print")
        )
        target = tmp_path / "sealed.pdf"
        assert security.save(target)
        security.dispose()
        with Document(target, password="owner") as reopened:
            assert reopened.permissions.allowed == ("print",)

    def test_the_lowcode_options_take_a_typed_value(self):
        from aspose_pdf.lowcode import EncryptOptions

        options = EncryptOptions(
            "", "owner", permissions=Permissions.denying("copy")
        )
        assert isinstance(options.permissions, Permissions)
        assert not options.permissions.can_copy

    def test_the_lowcode_options_normalise_a_raw_value(self):
        from aspose_pdf.lowcode import EncryptOptions

        options = EncryptOptions("", "owner", permissions=-3904)
        assert isinstance(options.permissions, Permissions)
        assert options.permissions.is_nothing


# ---------------------------------------------------------------------------
# Another implementation's /P
# ---------------------------------------------------------------------------
class TestForeignPermissions:
    """``fixtures_permissions_qpdf_no_copy.pdf`` was encrypted by **qpdf**
    (through pikepdf) with extraction denied and everything else left alone, so
    it says what another implementation writes rather than what ours does.
    """

    def test_a_qpdf_written_p_reads_back_by_name(self):
        path = Path(__file__).with_name("fixtures_permissions_qpdf_no_copy.pdf")
        with Document(path, password="qpdfowner") as document:
            value = document.permissions
        assert isinstance(value, Permissions)
        assert not value.can_copy
        assert value.can_print and value.can_modify and value.can_annotate
        assert value.can_print_faithfully

    def test_no_bit_is_forced_or_cleared_on_the_way_in(self):
        """The value is the file's: this reports ``/P``, it does not tidy it."""
        path = Path(__file__).with_name("fixtures_permissions_qpdf_no_copy.pdf")
        with Document(path, password="qpdfowner") as document:
            raw = document._engine_pdf.P
            assert int(document.permissions) == raw


# ---------------------------------------------------------------------------
# The public-key handler, whose word is *not* /P
# ---------------------------------------------------------------------------
def _certificate():
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "reader")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, __import__("cryptography.hazmat.primitives.hashes",
                              fromlist=["SHA256"]).SHA256())
    )
    return certificate, key


class TestPublicKeyPermissions:
    def test_all_permissions_is_minus_one_on_purpose(self):
        """The envelope's word is not ``/P``: bit 1 is required, bit 2 means
        "may change encryption settings", bit 13 "a missing MAC is acceptable".
        ``-1`` grants all three, which ``-4`` would not -- so the two defaults
        differ because the two words do, not because one of them is wrong.
        """
        from aspose_pdf.engine.pubsec import normalize_permissions
        from aspose_pdf.recipients import ALL_PERMISSIONS

        assert ALL_PERMISSIONS == -1
        assert normalize_permissions(-1) != normalize_permissions(-4)
        # The difference is bit 2 alone.
        assert normalize_permissions(-1) ^ normalize_permissions(-4) == 1 << 1

    def test_the_grant_bits_mean_the_same_in_both_words(self):
        from aspose_pdf.engine.pubsec import normalize_permissions

        denied = Permissions.denying("copy")
        normalised = normalize_permissions(int(denied))
        assert not normalised & Permissions.COPY
        assert normalised & Permissions.PRINT

    def test_a_recipient_takes_a_typed_value(self):
        from aspose_pdf import Recipient

        certificate, key = _certificate()
        document = Document()
        document.pages.add(PageSize.A4)
        document.pages[0].add_text("secret", 50, 700)
        buffer = io.BytesIO()
        document.save(buffer)
        document.close()

        with Document(buffer.getvalue()) as reopened:
            reopened.encrypt_for_recipients(
                [Recipient(certificate, permissions=Permissions.allowing("print"))]
            )
            out = io.BytesIO()
            reopened.save(out)

        with Document(
            out.getvalue(), certificate=certificate, private_key=key
        ) as opened:
            assert opened.permissions.can_print
            assert not opened.permissions.can_copy
