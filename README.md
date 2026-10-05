# Penggabung Nilai Ujian

Aplikasi Streamlit untuk menggabungkan hasil export nilai dari satu atau beberapa ZIP.

## Fitur

- Upload banyak ZIP sekaligus.
- Ekstraksi tiap ZIP secara terpisah agar file bernama sama tidak saling menimpa.
- Pengelompokan otomatis berdasarkan nama file yang dinormalisasi secara aman.
- Review manual pasangan nama mapel yang mirip: pilih **Gabungkan** atau **Tetap terpisah**.
- Analisis, keputusan review, log, dan hasil tersimpan selama interaksi dalam sesi Streamlit.
- Validasi schema CSV sebelum merge.
- `Email address` diubah menjadi `NoPes` tanpa domain (`12-05-061@example.test` → `12-05-061`).
- NoPes duplikat mempertahankan nilai tertinggi.
- Log proses, warning/error per mapel, audit hasil, dan download satu ZIP final.

## Jalankan lokal

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Buka alamat yang ditampilkan Streamlit (umumnya `http://localhost:8501`).

## Catatan matching nama

Versi MVP hanya auto-merge untuk nama yang menjadi sama setelah normalisasi aman, misalnya:

- `MATEMATIKA-grades.csv`
- `MATEMATIKA-grades(1).csv`
- `MATEMATIKA-grades2.csv`

Nama yang hanya *mirip*, misalnya `B. INGGRIS XII` dan `BAHASA INGGRIS XII`, ditampilkan
sebagai saran review. **Tidak pernah ada fuzzy-auto-merge**, termasuk untuk
`BAHASA INGGRIS XII` dan `BAHASA INGGRIS LANJUT XII`. Kata `LANJUT` tetap dipertahankan.

1. Upload satu atau beberapa ZIP dan klik **Analisis file**.
2. Periksa kelompok otomatis dan file sumber. Untuk setiap pasangan saran, pilih
   **Gabungkan** atau **Tetap terpisah**; tidak ada pilihan gabung bawaan.
3. Periksa pratinjau kelompok hasil review, lalu klik **Proses hasil review**.
4. Periksa log, audit, warning/error, dan download ZIP hasil.

Pilihan gabung berlaku transitif: A–B dan B–C menghasilkan A–B–C. Keputusan yang
bertentangan dengan **Tetap terpisah** ditolak sebelum proses. Mengubah keputusan
menghapus hasil lama; mengubah isi/nama/urutan upload menghapus analisis dan review lama.
Refresh penuh tab browser atau sesi terputus dapat menghapus session state.

Saran menggunakan `difflib.SequenceMatcher` dari standard library: minimal kemiripan
0,78, memiliki token mapel yang sama, dan token tingkat kelas yang sama.
Singkatan `B.` diperluas menjadi `bahasa` **hanya untuk saran**, tanpa mengubah
`filename_key()`. Maksimal lima pasangan per kelompok ditampilkan. Heuristik dapat
melewatkan pasangan atau memberi saran yang salah; keputusan tetap milik pengguna.

## Format CSV yang didukung

Aplikasi menerima export ber-ekstensi `.csv` yang menggunakan delimiter koma maupun TAB/TSV.
Kolom metadata Moodle seperti `Institution`, `Department`, `Started on`, `Completed`, dan `Time taken`
boleh ada di satu sumber dan tidak ada di sumber lain. Yang divalidasi untuk penggabungan adalah
kolom identitas/nilai serta struktur kolom soal yang relevan. Footer `Overall average` hanya dihapus
jika benar-benar ada, sehingga baris siswa terakhir tidak hilang.

Perbedaan schema dilaporkan bersama mapel, ZIP/path sumber A dan B, delimiter,
kolom wajib yang kurang, kolom relevan yang hanya ada di A/B, serta metadata yang
diabaikan. Perbedaan kapital/spasi header yang kompatibel disejajarkan sebelum concat.
Mapel yang schema-nya tidak valid dilewati; mapel lain tetap diproses. Jika tidak ada
hasil, rincian error tetap terlihat di log dan pesan UI. Nama output yang bentrok
mendapat suffix numerik; ZIP final hanya berisi Excel yang berhasil ditulis.

## Arsitektur dan pengujian lokal

`analyze_zip_uploads()` membaca CSV menjadi DataFrame dan membangun kelompok exact
serta kandidat. Direktori ekstraksi sementara dibersihkan setelah analisis.
`confirmed_groups()` memvalidasi keputusan eksplisit, lalu `process_analysis()`
memvalidasi schema, menjalankan tahap Excel internal/final, dedupe, audit, dan ZIP.
`process_zip_uploads()` tetap tersedia untuk pemanggil lama dan hanya memakai kelompok exact.
Pemilihan nilai tertinggi, nilai tampilan, dan perlakuan NoPes kosong tetap sama.

Tidak ada dependency tambahan. Dari root proyek:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q app.py src tests
.\.venv\Scripts\python.exe -m streamlit run app.py --server.headless true
```

Test mencakup CSV koma/TAB, footer opsional, normalisasi, kandidat, review eksplisit,
schema/metadata, dedupe, input rusak, output bentrok, dan Excel gagal ditulis.
Test regresi memakai data sintetis: 279 baris awal ditambah 42 baris dengan NoPes
yang sama menghasilkan 279 siswa final. Tidak ada data nilai siswa asli di repository.
Test UI memakai `streamlit.testing.v1.AppTest`; bagian upload test membutuhkan Streamlit
yang menyediakan `AppTest.file_uploader` (lingkungan pengembangan: 1.65.0).
