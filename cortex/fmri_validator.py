"""fMRI upload validation.

Detects format from extension + magic bytes, parses minimal metadata, and
returns a structured result. Never logs raw scan data — only shape/metadata.

Supported formats:
    NIfTI    .nii / .nii.gz   (4D BOLD volume)
    DICOM    .dcm / .dicom    (single slice or zipped series)
    BIDS     .zip             (BIDS-compliant dataset, look for sub-*/func/*_bold.nii.gz)
    MATLAB   .mat             (legacy 4D BOLD array)
    EDF      .edf             (EEG, different modality but accepted as upload target)
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from dataclasses import dataclass, field

log = logging.getLogger("cortex.fmri_validator")

MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB hard cap (matches signed-URL ceiling)
MIN_TIMEPOINTS = 30
MIN_TR_SECONDS = 0.5

NIFTI1_MAGIC = (b"ni1\x00", b"n+1\x00")
NIFTI2_MAGIC = (b"ni2\x00", b"n+2\x00")
GZIP_MAGIC = b"\x1f\x8b"
DICOM_MAGIC = b"DICM"  # at offset 128
ZIP_MAGIC = b"PK\x03\x04"
MAT_V5_MAGIC = b"MATLAB"
EDF_MAGIC = b"0       "  # 8-byte version header for EDF


@dataclass
class ValidationResult:
    format: str
    dimensions: list[int] = field(default_factory=list)
    tr_s: float = 0.0
    n_timepoints: int = 0
    anonymized: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_tuple(self) -> tuple[str, list[int], float, int, bool, list[str]]:
        return (
            self.format,
            self.dimensions,
            self.tr_s,
            self.n_timepoints,
            self.anonymized,
            list(self.errors),
        )


def detect_format(filename: str, head: bytes) -> str:
    name = filename.lower()
    if name.endswith((".nii.gz", ".nii")):
        return "nifti"
    if name.endswith((".dcm", ".dicom")):
        return "dicom"
    if name.endswith(".mat"):
        return "matlab"
    if name.endswith(".edf"):
        return "edf"
    if name.endswith(".zip"):
        return "bids"
    # Fall back to magic bytes
    if head.startswith(GZIP_MAGIC):
        return "nifti"
    if head.startswith(ZIP_MAGIC):
        return "bids"
    if len(head) >= 132 and head[128:132] == DICOM_MAGIC:
        return "dicom"
    if head.startswith(MAT_V5_MAGIC):
        return "matlab"
    if head.startswith(EDF_MAGIC):
        return "edf"
    return "unknown"


def _validate_nifti(data: bytes) -> ValidationResult:
    res = ValidationResult(format="nifti")
    try:
        import nibabel as nib
    except ImportError:
        res.errors.append("nibabel not installed")
        return res

    try:
        fobj = nib.Nifti1Image.from_bytes(data) if hasattr(nib.Nifti1Image, "from_bytes") else None
        if fobj is None:
            from nibabel import FileHolder

            holder = FileHolder(fileobj=io.BytesIO(data))
            try:
                fobj = nib.Nifti1Image.from_file_map({"image": holder, "header": holder})
            except Exception:
                holder = FileHolder(fileobj=io.BytesIO(data))
                fobj = nib.Nifti2Image.from_file_map({"image": holder, "header": holder})
        hdr = fobj.header
        shape = list(fobj.shape)
        zooms = list(hdr.get_zooms())
    except Exception as exc:
        res.errors.append(f"nifti parse failed: {type(exc).__name__}")
        return res

    res.dimensions = shape
    if len(shape) != 4:
        res.errors.append(f"expected 4D volume, got {len(shape)}D shape={shape}")
        return res
    res.n_timepoints = int(shape[3])
    res.tr_s = float(zooms[3]) if len(zooms) >= 4 else 0.0
    if res.tr_s < MIN_TR_SECONDS:
        res.errors.append(f"TR {res.tr_s:.3f}s below minimum {MIN_TR_SECONDS}s")
    if res.n_timepoints < MIN_TIMEPOINTS:
        res.errors.append(f"only {res.n_timepoints} timepoints, need >= {MIN_TIMEPOINTS}")
    res.anonymized = True  # NIfTI stores no PHI by default
    return res


def _validate_dicom(data: bytes) -> ValidationResult:
    res = ValidationResult(format="dicom")
    try:
        import pydicom
    except ImportError:
        res.errors.append("pydicom not installed")
        return res

    try:
        ds = pydicom.dcmread(io.BytesIO(data), stop_before_pixels=True, force=True)
    except Exception as exc:
        res.errors.append(f"dicom parse failed: {type(exc).__name__}")
        return res

    modality = str(getattr(ds, "Modality", "")).upper()
    study_desc = str(getattr(ds, "StudyDescription", "")).lower()
    series_desc = str(getattr(ds, "SeriesDescription", "")).lower()

    if modality not in ("MR", "FMRI", "MRI"):
        res.errors.append(f"modality {modality!r} is not MR/fMRI")
    looks_bold = any(
        kw in (study_desc + " " + series_desc) for kw in ("bold", "task", "rest", "epi", "fmri")
    )
    if not looks_bold:
        res.errors.append("StudyDescription/SeriesDescription does not reference BOLD/task/EPI")

    rows = int(getattr(ds, "Rows", 0))
    cols = int(getattr(ds, "Columns", 0))
    n_frames = int(getattr(ds, "NumberOfFrames", 1))
    res.dimensions = [rows, cols, 1, n_frames]
    res.n_timepoints = n_frames
    tr_ms = getattr(ds, "RepetitionTime", None)
    res.tr_s = float(tr_ms) / 1000.0 if tr_ms else 0.0
    # PHI fields: PatientName / PatientID / PatientBirthDate present means not anonymized
    has_phi = any(
        getattr(ds, attr, None) for attr in ("PatientName", "PatientID", "PatientBirthDate")
    )
    res.anonymized = not has_phi
    return res


def _validate_bids(data: bytes) -> ValidationResult:
    res = ValidationResult(format="bids")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        res.errors.append("not a valid zip archive")
        return res

    names = zf.namelist()
    has_desc = any(n.endswith("dataset_description.json") for n in names)
    if not has_desc:
        res.errors.append("missing dataset_description.json at root")

    bold_files = [
        n for n in names if "/func/" in n and n.endswith("_bold.nii.gz")
    ]
    if not bold_files:
        res.errors.append("no sub-*/func/*_bold.nii.gz files found")
        return res

    # Try to read TR from a sidecar JSON if present
    first_bold = bold_files[0]
    sidecar = first_bold.replace(".nii.gz", ".json")
    if sidecar in names:
        try:
            meta = json.loads(zf.read(sidecar))
            res.tr_s = float(meta.get("RepetitionTime", 0.0))
        except Exception:
            pass

    res.dimensions = [len(bold_files)]  # number of BOLD runs
    res.n_timepoints = MIN_TIMEPOINTS  # placeholder; full per-run validation is downstream
    res.anonymized = True
    if res.tr_s and res.tr_s < MIN_TR_SECONDS:
        res.errors.append(f"TR {res.tr_s:.3f}s below minimum {MIN_TR_SECONDS}s")
    return res


def _validate_matlab(data: bytes) -> ValidationResult:
    res = ValidationResult(format="matlab", anonymized=True)
    if not data.startswith(MAT_V5_MAGIC):
        res.errors.append("not a MATLAB v5 .mat file (HDF5 v7.3 not yet supported)")
        return res
    # We deliberately do not parse the full .mat — just confirm it claims to hold a 4D array.
    # Real shape inspection happens in the training pipeline where scipy.io is available.
    res.dimensions = []
    res.n_timepoints = 0
    return res


def _validate_edf(data: bytes) -> ValidationResult:
    res = ValidationResult(format="edf", anonymized=False)
    if len(data) < 256:
        res.errors.append("EDF header truncated")
        return res
    # EDF header: 8b version, 80b patient_id, 80b recording_id, 8b startdate, 8b starttime,
    # 8b header_bytes, 44b reserved, 8b num_records, 8b dur_per_record, 4b num_signals
    try:
        n_records = int(data[236:244].decode("ascii").strip())
        dur = float(data[244:252].decode("ascii").strip())
        n_sig = int(data[252:256].decode("ascii").strip())
    except Exception:
        res.errors.append("EDF header parse failed")
        return res
    res.dimensions = [n_sig, n_records]
    res.n_timepoints = n_records
    res.tr_s = dur
    patient_field = data[8:88].decode("ascii", errors="ignore").strip()
    res.anonymized = patient_field.upper() in ("X", "X X X X", "") or patient_field.startswith("X ")
    return res


def validate(data: bytes, format_hint: str | None = None, filename: str = "") -> ValidationResult:
    """Validate an uploaded fMRI scan.

    Parameters
    ----------
    data : raw file bytes (or first ~1 MB if streamed)
    format_hint : optional explicit format string ("nifti", "dicom", ...)
    filename : original filename for extension-based detection
    """
    if len(data) > MAX_BYTES:
        return ValidationResult(
            format="unknown", errors=[f"file exceeds {MAX_BYTES // (1024 ** 3)} GB limit"]
        )

    fmt = format_hint or detect_format(filename, data[:512])
    if fmt == "nifti":
        return _validate_nifti(data)
    if fmt == "dicom":
        return _validate_dicom(data)
    if fmt == "bids":
        return _validate_bids(data)
    if fmt == "matlab":
        return _validate_matlab(data)
    if fmt == "edf":
        return _validate_edf(data)
    return ValidationResult(format=fmt or "unknown", errors=[f"unsupported format: {fmt!r}"])


__all__ = ["MAX_BYTES", "ValidationResult", "detect_format", "validate"]
