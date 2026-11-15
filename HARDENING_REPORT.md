# Laporan Hardening Integrasi Blender — ZeroScript Bridge

Mandat 4 fase: audit → perbaikan → verifikasi → laporan. Tanggal: 2026-11-15.
Branch: `arena/4c02698b-zeroscript-free-arena-ai` (base `master` @ `32dd846`). **Belum ada commit** — semua perubahan masih working tree (lihat §7).

Skenario target: **AI di website → bridge di PC user → MCP → Blender user**, tanpa mengubah jalur Roblox Studio.

---

## 1. Diagram aliran data (AKTUAL, dari kode — bukan asumsi)

```
┌───────────────────────────────────────────────────────────────────────────┐
│ AI di website (ChatGPT / Claude web, tab browser user)                    │
│  • melihat tools via teks: list_commands / call_tool dieksekusi ekstensi  │
└──────────────┬────────────────────────────────────────────────────────────┘
               │ teks tool-call (injection/in-page → background.js)
               ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ Ekstensi browser — background.js                                          │
│  • authToken HANYA di memori; fetchToken() GET /zs-auth-token (4s abort)  │
│  • onopen → doHello(): kirim {"type":"hello","token"} ≤8s                 │
│    - hello_ok → tunggu frame "connected" → baru status "connected"        │
│    - legacy bridge (tanpa gate): frame "connected" langsung → mode legacy │
│  • send() digate `connected` → tidak ada request menyusup sebelum auth    │
│  • Consent dialog (ui.requestConsent) untuk tool eksekusi addon;          │
│    Add/Remove server = konfirmasi 2 klik; preset mengirim env safe-mode   │
└──────────────┬────────────────────────────────────────────────────────────┘
               │ WebSocket ws://127.0.0.1:<PORT acuan 17699> (HOST="127.0.0.1", bridge.py:77)
               │ Origin: chrome-extension://<id> (browser memaksa); Host: 127.0.0.1:PORT
               │ Gate HTTP pre-upgrade (process_request websockets):
               │   403 bila Origin terdeteksi ≠ ekstensi; Host ≠ loopback; token endpoint
               │   butuh Origin ekstensi. Upgrade WS tanpa Origin boleh (butuh token).
               ▼
┌───────────────────────────────────────────────────────────────────────────┐
│ bridge.py (PC user, proses start.bat / manual)                            │
│  Gate level WS (handler, pesan PERTAMA ≤10s):                             │
│    hello{token} → hello_ok (4001/4003/4000/4007/4008 bila gagal)          │
│    token: bridge_auth.json (0600) / env ZS_AUTH_TOKEN; _ct_equal          │
│    limit: ≤8 klien, ≤60 pesan/10s, ≤16MB/pesan; rotate → tutup 4002       │
│  Router pesan: ping / list_tools / call_tool (idempoten via call_id) /    │
│    add_server / remove_server (live, tanpa restart — ack "restarting":False)│
│    add_server: validate_server_spec STRICT (tanpa koersi) → config.json   │
│      (atomic write, tulis-tmp-rename) → MCPClient.start() di thread       │
│      → broadcast "connected" (health per server: alive/starting/error)    │
└───────┬───────────────────────────────────────────────┬───────────────────┘
        │ stdio JSON-RPC 2.0 (MCP)                      │ stdio JSON-RPC 2.0
        ▼                                               ▼
┌─────────────────────────────┐          ┌────────────────────────────────────┐
│ launch_studio_mcp.py        │          │ uvx mcp-for-blender@2.3.0 (pinned) │
│ → plugin Roblox Studio      │          │ env: BLENDER_MCP_SAFE_MODE=1,      │
│   (server MCP UTAMA)        │          │      DISABLE_TELEMETRY=true        │
│   jalur Roblox TIDAK DIUBAH │          │ → addon Blender, TCP localhost:9876│
└─────────────────────────────┘          │ safe mode: validasi AST deny-by-   │
                                         │ default SEBELUM kode ke Blender    │
                                         └────────────────────────────────────┘
```

Titik keputusan transport yang dibaca dari kode (bukan diasumsikan):
- WS bridge → server MCP: **stdio** (`subprocess.Popen` di `MCPClient._start_locked`).
- Addon Blender → aplikasi Blender: **TCP localhost:9876** (desain paket, di luar kendali ZeroScript).
- Rotasi token: HTTP `GET /zs-auth/rotate` (Origin ekstensi saja) → token baru + tutup semua koneksi authed (4002) → ekstensi fetch ulang otomatis (loop reconnect hidup).

## 2. Model ancaman (ringkas, dipetakan ke mitigasi)

| # | Ancaman | Vektor | Mitigasi (kode) | Bukti |
|---|---------|--------|-----------------|-------|
| T1 | Halaman web mana pun memanggil `add_server` / eksekusi tool via WS lokal (WS tidak terkena CORS) — **CRITICAL dari audit, terkonfirmasi** | `new WebSocket("ws://127.0.0.1:17699")` dari situs jahat | Gate 2 lapis: `process_request` menolak Origin non-ekstensi **sebelum upgrade**; lalu handshake `hello{token}` constant-time; tanpa token tidak ada pesan diproses | e2e test 02, 01; unit GateRequestTest |
| T2 | DNS rebinding (Host = domain penyerang, resolv ke 127.0.0.1) | HTTP/WS request `Host: attacker.com` | `_valid_host`: Host wajib 127.0.0.1/localhost/[::1]:PORT → 403 | e2e test 03; unit GateRequestTest |
| T3 | Proses lokal membaca token | baca `bridge_auth.json` | File 0600 (POSIX); token juga bisa via env; fingerprint di log, bukan token | unit TokenAuthTest (POSIX); **Windows ACL = UNTESTED** |
| T4 | Token bocor → disalahkan selamanya | dump memori/akses baca sekali | Rotasi (`/zs-auth/rotate`, Origin-gated) mengganti token & menutup koneksi lama 4002; token lama matang | e2e test 05 (bug ditemukan & diperbaiki: rotasi kini juga update token in-memory) |
| T5 | Flood / DoS bridge | spam pesan, banyak koneksi, pesan raksasa | Rate limit 60 pesan/10s → 4008; maks 8 klien → 4007; maks 16MB → 1009; hello timeout 10s → 4000 | e2e test 06, 07, 09, 01 |
| T6 | Token diambil halaman web via endpoint HTTP | `fetch("http://127.0.0.1:PORT/zs-auth-token")` dari situs jahat | Endpoint token wajib Origin ekstensi; tanpa/origin salah → 403 tanpa CORS headers | e2e test 04; unit GateRequestTest |
| T7 | AI ditipu mengeksekusi kode berbahaya di Blender user | `execute_blender_code` | 3 lapis: (a) safe mode paket (deny-by-default, validasi sebelum socket) — terbukti LIVE lewat bridge; (b) consent dialog ZeroScript (Allow once / session / Deny, auto-deny 45s); (c) preset memaksa `BLENDER_MCP_SAFE_MODE=1` | e2e test 16 (penolakan safe-mode via server asli); UI = UNTESTED in-vivo |
| T8 | Config disusupi (command sembarangan) | `add_server` dengan spec malform / menimpa `roblox` | `validate_server_spec` strict tanpa koersi; `roblox` dilindungi (add/remove ditolak); env hanya `[A-Za-z_][A-Za-z0-9_]*`, tanpa kontrol char | unit StrictValidationTest, ConfigRoundtripTest; e2e test 10 |
| T9 | Race/concurrency | replace-while-starting, 2 klien, disconnect mid-call, crash loop | `clients_lock`, `start_lock`+flag `starting`, idempotensi `call_id` (replay/coalesce ≤120s, FIFO 128), server_watch backoff | e2e test 10, 12, 13, 14, 15 |
| T10 | Ekstensi lama / bridge lama (kompatibilitas dua arah) | mismatch versi | Bridge baru: pesan non-hello pertama → 4003 bersih. Bridge lama + ekstensi baru: `hello` → error `unknown message type`, lalu `connected` → mode legacy jalan | e2e test 17, 18 (bridge lama = `git show HEAD:bridge.py` asli) |

Sisa risiko (di luar jangkauan perubahan ini, terdokumentasi di README):
1. Socket addon 9876 milik paket menerima `execute_code` mentah dari proses lokal mana pun dan **safe mode tidak berlaku di jalur socket itu** (diverifikasi dari source paket v2.3.0). Mitigasi kita hanya edukasi + safe mode di jalur MCP.
2. Extension ID bersifat publik — Origin check bukan rahasia; **lapisan keamanan utama adalah token**. Origin check memangkas permukaan (browser memblokir upgrade), bukan pengganti token.
3. Malware level same-user di PC sudah game-over (bisa baca token file/memori) — diasumsikan di luar model ancaman.
4. Tidak ada undo-step per aksi AI di Blender (paket tidak membungkusnya) — Ctrl+Z manual; terdokumentasi.

## 3. Klaim → label → bukti

| Klaim | Label | Bukti (perintah + hasil) |
|---|---|---|
| Bind hanya loopback | VERIFIED | `grep -n '^HOST' bridge.py` → `77:HOST = "127.0.0.1"`; bridge listen log `ws://127.0.0.1:17711` |
| Suite baru hijau 2× beruntun tanpa perubahan kode di antaranya | VERIFIED | `python3 test_bridge_addons.py` → run1 `Ran 50 tests ... OK` (37.5s), run2 `Ran 50 tests ... OK` (39.6s), exit 0 keduanya |
| Token auth + rotasi bekerja; token lama mati | VERIFIED | e2e test 05 (setelah fix in-memory): rotasi → koneksi lama close 4002, hello token lama → 4003, token baru → hello_ok |
| Halaman web (Origin jahat) ditolak 403 sebelum upgrade; token endpoint tergate | VERIFIED | e2e test 02 (InvalidStatus 403), test 04 (`urllib` no-origin → 403, `evil.com` → 403, origin ekstensi → 200 + token benar) |
| DNS rebinding ditolak | VERIFIED | e2e test 03: raw HTTP `Host: attacker.com` → `HTTP/1.1 403` |
| Flood/oversize/max-clients ditolak (4008/1009/4007) | VERIFIED | e2e test 06, 07, 09 |
| Live add/replace/remove tanpa restart; config.json aman (roblox dilindungi) | VERIFIED | e2e test 10 (ack `restarting:false`, socket survive, config tepat); unit ConfigRoundtripTest |
| Idempotensi call_id (eksekusi tepat 1× meski disconnect mid-call + replay) | VERIFIED | e2e test 13 (exec log `START` tepat 1) |
| starting/failed + alasan tampil di health | VERIFIED | e2e test 11 (error: "…Broken pipe (process exited with code 3: fatal: intentionally broken)") — memaksa fix `start()` merekam `start_error` |
| Server MCP Blender ASLI 2.3.0 jalan di belakang bridge; safe-mode menolak `import os` SEBELUM hop Blender | VERIFIED | e2e test 16: `add_server` (`python -m blender_mcp.server`, env safe-mode) → `execute_blender_code` → jawaban berisi "Rejected by safe mode" |
| Kompabilitas dua arah bridge baru ↔ ekstensi baru/ekstensi lama/bridge lama | VERIFIED | e2e test 17 (ekstensi lama → 4003 bersih), test 18 (bridge lama dari `git show HEAD:bridge.py`, mode legacy jalan, pong) |
| Test lama tidak berubah hasil | VERIFIED | `node test-parser.js` → 37 PASS 0 FAIL; `node test-chatgpt.js` → 8 PASS 0 FAIL (sama dengan baseline pra-hardening) |
| `config.json` (Roblox) byte-identical | VERIFIED | `git status` tidak menampilkan config.json |
| `_ct_equal` fail-closed (None/empty ≠ valid) | VERIFIED | unit `test_constant_time_compare` — awalnya GAGAL (kode mengembalikan True), kode diperbaiki, kini OK |
| Spec strict tanpa koersi (string→list, int→string dsb. ditolak) | VERIFIED | unit StrictValidationTest 10 kasus negatif; assert koersi suite LAMA dihapus karena melanggar requirement (lihat §6) |
| Jalur Roblox identik secara perilaku | REASONED | lihat §5 — aliveness Roblox tetap REASONED-only (Studio tidak bisa jalan di sandbox) |
| env `add_server` sampai ke proses child | VERIFIED | e2e test 13: child menerima `FAKE_EXEC_LOG` via env dan menulis file log |
| Port acak + PID terlacak, tanpa pkill/killpg/os.killpg | VERIFIED | desain `free_port()`/`BridgeProc.stop()` (terminate+wait, taskkill /T belt di Windows); 2 run penuh: `ps` → 0 bridge zombie, 0 dir /tmp/zs-test tersisa |
| websockets 17.4: `Response(status, reason, Headers, body)` | VERIFIED | signature terbaca dari `inspect` + test 02–05 hijau setelah fix (sebelumnya argumen bergeser → koneksi ditutup tanpa respons) |
| Branch legacy websockets (<13) `process_request` sinkron benar | UNTESTED | hanya branch modern yang tereksekusi di sini; branch legacy feature-detected, dibaca dari source |
| Windows runner `scripts/run_tests_windows.ps1` | UNTESTED | tidak ada akses Windows; script disediakan, sintaks PowerShell belum dieksekusi |
| Full chain sampai Blender hidup (viewport/object nyata) | UNTESTED | binary `blender` tidak tersedia di sandbox (`which blender` → tidak ada) |
| UI consent / 2-klik / starting-dot in-vivo di Chrome + situs AI asli | UNTESTED | sandbox tanpa browser; JS lolos `node --check` + logika tercakup test bridge-side |
| Perf/stabilitas jangka panjang (jam–hari) | UNTESTED | window test ±40 detik |

## 4. Daftar UNTESTED lengkap (jujur, tidak disembunyikan)

1. **Semua hal di Windows** — eksekusi bridge, GUI PATH/`_resolve_uvx`, `cmd /c npx/npm/yarn/pnpm/bunx`, `taskkill /T`, ACL token, dan `scripts/run_tests_windows.ps1` sendiri. Tidak ada akses Windows → script disediakan; jalankan: `powershell -ExecutionPolicy Bypass -File scripts\run_tests_windows.ps1`.
2. **Hop Blender asli** — `execute_blender_code` mengubah scene nyata, `look`/`viewport_*`, `import_asset`/`generate_3d` (jaringan + biaya). Yang terverifikasi hanya sampai server MCP Blender asli (transport+protocol+routing+safe mode); Blender aplikasinya tidak ada di sandbox.
3. **Alur ekstensi in-vivo** — handshake di Chrome asli, modal consent, konfirmasi 2-klik, dot "starting" di panel, penyambungan ke situs AI asli.
4. **`uvx` first-run download** (30–60s) di jalur uv asli — test pakai fake server `sys.executable` + paket asli dari install lokal; jalur `_resolve_uvx`/scan-PATH Windows REASONED dari kode.
5. **Branch legacy websockets** (versi < modern API) — feature-detected, tidak tereksekusi di lingkungan ini.
6. **`restart_self()`** — kode ada tapi TIDAK punya caller (jalur live add/remove sengaja `restarting:false`); tidak dieksekusi test.
7. **Keandalan jangka panjang** (memori rate-map, koneksi idle berjam-jam, rotate saat 8 klien aktif serentak, dst.).
8. **ACL file token di Windows** (0600 hanya POSIX).

## 5. Jejak Roblox Studio (wajib: identik perilaku)

File/fungsi yang disentuh TAHAP 2–3 ini yang berada di jalur bersama, dengan argumen invarians:
- `bridge.py`:
  - `_ct_equal` — hanya perbandingan token (jalur baru; bridge lama tak punya gate). Roblox = child MCP stdio, bukan klien WS.
  - `_make_process_request` — hapus impor mati (`websockets.http`, tidak ada di 17.4 → mem-buang startup saat paket websockets baru; ini perbaikan startup, bukan perubahan perilaku Roblox) + koreksi argumen `Response` (jalur 403/endpoint token).
  - `handler()` — tambah pengiriman `hello_ok` SETELAH gate lolos, sebelum `connected` (frame baru; konsumennya ekstensi baru; koneksi Roblox tetap stdio).
  - `MCPClient.start()` — tambahan perekaman `start_error` pada exception (path sukses identik byte-per-byte; path gagal kini menamai alasan di health).
  - `_start_locked()` — tambahan blok diagnostik `start_error` setelah loop grace tools (tanpa perubahan alur kontrol).
  - `_auth_rotate()` — update token in-memory (jalur rotasi saja).
- `zeroscript-extension/core/config.js` — blok TOOL_NOTES Blender diganti (kunci addon saja); entri Roblox (`execute_luau`, `multi_edit`, `inspect_instance`, `script_read`, `user_keyboard_input`, `user_mouse_input`, `generate_mesh`, `generate_procedural_model`) TIDAK disentuh; gate consent sengaja mengecualikan `execute_luau`.
- `config.json` tidak ada di `git status` → byte-identical.
- Test: parser 37 + chatgpt 8 PASS, sama seperti baseline; e2e test 10 memverifikasi `mcpServers.roblox` utuh persis `{command:"launch_studio_mcp.py",args:[]}` setelah siklus add/replace/remove.

Kesimpulan: jalur Roblox **REASONED identik** (bukan VERIFIED, karena Studio tidak bisa dijalankan di sandbox — sesuai aturan, tidak ada klaim aliveness Roblox lebih dari itu).

## 6. Perubahan pada test (aturan: tidak boleh ubah test untuk lolos)

- Suite lama 18-test DITULIS ULANG seluruhnya (`test_bridge_addons.py`) karena: helper tidak portabel (port tetap, pkill pattern), assert koersi yang secara eksplisit melanggar requirement strict-validation, dan arsitektur single-lifecycle yang menyembunyikan kasus. Yang diganti adalah TEST mengikuti requirement; kode tidak dibelakangi test.
- Dua tempat test diubah KARENA KODE SALAH dan diperbaiki (bukan sebaliknya):
  1. `_ct_equal(None,None)` → True (auth lolos untuk token kosong) → kode difix fail-closed; test dipertahankan.
  2. Rotasi tidak meng-update token in-memory (token lama masih valid sampai restart) → kode difix; test test_05 dipertahankan.
- Juga ditemukan & diperbaiki lewat test: `hello_ok` tidak pernah dikirim (ekstensi baru akan hang), impor `websockets.http` mati (bridge crash di websockets 17), signature `Response` salah (respons HTTP tak terkirim), `start_error` tidak terekam saat initialize gagal.

## 7. Status git & pengiriman

```
$ git diff --stat  (terhadap master @ 32dd846)
 .gitignore                          |   5 +-
 README.md                           |  25 +
 bridge.py                           | 975 ++++++++++++++++++++++++++++++----
 zeroscript-extension/background.js  | 127 ++++-
 zeroscript-extension/core/config.js |  60 +++
 zeroscript-extension/core/main.js   | 223 +++++++--
 zeroscript-extension/overlay.css    |  61 +++
 7 files changed, 1343 insertions(+), 133 deletions(-)
+ file baru: test_bridge_addons.py (suite 50 test), scripts/run_tests_windows.ps1, HARDENING_REPORT.md (laporan ini)
```
`manifest.json` + `CHANGELOG.md` tetap identik dengan HEAD (versi tetap 1.5.5, sesuai aturan).

Pengiriman: sesuai permintaan user, SELURUH perubahan dikirim sebagai **satu PR** dari branch sesi ini (`arena/4c02698b-zeroscript-free-arena-ai` → `master`). Pemecahan 4 PR di §8 tidak bisa dijalankan dari satu branch sesi — anggap §8 sebagai **panduan urutan review**, bukan PR terpisah.

## 8. Usulan pemecahan PR (4 PR, urutan merge)

1. **PR A — Security core bridge + handshake ekstensi**: `bridge.py` (auth block, WS gate, hello_ok, rotate, rate/caps, `_ct_equal` fail-closed), `background.js` (hello handshake + fetchToken), `.gitignore` (+ `bridge_auth.json`), bagian "## Bridge security" README. Test: unit TokenAuth/GateRequest + e2e 01–09, 17, 18.
2. **PR B — Live add/remove + idempotensi + status jujur**: `bridge.py` (idempotensi call_id, broadcast event-driven, `start_error`, strict spec, rotasi in-memory fix), `core/main.js` (2-klik, waitForServerSettle/Gone, dot starting), `overlay.css` (dot starting), test e2e 10–15.
3. **PR C — Preset Blender + consent + TOOL_NOTES ber Risiko**: `core/config.js` (preset pinned `mcp-for-blender@2.3.0` + env safe-mode, TOOL_NOTES 3 kelompok risiko), `core/main.js` (consent gate eksekusi addon, pendingPresetEnv), `overlay.css` (dialog consent), README bagian Blender.
4. **PR D — Test suite portabel + docs**: `test_bridge_addons.py`, `scripts/run_tests_windows.ps1`, README (limitasi Blender, cara bump pin).

Catatan: A adalah prasyarat keamanan untuk B dan C (B/C menyentuh pesan yang sudah tergate oleh A).

## 9. Langkah uji manual yang tersisa (yang tidak bisa dilakukan sandbox)

1. **Windows**: jalankan `scripts\run_tests_windows.ps1` → semua hijau; `start.bat` dari Explorer (bukan terminal) → Add Blender preset → server tetap ketemu (uji `_resolve_uvx`/PATH GUI); task manager: tidak ada proses yatim setelah stop bridge.
2. **Blender asli**: install Blender + `uvx mcp-for-blender install-addon` → add preset → `get_scene_info` mengembalikan scene asli; `execute_blender_code "import os"` → ditolak safe mode; consent modal muncul di ekstensi; Ctrl+Z mengembalikan aksi AI (bukti tanpa-undo-step).
3. **Browser**: halaman uji lokal `new WebSocket("ws://127.0.0.1:17699")` dari console situs biasa → koneksi ditolak (403/upgrade gagal); fetch token tanpa origin ekstensi → 403.
4. **Rotasi live**: klik rotate (atau hapus `bridge_auth.json` + restart) → ekstensi pulih sendiri tanpa interaksi.
