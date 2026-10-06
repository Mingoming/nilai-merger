import hashlib

import streamlit as st

from src.processor import analyze_zip_uploads, confirmed_groups, process_analysis


def reset_work():
    for key in list(st.session_state):
        if key.startswith("nm_") and key not in {"nm_uploads", "nm_fingerprint"}:
            del st.session_state[key]
    st.session_state["nm_logs"] = []


def show_diagnostics(warnings, errors):
    if warnings:
        with st.expander(f"Warning ({len(warnings)})", expanded=True):
            for warning in warnings:
                st.warning(warning)
    if errors:
        with st.expander(f"Error / file dilewati ({len(errors)})", expanded=True):
            for error in errors:
                st.error(error)


st.set_page_config(page_title="Penggabung Nilai", page_icon="📊", layout="wide")
st.title("📊 Penggabung Nilai Ujian")
st.caption(
    "Upload satu atau beberapa ZIP, analisis nama mapel, lalu konfirmasi hasil review "
    "sebelum memproses nilai. Nama yang sama setelah normalisasi aman dikelompokkan otomatis."
)
with st.expander("Aturan penggabungan", expanded=False):
    st.write(
        "Perbedaan kapital/spasi serta suffix export seperti `grades(1)` atau `grades2` "
        "diabaikan. Nama yang hanya mirip selalu membutuhkan keputusan Anda. "
        "Pastikan mapel seperti BAHASA INGGRIS dan BAHASA INGGRIS LANJUT sesuai "
        "sebelum memilih Gabungkan."
    )

uploads = st.file_uploader(
    "Upload file ZIP", type=["zip"], accept_multiple_files=True, key="nm_uploads",
    help="Bisa upload 1 ZIP, 2 ZIP, ZIP susulan, dan seterusnya.",
)
payload = [(file.name, file.getvalue()) for file in uploads or []]
fingerprint = tuple((name, hashlib.sha256(data).hexdigest()) for name, data in payload)
if st.session_state.get("nm_fingerprint") != fingerprint:
    reset_work()
    st.session_state["nm_fingerprint"] = fingerprint

if uploads:
    st.write(f"**{len(uploads)} ZIP dipilih:**")
    for file in uploads:
        st.text(file.name)

analyze = st.button("Analisis file", key="nm_analyze", type="primary",
                    disabled=not uploads, width="stretch")
if analyze:
    reset_work()

st.subheader("Log proses")
log_area = st.empty()
log_area.code("\n".join(st.session_state.get("nm_logs", [])[-250:]) or "Menunggu analisis.",
              language=None)


def add_log(message):
    st.session_state["nm_logs"].append(message)
    log_area.code("\n".join(st.session_state["nm_logs"][-250:]), language=None)


if analyze:
    try:
        with st.spinner("Menganalisis ZIP dan CSV..."):
            st.session_state["nm_analysis"] = analyze_zip_uploads(payload, log=add_log)
    except Exception as exc:
        st.session_state["nm_error"] = f"Analisis dihentikan: {exc}"
        add_log(st.session_state["nm_error"])

analysis = st.session_state.get("nm_analysis")
if analysis is not None:
    st.subheader("Review nama mapel")
    st.write(f"{len(analysis.groups)} kelompok otomatis; {len(analysis.candidates)} pasangan perlu review.")
    with st.expander("Kelompok otomatis dan file sumber", expanded=not analysis.candidates):
        st.dataframe([
            {"Mapel": info["output_name"], "ZIP sumber": source_zip,
             "File sumber": rel_path, "Baris": len(df)}
            for info in analysis.groups.values() for source_zip, rel_path, df in info["entries"]
        ], width="stretch", hide_index=True)

    decisions = {}
    pending = 0
    for index, candidate in enumerate(analysis.candidates):
        left = analysis.groups[candidate.left]
        right = analysis.groups[candidate.right]
        st.text(f"{left['output_name']} ↔ {right['output_name']}")
        st.caption(f"Kemiripan {candidate.score:.0%}; saran saja, keputusan Anda menentukan penggabungan.")
        with st.expander(f"File sumber pasangan {index + 1}"):
            for info in [left, right]:
                for source_zip, rel_path, df in info["entries"]:
                    st.text(f"{info['output_name']} :: {source_zip} :: {rel_path} ({len(df)} baris)")
        choice = st.radio(
            f"Keputusan pasangan {index + 1}",
            ["Belum dipilih", "Tetap terpisah", "Gabungkan"],
            key=f"nm_review_{index}", horizontal=True,
        )
        if choice == "Belum dipilih":
            pending += 1
        else:
            decisions[(candidate.left, candidate.right)] = choice == "Gabungkan"

    signature = tuple(sorted(decisions.items()))
    if st.session_state.get("nm_decisions") != signature:
        st.session_state.pop("nm_result", None)
        st.session_state.pop("nm_error", None)
        st.session_state["nm_decisions"] = signature

    conflict = False
    try:
        groups = confirmed_groups(analysis, decisions)
        st.caption("Gabung A–B dan B–C menghasilkan satu kelompok A–B–C. "
                   "Pilihan Tetap terpisah harus sesuai dengan semua keputusan gabung.")
        st.dataframe([
            {"Output mapel": info["output_name"],
             "Kelompok asal": ", ".join(analysis.groups[k]["output_name"] for k in info["members"]),
             "File": len(info["entries"])}
            for info in groups.values()
        ], width="stretch", hide_index=True)
    except ValueError as exc:
        conflict = True
        st.error(str(exc))

    if pending:
        st.info(f"Pilih Gabungkan atau Tetap terpisah untuk {pending} pasangan sebelum memproses.")
    if not analysis.groups:
        st.error("Tidak ada CSV yang berhasil dibaca. Periksa warning/error dan upload ZIP yang valid.")

    if st.button("Proses hasil review", key="nm_process", type="primary",
                 disabled=bool(pending or conflict or not analysis.groups), width="stretch"):
        st.session_state.pop("nm_result", None)
        st.session_state.pop("nm_error", None)
        try:
            with st.spinner("Memproses kelompok yang dikonfirmasi..."):
                st.session_state["nm_result"] = process_analysis(analysis, decisions, log=add_log)
        except Exception as exc:
            st.session_state["nm_error"] = f"Proses dihentikan: {exc}"
            add_log(st.session_state["nm_error"])

    result = st.session_state.get("nm_result")
    if result is None:
        show_diagnostics(analysis.warnings, analysis.errors)
    else:
        st.success("Proses selesai.")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("ZIP input", result.total_input_zips)
        c2.metric("CSV ditemukan", result.total_csv)
        c3.metric("Excel final", result.total_mapel)
        c4.metric("Error", len(result.errors))
        show_diagnostics(result.warnings, result.errors)
        if not result.audit.empty:
            st.subheader("Audit hasil")
            st.dataframe(result.audit, width="stretch", hide_index=True)
        st.download_button(
            "⬇️ Download hasil ZIP", data=result.zip_bytes, file_name=result.output_filename,
            mime="application/zip", type="primary", width="stretch",
        )

if st.session_state.get("nm_error"):
    st.error(st.session_state["nm_error"])
