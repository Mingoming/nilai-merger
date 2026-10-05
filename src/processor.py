from __future__ import annotations

import io
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping

import pandas as pd

from .excel_utils import csv_to_df, df_to_excel
from .naming import (SimilarCandidate, clean_filename, filename_key,
                     safe_output_name, similar_group_candidates)

LogFn = Callable[[str], None]

RENAME_MAP = {
    "surname": "Kelas",
    "first name": "Nama",
    "email address": "NoPes",
    "grade/100.00": "Nilai",
}
DROP_COLS = {"institution", "department", "state", "started on", "completed", "time taken"}
REQUIRED_FINAL_COLS = {"Kelas", "Nama", "NoPes", "Nilai"}

MAX_ZIP_FILES = 2000
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024


@dataclass
class ProcessResult:
    zip_bytes: bytes
    output_filename: str
    audit: pd.DataFrame
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    total_input_zips: int = 0
    total_csv: int = 0
    total_mapel: int = 0


def _normalize_col(s) -> str:
    return " ".join(str(s).strip().casefold().split())


def _effective_schema(df: pd.DataFrame) -> set[str]:
    """Kolom yang harus kompatibel untuk digabung.

    Metadata Moodle yang memang dibuang pada tahap final (Institution, Department,
    State, Started on, Completed, Time taken) tidak ikut menentukan kompatibilitas.
    """
    return {
        _normalize_col(col)
        for col in df.columns
        if _normalize_col(col) not in DROP_COLS
    }


def _required_source_columns_ok(df: pd.DataFrame) -> tuple[bool, list[str]]:
    required = {"surname", "first name", "email address", "grade/100.00"}
    present = {_normalize_col(col) for col in df.columns}
    missing = sorted(required - present)
    return not missing, missing


def rename_and_clean(df: pd.DataFrame) -> pd.DataFrame:
    new_cols = {}
    drop_cols = []
    for col in df.columns:
        norm = _normalize_col(col)
        if norm in DROP_COLS:
            drop_cols.append(col)
        elif norm in RENAME_MAP:
            new_cols[col] = RENAME_MAP[norm]

    df = df.drop(columns=drop_cols, errors="ignore").rename(columns=new_cols)

    if "NoPes" in df.columns:
        df["NoPes"] = (
            df["NoPes"]
            .astype("string")
            .str.strip()
            .str.split("@", n=1)
            .str[0]
            .str.strip()
        )
    return df


def _safe_extract(zf: zipfile.ZipFile, destination: Path) -> None:
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_FILES:
        raise ValueError(f"ZIP berisi terlalu banyak file ({len(infos)} > {MAX_ZIP_FILES}).")

    total_size = sum(info.file_size for info in infos)
    if total_size > MAX_UNCOMPRESSED_BYTES:
        raise ValueError("Ukuran hasil ekstraksi ZIP melebihi batas 250 MB.")

    dest = destination.resolve()
    for info in infos:
        target = (destination / info.filename).resolve()
        if target != dest and dest not in target.parents:
            raise ValueError(f"Path tidak aman di dalam ZIP: {info.filename}")
    zf.extractall(destination)


def _dedupe_final(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    before_count = len(df)
    id_norm = df["NoPes"].astype("string").str.strip().str.casefold()
    valid_id = id_norm.notna() & id_norm.ne("") & id_norm.ne("nan")
    invalid_id_count = int((~valid_id).sum())

    work = df.copy()
    work["_id_norm"] = id_norm
    work["_row_order"] = range(len(work))
    work["_nilai_num"] = pd.to_numeric(
        work["Nilai"].astype("string").str.replace(",", ".", regex=False),
        errors="coerce",
    )

    duplicated_ids = (
        work.loc[valid_id, "_id_norm"]
        .loc[lambda s: s.duplicated(keep=False)]
        .dropna()
        .unique()
        .tolist()
    )
    invalid_nilai_count = int(work["_nilai_num"].isna().sum())

    valid_rows = (
        work.loc[valid_id]
        .sort_values(
            ["_id_norm", "_nilai_num", "_row_order"],
            ascending=[True, False, True],
            na_position="last",
            kind="stable",
        )
        .drop_duplicates(subset=["_id_norm"], keep="first")
    )
    invalid_rows = work.loc[~valid_id]
    final = pd.concat([valid_rows, invalid_rows], ignore_index=True)
    final = final.drop(columns=["_id_norm", "_nilai_num", "_row_order"], errors="ignore")

    sort_cols = [c for c in ["Kelas", "Nama"] if c in final.columns]
    if sort_cols:
        final = final.sort_values(by=sort_cols, kind="stable").reset_index(drop=True)

    return final, {
        "sebelum": before_count,
        "id_duplikat": len(duplicated_ids),
        "dihapus": before_count - len(final),
        "nopes_kosong": invalid_id_count,
        "nilai_tidak_numerik": invalid_nilai_count,
        "final": len(final),
    }


@dataclass
class AnalysisResult:
    # DataFrames survive extraction-directory cleanup and Streamlit reruns.
    groups: dict[str, dict]
    candidates: list[SimilarCandidate]
    errors: list[str]
    warnings: list[str]
    total_input_zips: int
    total_csv: int


MergeDecisions = Mapping[tuple[str, str], bool]


def confirmed_groups(analysis: AnalysisResult,
                     merge_decisions: MergeDecisions | None = None) -> dict[str, dict]:
    """Only explicit True decisions merge groups. False is a separation constraint."""
    decisions = merge_decisions or {}
    parents = {key: key for key in analysis.groups}

    def root(key):
        while parents[key] != key:
            key = parents[key]
        return key

    for pair, merge in decisions.items():
        if (not isinstance(pair, tuple) or len(pair) != 2
                or any(key not in parents for key in pair)
                or pair[0] == pair[1] or type(merge) is not bool):
            raise ValueError(f"Keputusan review tidak valid: {pair!r}.")
        if merge:
            left, right = sorted([root(pair[0]), root(pair[1])])
            parents[right] = left
    for (left, right), merge in decisions.items():
        if not merge and root(left) == root(right):
            raise ValueError(f"{left} dan {right} dipilih tetap terpisah tetapi terhubung "
                             "oleh keputusan gabung lain. Ubah keputusan review.")
    grouped = {}
    for key, info in sorted(analysis.groups.items()):
        target = root(key)
        grouped.setdefault(target, {"output_name": analysis.groups[target]["output_name"],
                                    "entries": [], "members": []})
        grouped[target]["entries"].extend(info["entries"])
        grouped[target]["members"].append(key)
    return grouped


def _delimiter_label(df: pd.DataFrame) -> str:
    delimiter = df.attrs.get("delimiter")
    return {",": "koma (,)", "\t": "TAB", ";": "titik koma (;)"}.get(
        delimiter, repr(delimiter) if delimiter else "tidak tersedia")


def _source_label(entry) -> str:
    source_zip, rel_path, df = entry
    return f"{source_zip} :: {rel_path} (delimiter={_delimiter_label(df)})"


def _schema_diagnostics(group: str, entries, log: LogFn) -> list[str]:
    errors = []
    for entry in entries:
        df = entry[2]
        ok, missing = _required_source_columns_ok(df)
        if not ok:
            errors.append(f"{group}: {_source_label(entry)}; kolom wajib kurang: "
                          f"{', '.join(missing)}; mapel di-skip.")
        normalized = [_normalize_col(c) for c in df.columns]
        duplicates = sorted({c for c in normalized if normalized.count(c) > 1})
        if duplicates:
            errors.append(f"{group}: {_source_label(entry)}; kolom ambigu setelah "
                          f"normalisasi: {', '.join(duplicates)}; mapel di-skip.")
    reference = entries[0]
    ref_schema = _effective_schema(reference[2])
    ref_all = {_normalize_col(c) for c in reference[2].columns}
    for entry in entries[1:]:
        schema = _effective_schema(entry[2])
        columns = {_normalize_col(c) for c in entry[2].columns}
        only_a = sorted(ref_schema - schema)
        only_b = sorted(schema - ref_schema)
        metadata_a = sorted((ref_all - columns) & DROP_COLS)
        metadata_b = sorted((columns - ref_all) & DROP_COLS)
        labels = f"A={_source_label(reference)}; B={_source_label(entry)}"
        if only_a or only_b:
            errors.append(
                f"{group}: schema kolom relevan identitas/nilai/soal berbeda; {labels}; "
                f"hanya A: {only_a}; hanya B: {only_b}; "
                f"kolom wajib kurang A: {_required_source_columns_ok(reference[2])[1]}; "
                f"kolom wajib kurang B: {_required_source_columns_ok(entry[2])[1]}; "
                f"metadata diabaikan hanya A: {metadata_a}, hanya B: {metadata_b}; mapel di-skip.")
        elif metadata_a or metadata_b:
            log(f"{group}: perbedaan hanya metadata Moodle yang diabaikan; {labels}; "
                f"hanya A: {metadata_a}; hanya B: {metadata_b}; kolom relevan kompatibel.")
    return errors


def analyze_zip_uploads(
    uploads: Iterable[tuple[str, bytes]],
    log: LogFn | None = None,
) -> AnalysisResult:
    log = log or (lambda _msg: None)
    uploads = list(uploads)
    if not uploads:
        raise ValueError("Belum ada ZIP yang dipilih.")

    errors: list[str] = []
    warnings: list[str] = []

    with tempfile.TemporaryDirectory(prefix="nilai_merger_") as tmp:
        root = Path(tmp)
        extract_root = root / "extracted"
        extract_root.mkdir()

        csv_files: list[tuple[Path, str, str]] = []
        log(f"Memulai proses {len(uploads)} ZIP.")

        for idx, (zip_name, zip_bytes) in enumerate(uploads, 1):
            if not zip_name.lower().endswith(".zip"):
                warnings.append(f"{zip_name}: bukan file ZIP, dilewati.")
                log(f"⚠ {zip_name}: bukan ZIP, dilewati.")
                continue

            destination = extract_root / f"zip_{idx:03d}"
            destination.mkdir()
            try:
                with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
                    _safe_extract(zf, destination)
                found = 0
                for path in destination.rglob("*"):
                    if path.is_file() and path.suffix.lower() == ".csv":
                        rel = str(path.relative_to(destination))
                        csv_files.append((path, rel, zip_name))
                        found += 1
                log(f"✓ {zip_name}: {found} CSV ditemukan.")
                if not found:
                    msg = f"{zip_name}: tidak ada CSV di dalam ZIP."
                    warnings.append(msg)
                    log(f"⚠ {msg}")
            except Exception as exc:
                msg = f"{zip_name}: gagal dibaca/ekstrak ({exc})"
                errors.append(msg)
                log(f"❌ {msg}")

        if not csv_files:
            msg = "Tidak ada CSV valid yang ditemukan dari seluruh ZIP."
            warnings.append(msg)
            log(f"⚠ {msg}")

        log(f"Total CSV ditemukan: {len(csv_files)}.")

        grouped: dict[str, dict] = {}
        for full_path, rel_path, source_zip in sorted(
            csv_files, key=lambda x: (x[2].casefold(), x[1].casefold())
        ):
            stem = Path(rel_path).stem
            key = filename_key(stem)
            try:
                df = csv_to_df(full_path)
            except Exception as exc:
                msg = (f"{clean_filename(stem)}: {source_zip} :: {rel_path}: "
                       f"CSV gagal dibaca (delimiter tidak tersedia; {exc})")
                errors.append(msg)
                log(f"❌ {msg}")
                continue

            delimiter = _delimiter_label(df)
            log(f"✓ {source_zip} :: {rel_path}: delimiter={delimiter}; {len(df)} baris dibaca.")
            grouped.setdefault(
                key,
                {"output_name": clean_filename(stem), "entries": []},
            )["entries"].append((source_zip, rel_path, df))

        log(f"Ditemukan {len(grouped)} kelompok mapel setelah normalisasi nama.")

        candidates = similar_group_candidates(grouped)
        log(f"Review: {len(candidates)} pasangan nama mirip; belum ada penggabungan manual.")
        return AnalysisResult(grouped, candidates, errors, warnings, len(uploads), len(csv_files))


def process_analysis(
    analysis: AnalysisResult,
    merge_decisions: MergeDecisions | None = None,
    log: LogFn | None = None,
) -> ProcessResult:
    log = log or (lambda _msg: None)
    grouped = confirmed_groups(analysis, merge_decisions)
    errors = list(analysis.errors)
    warnings = list(analysis.warnings)
    audit_rows: list[dict] = []
    log(f"Memproses {len(grouped)} kelompok mapel setelah keputusan review.")
    for info in grouped.values():
        if len(info["members"]) > 1:
            log(f"Gabung manual dikonfirmasi: {', '.join(info['members'])} "
                f"-> {info['output_name']}.")
    for (left, right), merge in (merge_decisions or {}).items():
        if not merge:
            log(f"Tetap terpisah dikonfirmasi: {left} / {right}.")
    with tempfile.TemporaryDirectory(prefix="nilai_merger_") as tmp:
        root = Path(tmp)
        step1_dir = root / "step1"
        final_dir = root / "final"
        step1_dir.mkdir()
        final_dir.mkdir()
        # Tahap 1: schema check, concat, lalu Excel internal seperti notebook.
        step1_files: list[Path] = []
        output_files: list[Path] = []
        used_names: set[str] = set()
        for key in sorted(grouped):
            info = grouped[key]
            base_name = safe_output_name(info["output_name"])
            output_name = base_name
            suffix = 2
            while output_name.casefold() in used_names:
                output_name = f"{base_name} ({suffix})"
                suffix += 1
            used_names.add(output_name.casefold())
            if output_name != base_name:
                msg = f"{info['output_name']}: nama output bentrok; digunakan {output_name}.xlsx."
                warnings.append(msg)
                log(f"⚠ {msg}")
            entries = info["entries"]
            dfs = [entry[2] for entry in entries]
            if not dfs:
                continue

            diagnostics = _schema_diagnostics(output_name, entries, log)
            if diagnostics:
                errors.extend(diagnostics)
                for message in diagnostics:
                    log(f"❌ {message}")
                continue

            # Schema comparisons use normalized headers. Align to reference
            # spellings before concat so accepted variants cannot split columns.
            # Keep metadata through the original Excel round trip; it is still
            # removed only by rename_and_clean, preserving original row sorting.
            canonical = {}
            for df in dfs:
                for column in df.columns:
                    canonical.setdefault(_normalize_col(column), column)
            aligned = []
            for df in dfs:
                aligned.append(df.rename(columns={c: canonical[_normalize_col(c)]
                                                  for c in df.columns}))
            out = step1_dir / f"{output_name}.xlsx"
            try:
                merged = pd.concat(aligned, ignore_index=True)
                if not merged.empty:
                    merged = merged.sort_values(by=merged.columns[0]).reset_index(drop=True)
                df_to_excel(merged, out)
                source_count = len(set(src for src, _, _ in entries))
                marker = "🔀" if len(entries) > 1 else "✓"
                log(
                    f"{marker} {output_name}: {len(entries)} file dari "
                    f"{source_count} ZIP → {len(merged)} baris."
                )
                if len(entries) > 1 and source_count == 1 and len(info["members"]) == 1:
                    msg = f"{output_name}: lebih dari satu file bernama sama ditemukan dalam ZIP yang sama."
                    warnings.append(msg)
                    log(f"⚠ {msg}")
                step1_files.append(out)
            except Exception as exc:
                msg = f"{output_name}: gagal membuat Excel internal ({exc})"
                errors.append(msg)
                log(f"❌ {msg}")

        if not step1_files:
            raise ValueError("Tidak ada mapel yang berhasil melewati tahap awal. "
                             + " | ".join(errors + warnings))

        # Tahap final.
        for xlsx_path in sorted(step1_files):
            mapel = xlsx_path.stem
            try:
                df = pd.read_excel(xlsx_path, engine="openpyxl")
                df = rename_and_clean(df)
                missing = REQUIRED_FINAL_COLS - set(df.columns)
                if missing:
                    msg = f"{mapel}: kolom wajib tidak ditemukan {sorted(missing)}"
                    errors.append(msg)
                    log(f"❌ {msg}")
                    continue

                final_df, audit = _dedupe_final(df)
                # Already sanitized and made unique in the internal stage.
                # Renormalizing would erase '(2)' after a '-grades' suffix.
                out = final_dir / f"{mapel}.xlsx"
                df_to_excel(final_df, out)
                output_files.append(out)
                audit_rows.append({"mapel": mapel, **audit})

                log(
                    f"✓ {mapel}: final {audit['final']} siswa; "
                    f"duplikat NoPes={audit['id_duplikat']}; "
                    f"NoPes kosong={audit['nopes_kosong']}."
                )
            except Exception as exc:
                msg = f"{mapel}: gagal pada tahap final ({exc})"
                errors.append(msg)
                log(f"❌ {msg}")

        output_files.sort()
        if not output_files:
            raise ValueError("Tidak ada Excel final yang berhasil dibuat. "
                             + " | ".join(errors + warnings))

        out_buffer = io.BytesIO()
        with zipfile.ZipFile(out_buffer, "w", zipfile.ZIP_DEFLATED) as zout:
            for path in output_files:
                zout.write(path, path.name)

        audit_df = pd.DataFrame(audit_rows)
        log(f"Selesai. {len(output_files)} file Excel siap diunduh.")
        return ProcessResult(
            zip_bytes=out_buffer.getvalue(),
            output_filename="nilai_US_final.zip",
            audit=audit_df,
            errors=errors,
            warnings=warnings,
            total_input_zips=analysis.total_input_zips,
            total_csv=analysis.total_csv,
            total_mapel=len(output_files),
        )


def process_zip_uploads(
    uploads: Iterable[tuple[str, bytes]],
    log: LogFn | None = None,
) -> ProcessResult:
    """Backward-compatible exact-only processing; suggestions never merge."""
    return process_analysis(analyze_zip_uploads(uploads, log=log), log=log)
