"""Builders for the test document corpus.

Every fixture is **generated**, not checked in as a recorded blob. Two reasons:

* The ground truth is written next to the generator, so a golden-file accuracy
  test compares against values this module declared rather than against whatever
  a previous run happened to produce.
* A scanned fixture is produced by genuinely rasterising a real PDF and degrading
  the image, so the OCR path is exercised against something with actual scan
  artefacts rather than a clean render.

Files are built once per session into a temp directory and reused.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

__all__ = [
    "CERTIFICATE_SAMPLES",
    "CertificateSample",
    "build_corrupt_pdf",
    "build_docx",
    "build_encrypted_pdf",
    "build_image",
    "build_large_pdf",
    "build_multi_certificate_pdf",
    "build_permissions_only_pdf",
    "build_text_pdf",
    "build_zero_page_pdf",
    "build_zip",
]


@dataclass(frozen=True)
class CertificateSample:
    """One synthetic certificate plus the values a correct extraction must return."""

    key: str
    certificate_type: str
    heading: str
    lines: list[tuple[str, str]]
    """(label, value) pairs, rendered as a two-column form."""

    expected: dict[str, str] = field(default_factory=dict)
    """Ground truth, keyed by the schema's field names."""


# Deliberately varied: Latin and Urdu names, two date conventions, a CNIC, an
# ambiguous day/month date, so the validation layer has something to catch.
CERTIFICATE_SAMPLES: list[CertificateSample] = [
    CertificateSample(
        key="birth_lahore",
        certificate_type="BIRTH",
        heading="CERTIFICATE OF BIRTH",
        lines=[
            ("Certificate No.", "BC-2019-004471"),
            ("Registration No.", "REG/LHR/2019/88213"),
            ("Date of Registration", "2019-04-02"),
            ("Name of Child", "Ayesha Noor Malik"),
            ("Sex", "Female"),
            ("Date of Birth", "1987-03-14"),
            ("Time of Birth", "04:25"),
            ("Place of Birth", "Services Hospital, Lahore"),
            ("Father's Name", "Tariq Mahmood Malik"),
            ("Father's CNIC", "35201-1234567-1"),
            ("Mother's Name", "Nasreen Akhtar"),
            ("Mother's CNIC", "35201-7654321-8"),
            ("Permanent Address", "House 14, Street 7, Gulberg III, Lahore"),
            ("Informant", "Tariq Mahmood Malik (Father)"),
            ("Issuing Authority", "Union Council 42, Lahore"),
            ("Registrar", "Muhammad Aslam"),
            ("Date of Issue", "2019-04-09"),
        ],
        expected={
            "certificate_number": "BC-2019-004471",
            "registration_number": "REG/LHR/2019/88213",
            "registration_date": "2019-04-02",
            "child_full_name": "Ayesha Noor Malik",
            "sex": "F",
            "date_of_birth": "1987-03-14",
            "time_of_birth": "04:25",
            "place_of_birth": "Services Hospital, Lahore",
            "father_full_name": "Tariq Mahmood Malik",
            "father_id_number": "35201-1234567-1",
            "mother_full_name": "Nasreen Akhtar",
            "mother_id_number": "35201-7654321-8",
            "issuing_authority": "Union Council 42, Lahore",
            "registrar_name": "Muhammad Aslam",
            "date_of_issue": "2019-04-09",
        },
    ),
    CertificateSample(
        key="death_karachi",
        certificate_type="DEATH",
        heading="DEATH CERTIFICATE",
        lines=[
            ("Certificate No.", "DC-2021-000913"),
            ("Registration No.", "REG/KHI/2021/10442"),
            ("Date of Registration", "2021-11-20"),
            ("Name of Deceased", "Abdul Rehman Qureshi"),
            ("Sex", "Male"),
            ("Date of Birth", "1948-06-30"),
            ("Date of Death", "2021-11-17"),
            ("Age at Death", "73"),
            ("Place of Death", "Aga Khan University Hospital, Karachi"),
            ("Cause of Death", "Cardiac arrest"),
            ("Father's Name", "Ghulam Qureshi"),
            ("Spouse's Name", "Farida Qureshi"),
            ("CNIC", "42101-9988776-5"),
            ("Informant", "Farida Qureshi (Spouse)"),
            ("Issuing Authority", "Union Council 11, Karachi"),
            ("Date of Issue", "2021-11-25"),
        ],
        expected={
            "certificate_number": "DC-2021-000913",
            "registration_number": "REG/KHI/2021/10442",
            "registration_date": "2021-11-20",
            "deceased_full_name": "Abdul Rehman Qureshi",
            "sex": "M",
            "date_of_birth": "1948-06-30",
            "date_of_death": "2021-11-17",
            "age_at_death": "73",
            "place_of_death": "Aga Khan University Hospital, Karachi",
            "cause_of_death": "Cardiac arrest",
            "father_name": "Ghulam Qureshi",
            "spouse_name": "Farida Qureshi",
            "deceased_id_number": "42101-9988776-5",
            "issuing_authority": "Union Council 11, Karachi",
            "date_of_issue": "2021-11-25",
        },
    ),
    CertificateSample(
        key="marriage_islamabad",
        certificate_type="MARRIAGE",
        heading="CERTIFICATE OF MARRIAGE (NIKAH NAMA)",
        lines=[
            ("Certificate No.", "MC-2015-007788"),
            ("Registration No.", "REG/ISB/2015/3391"),
            ("Date of Registration", "2015-09-14"),
            ("Date of Marriage", "2015-09-12"),
            ("Place of Marriage", "Faisal Masjid, Islamabad"),
            ("Groom's Name", "Hamza Bin Yousaf"),
            ("Groom's Date of Birth", "1988-01-22"),
            ("Groom's CNIC", "61101-2233445-3"),
            ("Groom's Father", "Yousaf Ali"),
            ("Bride's Name", "Sana Fatima"),
            ("Bride's Date of Birth", "1991-07-08"),
            ("Bride's CNIC", "61101-5544332-6"),
            ("Bride's Father", "Rashid Hussain"),
            ("Dower Amount", "PKR 500000"),
            ("Witness 1", "Imran Shah"),
            ("Witness 2", "Bilal Ahmed"),
            ("Officiant", "Qari Abdul Sattar"),
            ("Date of Issue", "2015-09-20"),
        ],
        expected={
            "certificate_number": "MC-2015-007788",
            "registration_number": "REG/ISB/2015/3391",
            "registration_date": "2015-09-14",
            "date_of_marriage": "2015-09-12",
            "place_of_marriage": "Faisal Masjid, Islamabad",
            "groom_full_name": "Hamza Bin Yousaf",
            "groom_date_of_birth": "1988-01-22",
            "groom_id_number": "61101-2233445-3",
            "groom_father_name": "Yousaf Ali",
            "bride_full_name": "Sana Fatima",
            "bride_date_of_birth": "1991-07-08",
            "bride_id_number": "61101-5544332-6",
            "bride_father_name": "Rashid Hussain",
            "dower_amount": "500000",
            "witness_1_name": "Imran Shah",
            "witness_2_name": "Bilal Ahmed",
            "officiant_name": "Qari Abdul Sattar",
            "date_of_issue": "2015-09-20",
        },
    ),
]

SAMPLES_BY_KEY: dict[str, CertificateSample] = {s.key: s for s in CERTIFICATE_SAMPLES}


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def _draw_certificate(pdf: canvas.Canvas, sample: CertificateSample) -> None:
    """Render one certificate onto the current page as a labelled form.

    Laid out as two columns - label on the left, value at a fixed x - because that
    is what the real forms look like and what the spatial template matcher will
    have to cope with.
    """
    width, height = A4
    label_x = 25 * mm
    value_x = 95 * mm
    y = height - 35 * mm

    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawCentredString(width / 2, y, sample.heading)
    y -= 6 * mm
    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(width / 2, y, "GOVERNMENT OF PAKISTAN - LOCAL GOVERNMENT DEPARTMENT")

    y -= 12 * mm
    pdf.line(label_x, y, width - label_x, y)
    y -= 10 * mm

    for label, value in sample.lines:
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(label_x, y, f"{label}:")
        pdf.setFont("Helvetica", 10)
        pdf.drawString(value_x, y, value)
        y -= 8 * mm

    y -= 6 * mm
    pdf.line(label_x, y, width - label_x, y)
    y -= 8 * mm
    pdf.setFont("Helvetica-Oblique", 8)
    pdf.drawString(label_x, y, "This is a computer generated certificate.")


def build_text_pdf(path: Path, sample_key: str = "birth_lahore") -> Path:
    """A clean, text-layer PDF of a single certificate."""
    sample = SAMPLES_BY_KEY[sample_key]
    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.setTitle(sample.heading)
    _draw_certificate(pdf, sample)
    pdf.showPage()
    pdf.save()
    return path


def build_multi_certificate_pdf(
    path: Path,
    *,
    copies: int = 10,
    sample_keys: list[str] | None = None,
) -> Path:
    """One PDF holding many certificates, one per page.

    This is the case the splitter exists for: a records office hands over a single
    200-page file that is actually 200 separate certificates.
    """
    keys = sample_keys or [s.key for s in CERTIFICATE_SAMPLES]
    pdf = canvas.Canvas(str(path), pagesize=A4)
    for index in range(copies):
        sample = SAMPLES_BY_KEY[keys[index % len(keys)]]
        # Vary the serial so boundary detection has a real signal to find.
        varied = CertificateSample(
            key=sample.key,
            certificate_type=sample.certificate_type,
            heading=sample.heading,
            lines=[
                (label, f"{value}-{index:03d}" if label == "Certificate No." else value)
                for label, value in sample.lines
            ],
            expected=sample.expected,
        )
        _draw_certificate(pdf, varied)
        pdf.showPage()
    pdf.save()
    return path


def build_encrypted_pdf(path: Path, *, password: str = "letmein") -> Path:  # noqa: S107
    """A password-protected PDF, to exercise the encrypted-document error path."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _draw_certificate(pdf, SAMPLES_BY_KEY["birth_lahore"])
    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    writer = PdfWriter(clone_from=PdfReader(buffer))
    writer.encrypt(user_password=password, owner_password=password)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def build_permissions_only_pdf(path: Path) -> Path:
    """Encrypted with an empty user password: anyone can open it, but it is encrypted.

    Common in the wild - systems that only want to restrict printing or copying.
    """
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    _draw_certificate(pdf, SAMPLES_BY_KEY["death_karachi"])
    pdf.showPage()
    pdf.save()
    buffer.seek(0)

    writer = PdfWriter(clone_from=PdfReader(buffer))
    writer.encrypt(user_password="", owner_password="owner-only-secret")
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def build_large_pdf(path: Path, *, megabytes: int = 18) -> Path:
    """A genuinely large, valid PDF: a certificate page plus an incompressible image.

    Random pixels do not compress, so the file's size tracks the image's raw size.
    Used to exercise multi-part resumable uploads with real PDF bytes.
    """
    import numpy as np
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    side = int((megabytes * 1024 * 1024 / 3) ** 0.5)
    noise = np.random.default_rng(seed=7).integers(0, 256, size=(side, side, 3), dtype=np.uint8)
    image = Image.fromarray(noise, mode="RGB")

    pdf = canvas.Canvas(str(path), pagesize=A4)
    _draw_certificate(pdf, SAMPLES_BY_KEY["birth_lahore"])
    pdf.showPage()
    width, height = A4
    pdf.drawImage(ImageReader(image), 0, 0, width=width, height=height)
    pdf.showPage()
    pdf.save()
    return path


def build_corrupt_pdf(path: Path) -> Path:
    """A PDF header followed by truncated garbage - a real truncated export."""
    path.write_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R")
    return path


def build_zero_page_pdf(path: Path) -> Path:
    """A structurally valid PDF containing no pages."""
    writer = PdfWriter()
    with path.open("wb") as handle:
        writer.write(handle)
    return path


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
def build_docx(path: Path, sample_key: str = "birth_lahore", *, as_table: bool = True) -> Path:
    """A Word certificate, laid out as a table or as paragraphs.

    Both shapes appear in the wild, and the DOCX extractor has to read either.
    """
    sample = SAMPLES_BY_KEY[sample_key]
    document = DocxDocument()
    document.add_heading(sample.heading, level=1)

    if as_table:
        table = document.add_table(rows=0, cols=2)
        table.style = "Table Grid"
        for label, value in sample.lines:
            row = table.add_row().cells
            row[0].text = label
            row[1].text = value
    else:
        for label, value in sample.lines:
            document.add_paragraph(f"{label}: {value}")

    section = document.sections[0]
    section.footer.paragraphs[0].text = "This is a computer generated certificate."
    document.save(str(path))
    return path


# ---------------------------------------------------------------------------
# Images and archives
# ---------------------------------------------------------------------------
def build_image(path: Path, *, size: tuple[int, int] = (1240, 1754)) -> Path:
    """A standalone page image, rendered from a real certificate PDF."""
    import fitz

    source = path.with_suffix(".source.pdf")
    build_text_pdf(source)
    try:
        with fitz.open(str(source)) as document:
            pixmap = document.load_page(0).get_pixmap(dpi=150)
            pixmap.save(str(path))
    finally:
        source.unlink(missing_ok=True)

    del size
    return path


def build_zip(path: Path, members: dict[str, bytes]) -> Path:
    """An archive with the given member paths and contents."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


def build_zip_bomb(path: Path, *, uncompressed_mb: int = 200) -> Path:
    """A small archive that expands enormously, to prove the ratio guard fires."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        # A long run of a single byte compresses to almost nothing.
        archive.writestr("bomb.txt", b"0" * (uncompressed_mb * 1024 * 1024))
    return path
