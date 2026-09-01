"""The compatibility surfaces, and what each of them was decided to be.

The package keeps some names from the wider Aspose.PDF API so that ported code
keeps importing. The rule they are meant to follow is that constructing one is
allowed and *using* one raises, rather than quietly doing nothing — a caller
should find out at the call, not from a blank page.

Three surfaces were left undecided. ``PrinterSettings`` already followed the
rule. ``IPath`` did not: it accepted path segments and dropped them.
``PerformanceLogger`` was the opposite problem — a working stopwatch nothing
fed, so it is now what the renderer records its phase timings into.
"""

from __future__ import annotations

import io

import pytest

from aspose_pdf import Document, UnsupportedFeatureException
from aspose_pdf.presentation import FillMode, IMatrix, IPath
from aspose_pdf.printing import Duplex, PrinterSettings, PrintRange
from aspose_pdf.visualization import PerformanceLogger, VirtualizationPerformance

# ---------------------------------------------------------------------------
# IPath: it used to swallow the path it was given
# ---------------------------------------------------------------------------


def test_a_path_can_still_be_constructed():
    # Ported code that only builds one keeps working, which is the whole point
    # of keeping the name.
    path = IPath()
    assert (path.current_x, path.current_y) == (0.0, 0.0)
    assert path.fill_mode == FillMode.ALTERNATE


def test_appending_to_a_path_says_it_cannot_be_drawn():
    path = IPath()
    with pytest.raises(UnsupportedFeatureException, match="presentation drawing"):
        path.append_cubic_bezier_curve(0, 0, 1, 1, 2, 2)


def test_the_refusal_names_the_method_and_points_at_the_documentation():
    path = IPath()
    with pytest.raises(UnsupportedFeatureException) as caught:
        path.append_cubic_bezier_curve(0, 0, 1, 1, 2, 2)
    message = str(caught.value)
    assert "IPath.append_cubic_bezier_curve" in message
    assert "supported-features.md" in message


def test_the_refusal_is_catchable_as_not_implemented():
    with pytest.raises(NotImplementedError):
        IPath().append_cubic_bezier_curve(0, 0, 1, 1, 2, 2)


def test_the_fill_modes_are_the_two_pdf_fill_rules():
    assert (FillMode.ALTERNATE, FillMode.WINDING) == ("Alternate", "Winding")
    path = IPath()
    path.fill_mode = FillMode.WINDING
    assert path.fill_mode == FillMode.WINDING
    with pytest.raises(ValueError):
        path.fill_mode = "Sometimes"


def test_the_matrix_is_a_real_matrix():
    # IMatrix is arithmetic, not a placeholder: it keeps working.
    matrix = IMatrix(2.0, 0.0, 0.0, 3.0, 5.0, 7.0)
    assert (matrix.a, matrix.d) == (2.0, 3.0)
    matrix.translate(1.0, -2.0)
    assert (matrix.e, matrix.f) == (6.0, 5.0)


def test_a_path_still_carries_a_transform():
    path = IPath()
    matrix = IMatrix()
    path.transform = matrix
    assert path.transform is matrix


def test_geometry_on_a_page_goes_through_the_page_api():
    """The thing IPath is not: drawing that actually reaches the page."""
    document = Document()
    page = document.pages.add()
    page.draw_rectangle(60, 500, 200, 100, fill_color=(0.9, 0.1, 0.1))
    page.draw_line(60, 300, 500, 460, line_width=3)

    buffer = io.BytesIO()
    document.save(buffer)
    assert b" re" in buffer.getvalue()  # the rectangle operator


# ---------------------------------------------------------------------------
# PerformanceLogger: now fed by the renderer
# ---------------------------------------------------------------------------


def _drawn_page() -> Document:
    document = Document()
    page = document.pages.add()
    page.add_text("Timed render", 60, 700, font_size=18)
    page.draw_rectangle(60, 500, 120, 60, fill_color=(0.2, 0.4, 0.8))
    return document


def test_a_render_records_its_phases_into_a_logger():
    document = _drawn_page()
    timings = PerformanceLogger()

    document.pages[0].render(dpi=36, performance=timings)

    assert set(timings.timings) == {"content", "interpret", "annotations", "downsample"}
    assert all(value >= 0.0 for value in timings.timings.values())
    assert timings.total == pytest.approx(sum(timings.timings.values()))


def test_a_render_without_a_logger_records_nothing():
    document = _drawn_page()
    # The point of the option: a render nobody asked to measure does no timing.
    assert document.pages[0].render(dpi=36).width > 0


def test_two_renders_accumulate_into_the_same_logger():
    document = _drawn_page()
    timings = PerformanceLogger()

    document.pages[0].render(dpi=36, performance=timings)
    first = timings.timings["interpret"]
    document.pages[0].render(dpi=36, performance=timings)

    assert timings.timings["interpret"] >= first


def test_each_logger_keeps_its_own_numbers():
    # Caller-owned rather than process-global, so two renders cannot interleave.
    document = _drawn_page()
    one, two = PerformanceLogger(), PerformanceLogger()
    document.pages[0].render(dpi=36, performance=one)
    assert two.timings == {}


def test_a_phase_is_still_recorded_when_it_raises():
    timings = PerformanceLogger()
    with pytest.raises(ZeroDivisionError), timings.measure("doomed"):
        1 / 0
    assert "doomed" in timings.timings


def test_the_summary_is_slowest_first_and_lands_in_the_log():
    timings = PerformanceLogger()
    timings.record("slow", 0.5)
    timings.record("quick", 0.001)

    lines = timings.summarise()

    assert lines == ["slow: 500ms", "quick: 1ms"]
    assert timings.log == lines


def test_recording_the_same_phase_twice_adds_up():
    timings = PerformanceLogger()
    timings.record("paint", 0.25)
    timings.record("paint", 0.25)
    assert timings.timings["paint"] == pytest.approx(0.5)


def test_the_free_form_log_still_works():
    timings = PerformanceLogger()
    timings.log_line("hello")
    assert timings.log == ["hello"]


def test_the_global_stopwatch_is_still_a_working_stopwatch():
    # Kept for ported code, and documented as something the package never
    # writes to: timing a library into module-level state would interleave two
    # documents rendered at once.
    VirtualizationPerformance.reset()
    VirtualizationPerformance.start("phase")
    VirtualizationPerformance.stop()
    assert VirtualizationPerformance.get_elapsed_time("phase") >= 0.0

    logger = PerformanceLogger()
    VirtualizationPerformance.print_statistics(logger)
    assert logger.log and logger.log[0].startswith("phase: ")
    VirtualizationPerformance.reset()


def test_rendering_does_not_write_to_the_global_stopwatch():
    VirtualizationPerformance.reset()
    _drawn_page().pages[0].render(dpi=36, performance=PerformanceLogger())
    assert VirtualizationPerformance.get_elapsed_time("interpret") == 0.0


# ---------------------------------------------------------------------------
# PrinterSettings: already followed the rule
# ---------------------------------------------------------------------------


def test_printer_settings_can_be_built_but_not_saved_with(tmp_path):
    settings = PrinterSettings(copies=2, duplex=Duplex.TUMBLE, print_range=PrintRange.ALL_PAGES)
    assert settings.copies == 2

    document = Document()
    document.pages.add()
    with pytest.raises(UnsupportedFeatureException, match="printing"):
        document.save(str(tmp_path / "out.pdf"), settings)


def test_printer_settings_still_validates_its_own_values():
    with pytest.raises(ValueError, match="copies"):
        PrinterSettings(copies=0)
