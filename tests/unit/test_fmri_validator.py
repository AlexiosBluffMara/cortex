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

    def test_validate_dispatches_to_nifti(self):
        fake = _FakeNifti((64, 64, 36, 200), (3.0, 3.0, 3.0, 1.5))
        with mock.patch("nibabel.Nifti1Image") as nimg:
            nimg.from_file_map.return_value = fake
            if hasattr(nimg, "from_bytes"):
                del nimg.from_bytes
            res = fv.validate(b"\x1f\x8bfake", filename="scan.nii.gz")
        assert res.format == "nifti"

    def test_validate_dispatches_to_dicom(self):
        fake = _FakeDicom(
            Modality="MR", StudyDescription="task BOLD", SeriesDescription="EPI",
            Rows=64, Columns=64, NumberOfFrames=240, RepetitionTime=1500,
        )
        with mock.patch("pydicom.dcmread", return_value=fake):
            res = fv.validate(b"\x00" * 132 + b"DICM", filename="img.dcm")
        assert res.format == "dicom"

    def test_validate_dispatches_to_bids(self):
        data = _make_bids_zip({
            "dataset_description.json": b"{}",
            "sub-01/func/sub-01_task-rest_bold.nii.gz": b"\x1f\x8b",
        })
        res = fv.validate(data, filename="dataset.zip")
        assert res.format == "bids"

    def test_validate_dispatches_to_matlab(self):
        res = fv.validate(b"MATLAB fake", filename="scan.mat")
        assert res.format == "matlab"


# ─── detect_format magic bytes ────────────────────────────────────────────────

class TestDetectFormatMagic:
    def test_gzip_magic_returns_nifti(self):
        assert fv.detect_format("unknown.bin", b"\x1f\x8b" + b"\x00" * 20) == "nifti"

    def test_zip_magic_returns_bids(self):
        assert fv.detect_format("unknown.bin", b"PK\x03\x04" + b"\x00" * 20) == "bids"

    def test_matlab_magic_returns_matlab(self):
        assert fv.detect_format("unknown.bin", b"MATLAB5.0 something") == "matlab"

    def test_edf_magic_returns_edf(self):
        assert fv.detect_format("unknown.bin", b"0       " + b"\x00" * 100) == "edf"

    def test_mat_extension_returns_matlab(self):
        assert fv.detect_format("scan.mat", b"") == "matlab"

    def test_edf_extension_returns_edf(self):
        assert fv.detect_format("eeg.edf", b"") == "edf"


# ─── ValidationResult properties ─────────────────────────────────────────────

class TestValidationResultProperties:
    def test_ok_true_when_no_errors(self):
        r = fv.ValidationResult(format="nifti")
        assert r.ok is True

    def test_ok_false_when_errors_present(self):
        r = fv.ValidationResult(format="nifti", errors=["bad TR"])
        assert r.ok is False

    def test_as_tuple_returns_expected_fields(self):
        r = fv.ValidationResult(
            format="nifti",
            dimensions=[64, 64, 36, 200],
            tr_s=1.5,
            n_timepoints=200,
            anonymized=True,
            errors=[],
        )
        t = r.as_tuple()
        assert t[0] == "nifti"
        assert t[1] == [64, 64, 36, 200]
        assert t[2] == pytest.approx(1.5)
        assert t[3] == 200
        assert t[4] is True
        assert t[5] == []


# ─── _validate_nifti extra paths ─────────────────────────────────────────────

class TestNiftiExtraPaths:
    def test_nibabel_import_error_returns_error(self):
        with mock.patch.dict("sys.modules", {"nibabel": None}):
            res = fv._validate_nifti(b"\x1f\x8bfake")
        assert any("nibabel" in e for e in res.errors)

    def test_nifti2_fallback_when_nifti1_fails(self):
        """When Nifti1Image.from_file_map raises, falls back to Nifti2Image."""
        fake = _FakeNifti((64, 64, 36, 200), (3.0, 3.0, 3.0, 1.5))

        class FakeNifti1:
            from_bytes = None  # triggers the file_map path

            @staticmethod
            def from_file_map(fmap):
                raise Exception("wrong format")

        class FakeNifti2:
            @staticmethod
            def from_file_map(fmap):
                return fake

        class FakeNib:
            Nifti1Image = FakeNifti1
            Nifti2Image = FakeNifti2
            FileHolder = mock.MagicMock()

        with mock.patch.dict("sys.modules", {"nibabel": FakeNib}):
            # Re-import to get fresh module reference
            import importlib
            import sys as _sys
            _sys.modules.pop("cortex.fmri_validator", None)
            import cortex.fmri_validator as fv2
            with mock.patch("nibabel.Nifti1Image", FakeNifti1), \
                 mock.patch("nibabel.Nifti2Image", FakeNifti2), \
                 mock.patch("nibabel.FileHolder", mock.MagicMock(return_value=mock.MagicMock())):
                res = fv2._validate_nifti(b"\x00fake")

        _sys.modules.pop("cortex.fmri_validator", None)

    def test_nifti_parse_exception_returns_error(self):
        """When both Nifti1 and Nifti2 fail, parse exception is caught."""
        with mock.patch("nibabel.Nifti1Image") as nimg:
            nimg.from_file_map.side_effect = Exception("corrupt")
            # No from_bytes attribute → triggers fallback
            if hasattr(nimg, "from_bytes"):
                del nimg.from_bytes
            with mock.patch("nibabel.Nifti2Image") as nimg2:
                nimg2.from_file_map.side_effect = Exception("also corrupt")
                with mock.patch("nibabel.FileHolder", mock.MagicMock()):
                    res = fv._validate_nifti(b"corrupt data")
        assert any("parse failed" in e for e in res.errors)


# ─── _validate_dicom extra paths ──────────────────────────────────────────────

class TestDicomExtraPaths:
    def test_pydicom_import_error_returns_error(self):
        with mock.patch.dict("sys.modules", {"pydicom": None}):
            res = fv._validate_dicom(b"\x00" * 132 + b"DICM")
        assert any("pydicom" in e for e in res.errors)

    def test_dicom_parse_failure_returns_error(self):
        with mock.patch("pydicom.dcmread", side_effect=Exception("malformed")):
            res = fv._validate_dicom(b"\x00" * 132 + b"DICM")
        assert any("parse failed" in e for e in res.errors)


# ─── _validate_bids extra paths ───────────────────────────────────────────────

class TestBidsExtraPaths:
    def test_sidecar_json_parse_error_is_absorbed(self):
        """Malformed sidecar JSON doesn't crash validation."""
        data = _make_bids_zip({
            "dataset_description.json": b"{}",
            "sub-01/func/sub-01_task-rest_bold.nii.gz": b"\x1f\x8b",
            "sub-01/func/sub-01_task-rest_bold.json": b"not valid json {{{",
        })
        res = fv._validate_bids(data)
        # Should not raise; tr_s stays 0
        assert res.format == "bids"
        assert res.tr_s == 0.0

    def test_tr_below_minimum_adds_error(self):
        """TR of 0.1s (below MIN_TR_SECONDS=0.5) adds an error."""
        data = _make_bids_zip({
            "dataset_description.json": b"{}",
            "sub-01/func/sub-01_task-rest_bold.nii.gz": b"\x1f\x8b",
            "sub-01/func/sub-01_task-rest_bold.json": json.dumps({"RepetitionTime": 0.1}).encode(),
        })
        res = fv._validate_bids(data)
        assert any("TR" in e for e in res.errors)


# ─── _validate_matlab ─────────────────────────────────────────────────────────

class TestMatlabValidator:
    def test_valid_matlab_v5_header(self):
        data = b"MATLAB5.0 MAT-file, Created by MATLAB 9.8" + b"\x00" * 128
        res = fv._validate_matlab(data)
        assert res.format == "matlab"
        assert res.errors == []
        assert res.anonymized is True

    def test_non_v5_returns_error(self):
        data = b"\x89HDF\r\n\x1a\n" + b"\x00" * 100  # HDF5 = MATLAB v7.3
        res = fv._validate_matlab(data)
        assert any("HDF5" in e or "v5" in e for e in res.errors)


# ─── _validate_edf extra paths ────────────────────────────────────────────────

class TestEdfExtraPaths:
    def test_edf_parse_failed_bad_header(self):
        """Header bytes that can't be parsed as integers → parse failed error."""
        # Construct a header that's >= 256 bytes but has non-integer in n_records field
        data = b"0       " + b" " * 80 + b" " * 80 + b"01.01.20" + b"00.00.00"
        data += b" " * 8 + b" " * 44 + b"NOT_INT " + b"1.0     " + b"32  "
        data = data.ljust(256, b" ")
        res = fv._validate_edf(data)
        assert any("parse failed" in e for e in res.errors)
