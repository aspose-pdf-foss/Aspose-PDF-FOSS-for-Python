"""Split PDFs using the one-based Aspose.PDF page API.

These scenarios follow the Python via .NET split-document examples. They use the
FOSS package import and close each opened document with a context manager.

Reference: https://github.com/aspose-pdf/Aspose.PDF-for-Python-via-.NET/blob/main/
examples/working_with_documents/example_split_pdf_document.py
"""

from pathlib import Path

from aspose_pdf import Document


def split_documents(infile, outdir):
    with Document(infile) as document:
        for page_num in range(1, len(document.pages) + 1):
            with Document() as new_document:
                new_document.pages.add(document.pages[page_num])
                new_document.save(Path(outdir) / f"Page_{page_num}.pdf")


def split_documents_into_two_parts(infile, outdir):
    with Document(infile) as document:
        total_pages = len(document.pages)
    mid_point = total_pages // 2
    with Document(infile) as first_document:
        first_document.pages.delete(range(mid_point + 1, total_pages + 1))
        first_document.save(Path(outdir) / "Part_1.pdf")
    with Document(infile) as second_document:
        second_document.pages.delete(range(1, mid_point + 1))
        second_document.save(Path(outdir) / "Part_2.pdf")


def split_documents_every_n_pages(infile, outdir, pages_per_part=3):
    with Document(infile) as document:
        total_pages = len(document.pages)
        part_index = 1
        for start_page in range(1, total_pages + 1, pages_per_part):
            end_page = min(start_page + pages_per_part - 1, total_pages)
            with Document() as part_document:
                for page_num in range(start_page, end_page + 1):
                    part_document.pages.add(document.pages[page_num])
                part_document.save(
                    Path(outdir) / f"Every_{pages_per_part}_Part_{part_index}.pdf"
                )
            part_index += 1


def split_documents_by_page_ranges(infile, outdir):
    with Document(infile) as document:
        total_pages = len(document.pages)
        ranges = [(1, 3), (4, 6), (7, None)]
        for index, (start_page, end_page) in enumerate(ranges, start=1):
            if start_page > total_pages:
                continue
            effective_end = (
                total_pages if end_page is None else min(end_page, total_pages)
            )
            if start_page > effective_end:
                continue
            with Document() as range_document:
                for page_num in range(start_page, effective_end + 1):
                    range_document.pages.add(document.pages[page_num])
                range_document.save(
                    Path(outdir) / f"Range_{index}_{start_page}_to_{effective_end}.pdf"
                )


def split_documents_first_page_and_rest(infile, outdir):
    with Document(infile) as document:
        total_pages = len(document.pages)
        if total_pages == 0:
            return
        with Document() as first_page_document:
            first_page_document.pages.add(document.pages[1])
            first_page_document.save(Path(outdir) / "First_Page.pdf")
        if total_pages == 1:
            return
        with Document() as remaining_pages_document:
            for page_num in range(2, total_pages + 1):
                remaining_pages_document.pages.add(document.pages[page_num])
            remaining_pages_document.save(Path(outdir) / "Remaining_Pages.pdf")


def split_documents_last_page_and_rest(infile, outdir):
    with Document(infile) as document:
        total_pages = len(document.pages)
        if total_pages == 0:
            return
        with Document() as last_page_document:
            last_page_document.pages.add(document.pages[total_pages])
            last_page_document.save(Path(outdir) / "Last_Page.pdf")
        if total_pages == 1:
            return
        document.pages.delete(total_pages)
        document.save(Path(outdir) / "Previous_Pages.pdf")


def split_documents_into_three_parts(infile, outdir):
    with Document(infile) as document:
        total_pages = len(document.pages)
        if total_pages == 0:
            return
        part_size = max(1, (total_pages + 2) // 3)
        for part_index in range(3):
            start_page = part_index * part_size + 1
            end_page = min((part_index + 1) * part_size, total_pages)
            if start_page > total_pages:
                break
            with Document() as part_document:
                for page_num in range(start_page, end_page + 1):
                    part_document.pages.add(document.pages[page_num])
                part_document.save(Path(outdir) / f"Three_Parts_{part_index + 1}.pdf")


def split_documents_custom_page_groups(infile, outdir):
    with Document(infile) as document:
        total_pages = len(document.pages)
        groups = [[1, 2, 5], [3, 4, 6, 7]]
        for group_index, group in enumerate(groups, start=1):
            valid_pages = [number for number in group if 1 <= number <= total_pages]
            if not valid_pages:
                continue
            with Document() as group_document:
                for page_num in valid_pages:
                    group_document.pages.add(document.pages[page_num])
                group_document.save(Path(outdir) / f"Custom_Group_{group_index}.pdf")


def split_documents_with_stable_filenames(infile, outdir):
    with Document(infile) as document:
        for page_num in range(1, len(document.pages) + 1):
            with Document() as new_document:
                new_document.pages.add(document.pages[page_num])
                new_document.save(Path(outdir) / f"Page_{page_num:03d}.pdf")


def split_documents_odd_even_pages(infile, outdir):
    with Document(infile) as document:
        total_pages = len(document.pages)
        with Document() as odd_document:
            for page_num in range(1, total_pages + 1, 2):
                odd_document.pages.add(document.pages[page_num])
            odd_document.save(Path(outdir) / "Odd_Pages.pdf")
        with Document() as even_document:
            for page_num in range(2, total_pages + 1, 2):
                even_document.pages.add(document.pages[page_num])
            even_document.save(Path(outdir) / "Even_Pages.pdf")
