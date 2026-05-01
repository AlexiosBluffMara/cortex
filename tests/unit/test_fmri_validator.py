"""Unit tests for cortex.fmri_validator.

Mocks nibabel / pydicom / zipfile so the tests run without real fMRI data.
"""
from __future__ import annotations

import io
import json
import struct
import zipfile
from unittest import mock

import pytest

from cortex import fmri_validator as fv

pytestmark = pytest.mark.unit


# ─── format detection ─────────────────────────────────────────────────────────

class TestDetectFormat:
    def test_nifti_by_extension(self):
        assert fv.detect_format("scan.nii.gz", b"") == "nifti"
        assert fv.detect_format("scan.nii", b"") == "nifti"

    def test_dicom_by_extension(self):
        assert fv.detect_format("img.dcm", b"") == "dicom"
        assert fv.detect_format("img.dicom", b"") == "dicom"

    def test_bids_zip_by_extension(self):
        assert fv.detect_format("dataset.zip", b"") == "bids"

    def test_dicom_by_magic(self):
        # 128 byte preamble + DICM
        head = b"\x00" * 128 + b"DICM"
        assert fv.detect_format("unknown.bin", head) == "dicom"

    def test_unknown_falls_through(self):
        assert fv.detect_format("scan.tiff", b"random") == "unknown"


# ─── NIfTI happy path / rejections ────────────────────────────────────────────

class _FakeHdr:
    def __init__(self, zooms):
        self._zooms = zooms

    def get_zooms(self):
        return self._zooms


class _FakeNifti:
    def __init__(self, shape, zooms):
        self.shape = shape
        self.header = _FakeHdr(zooms)


class TestNifti:
    def test_valid_4d_volume(self):
        fake = _FakeNifti((64, 64, 36, 200), (3.0, 3.0, 3.0, 1.5))
        with mock.patch.object(fv, "_validate_nifti", wraps=fv._validate_nifti):
            with mock.patch("nibabel.Nifti1Image") as nimg:
                nimg.from_file_map.return_value = fake
                # We patch the from_file_map path; signal that from_bytes path is absent
                if hasattr(nimg, "from_bytes"):
                    del nimg.from_bytes
                res = fv._validate_nifti(b"\x1f\x8bfake")
        assert res.format == "nifti"
        assert res.errors == []
        assert res.n_timepoints == 200
        assert res.tr_s == pytest.approx(1.5)

    def test_rejects_3d_volume(self):
        fake = _FakeNifti((64, 64, 36), (3.0, 3.0, 3.0))
        with mock.patch("nibabel.Nifti1Image") as nimg:
            nimg.from_file_map.return_value = fake
            if hasattr(nimg, "from_bytes"):
                del nimg.from_bytes
            res = fv._validate_nifti(b"\x1f\x8bfake")
        assert any("4D" in e for e in res.errors)

    def test_rejects_too_few_timepoints(self):
        fake = _FakeNifti((64, 64, 36, 10), (3.0, 3.0, 3.0, 2.0))
        with mock.patch("nibabel.Nifti1Image") as nimg:
            nimg.from_file_map.return_value = fake
            if hasattr(nimg, "from_bytes"):
                del nimg.from_bytes
            res = fv._validate_nifti(b"\x1f\x8bfake")
        assert any("timepoints" in e for e in res.errors)

    def test_rejects_low_tr(self):
        fake = _FakeNifti((64, 64, 36, 200), (3.0, 3.0, 3.0, 0.1))
        with mock.patch("nibabel.Nifti1Image") as nimg:
            nimg.from_file_map.return_value = fake
            if hasattr(nimg, "from_bytes"):
                del nimg.from_bytes
            res = fv._validate_nifti(b"\x1f\x8bfake")
        assert any("TR" in e for e in res.errors)


# ─── DICOM ────────────────────────────────────────────────────────────────────

class _FakeDicom:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class TestDicom:
    def test_valid_bold(self):
        fake = _FakeDicom(
            Modality="MR", StudyDescription="task-rest BOLD",
            SeriesDescription="EPI run-01",
            Rows=64, Columns=64, NumberOfFrames=240, RepetitionTime=1500,
        )
        with mock.patch("pydicom.dcmread", return_value=fake):
            res = fv._validate_dicom(b"\x00" * 132 + b"DICM")
        assert res.errors == []
        assert res.n_timepoints == 240
        assert res.tr_s == pytest.approx(1.5)
        assert res.anonymized is True

    def test_rejects_non_mr_modality(self):
        fake = _FakeDicom(
            Modality="CT", StudyDescription="head",
            Rows=64, Columns=64, NumberOfFrames=1, RepetitionTime=0,
        )
        with mock.patch("pydicom.dcmread", return_value=fake):
            res = fv._validate_dicom(b"\x00" * 132 + b"DICM")
        assert any("modality" in e.lower() for e in res.errors)

    def test_phi_present_marks_not_anonymized(self):
        fake = _FakeDicom(
            Modality="MR", StudyDescription="task BOLD",
            SeriesDescription="EPI", PatientName="Doe^Jane",
            Rows=64, Columns=64, NumberOfFrames=240, RepetitionTime=1500,
        )
        with mock.patch("pydicom.dcmread", return_value=fake):
            res = fv._validate_dicom(b"\x00" * 132 + b"DICM")
        assert res.anonymized is False


# ─── BIDS zip ─────────────────────────────────────────────────────────────────

def _make_bids_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in files.items():
            zf.writestr(name, payload)
    return buf.getvalue()


class TestBids:
    def test_valid_bids_dataset(self):
        data = _make_bids_zip({
            "dataset_description.json": json.dumps({"Name": "test", "BIDSVersion": "1.6"}).encode(),
            "sub-01/func/sub-01_task-rest_bold.nii.gz": b"\x1f\x8b",
            "sub-01/func/sub-01_task-rest_bold.json": json.dumps({"RepetitionTime": 1.5}).encode(),
        })
        res = fv._validate_bids(data)
        assert res.errors == []
        assert res.format == "bids"
        assert res.tr_s == pytest.approx(1.5)

    def test_rejects_missing_dataset_description(self):
        data = _make_bids_zip({
            "sub-01/func/sub-01_task-rest_bold.nii.gz": b"\x1f\x8b",
        })
        res = fv._validate_bids(data)
        assert any("dataset_description" in e for e in res.errors)

    def test_rejects_zip_with_no_bold(self):
        data = _make_bids_zip({
            "dataset_description.json": b"{}",
            "sub-01/anat/sub-01_T1w.nii.gz": b"\x1f\x8b",
        })
        res = fv._validate_bids(data)
        assert any("bold" in e.lower() for e in res.errors)

    def test_rejects_garbage_bytes(self):
        res = fv._validate_bids(b"not a zip at all")
        assert any("zip" in e.lower() for e in res.errors)


# ─── EDF ──────────────────────────────────────────────────────────────────────

def _make_edf_header(n_records: int = 600, dur: float = 1.0, n_signals: int = 32,
                     patient: str = "X X X X") -> bytes:
    def pad(s, n):
        return s.ljust(n)[:n].encode("ascii")

    return (
        pad("0", 8)                 # version
        + pad(patient, 80)          # patient id
        + pad("X X X X", 80)        # recording id
        + pad("01.01.20", 8)        # date
        + pad("00.00.00", 8)        # time
        + pad("256", 8)             # header bytes
        + pad("", 44)               # reserved
        + pad(str(n_records), 8)    # num records
        + pad(f"{dur}", 8)          # dur per record
        + pad(str(n_signals), 4)    # num signals
    )


class TestEdf:
    def test_valid_edf_header(self):
        data = _make_edf_header()
        res = fv._validate_edf(data)
        assert res.format == "edf"
        assert res.errors == []
        assert res.n_timepoints == 600
        assert res.anonymized is True

    def test_rejects_truncated_edf(self):
        res = fv._validate_edf(b"0" * 50)
        assert any("truncated" in e for e in res.errors)


# ─── top-level dispatch ───────────────────────────────────────────────────────

class TestDispatch:
    def test_oversized_file_rejected_immediately(self):
        # Simulate a > 2 GB upload by mocking len()
        class BigBytes:
            def __len__(self):
                return fv.MAX_BYTES + 1

            def __getitem__(self, key):
                return b""

        res = fv.validate(BigBytes(), filename="huge.nii.gz")
        assert any("limit" in e.lower() for e in res.errors)

    def test_unknown_format_returns_error(self):
        res = fv.validate(b"random bytes", filename="mystery.bin")
        assert any("unsupported" in e.lower() for e in res.errors)

    def test_format_hint_overrides_extension(self):
        # An EDF body but nii.gz extension — hint forces EDF parser
        res = fv.validate(_make_edf_header(), format_hint="edf", filename="weird.nii.gz")
        assert res.format == "edf"
