# Audit findings

Three independent read-only reviews of this plugin, run after using the protocol
for a real delegation. 39 findings. Kept as a record of what was checked and as
the open-work list.

**Roughly a third are fixed in `a21b1aa`.** Read this file as "what the audit
found", not as "what is currently broken".

**v2 (lane model) update:** the worktree-per-worker redesign dissolved or fixed
most of what remained. Dissolved by construction: the global lock and its stale
states (protocol #3, #8), BASELINE attribution and its ordering traps (#5, #6,
#12, #13, #14 - a lane starts clean, so `git status -uall` IS the footprint).
Fixed in v2: turn numbering in the instruction line (#1), ROUNDS.txt on-disk
round bookkeeping (#7), MCP registration as preflight (#10), folder trust via
`doctor.py --trust/--untrust` (#11), archive-before-delete closeout (#18),
copy-based falsification in the lane (#16), smoke via mkdtemp (#17), doctor:
version floor + config parse + honest login states (#5, #6, #7, #15 install),
CODEX_HOME honoured (#17 install), plugin MCP discovery (#10 install),
command-based recursion check + networking hints + honest labels (#4, #11
install), `--remove-mcp` + chmod 600 (#13 install), python version gate (#2
install), `commands/` slash entry (#9 install), manifest metadata (#19),
single install shape - plugin only (#20 install, #1 install), argparse help +
rc=max (#21). Still open, deliberately: RAW_OUTPUT.log rotation, transcript
truncation limits, Windows support, `--network` shorthand (the `-c` gate
shipped instead; the denylist is in dispatch.py).

## Addressed in a21b1aa

- All three protocol blockers: turn numbering, `FINAL.txt` on the error path,
  the §6/§8 single-delegation contradiction (plus the reviewer's own task dir).
- Both install blockers: `${CLAUDE_PLUGIN_ROOT}` in documented commands, and the
  Python 3.11 / `tomllib` requirement.
- The reviewer had no way to find `SPEC.md`; §8 now specifies its `PROMPT.txt`,
  and `review-protocol.md` requires a `CHECKED:` line so an evidence-free
  approval is visible.
- `dispatch.py`: stderr discarded, `--timeout` that could never fire, the
  pre-0.145 `requestUserInput` reply shape, missing-`codex` traceback, unguarded
  `send()`, no trailing newline in `FINAL.txt`. New `-c/--config` passthrough
  with a denylist.
- §3/§7: `mkdir -p`, and `-uall` on both `git status` calls - without it the
  footprint check cannot see inside an untracked directory.
- §5 no longer claims unnamed MCP servers stay off.
- Shell portability: every glob in the documented commands replaced, because zsh
  aborts the whole command when one matches nothing.

## Still open

Everything else below, chiefly: `doctor.py` swallowing config-parse errors and
reporting a missing login as OK, its hardcoded worker model and `MAIN_HOME`, MCP
discovery that misses plugin-provided servers, name-based recursion protection,
`--add-mcp` copying secrets in plaintext with no removal command, Windows
symlink and `/tmp` assumptions, `plugin.json` / `marketplace.json` metadata gaps,
`RAW_OUTPUT.log` growth with no rotation, transcript truncation limits, the §8
round counters living only in the architect's context, and the lock not being
released on `dispatch.py` error exits.

## Protokol / SKILL.md — 18 bulgu

_SKILL.md + references okundu, dispatch.py/doctor.py kod olarak dogrulandi, makinedeki gercek ~/.codex-worker/config.toml ile karsilastirildi. Protokol tek turlu mutlu yolda tutarli; ikinci turda (retry / L1 review) coken bir tasarim var: her dispatch soguk baslangic oldugu halde protokol turu sayan bir isci varsayiyor, ayni task-dir yeniden kullanildiginda FINAL.txt ve turn-N.md birbirini eziyor, dispatch.py hata yolunda FINAL.txt'yi hic yazmiyor (bir onceki turun basarili raporu diskte kaliyor). Ikinci kume: preflight'in vaadini tutmamasi -- --check ne codex surum tabanini, ne modelin kullanilabilirligini, ne MCP kaydini, ne de proje trust_level'ini dogruluyor; hepsi spec yazildiktan SONRA dispatch aninda patliyor, ki §3'un varlik sebebi tam olarak bunu onlemek. Ucuncu kume: sayilamayan sayaclar (L1 max 2, L3 max 2, spin detection) yalnizca Claude'un baglaminda yasiyor, oysa protokolun geri kalani "compaction'i atlatir" diye diske yasliyor. 18 bulgu, 3 blocker._

### 1. [BLOCKER] SKILL.md §4 (PROMPT.txt insasi) + §7 satir 169 + worker-contract.md "Changelog" bolumu + dispatch.py:237-242

**Sorun.** Her dispatch.py cagrisi `thread/start` ile SIFIRDAN bir thread aciyor -- resume yok, isci onceki turu hatirlamiyor. Ama worker-contract "turn-<N>.md yaz (N = bu turun numarasi)" diyor ve §7 "test -f turn-<N>.md" ile bunu ariyor. Hicbir yer isciye N'i soylemiyor: §4'teki tek satirlik talimatta da, §10 recovery talimatinda da tur numarasi yok.

**Neden onemli.** Kullanici §8 L1 reviewer 'request-changes' verdi, mimar retry dispatch ediyor. Taze isci hicbir gecmis gormeden yine turn-1.md yaziyor. Iki senaryodan biri olur: (a) mimar N=2 bekliyorsa `test -f turn-2.md` bos doner, dogru calismis bir tur UNTRUSTED ilan edilip atilir; (b) mimar FINAL.txt'nin LOG: satirina guvenirse kontrol gecer ama turn-1.md 1. turun changelog'unu USTUNE YAZMISTIR -- iscinin ne yaptigina dair tek kalici kayit yok olur, ve bu tam da §7'nin yakalamak icin var oldugu 'raporladigi dosyayi yazmamis isci' arizasinin sessiz versiyonudur.

**Onerilen duzeltme.**

§4'teki talimat satirini tur numarasi tasiyacak sekilde degistir:
  `Read .delegate-runs/<task-id>/SPEC.md and execute it. Task dir: .delegate-runs/<task-id>/ — this is turn <N>; write your changelog to .delegate-runs/<task-id>/turn-<N>.md`

§4'e not olarak ekle:
  "Every dispatch is a cold start: dispatch.py opens a new thread, so the worker has no memory of earlier turns and cannot know its own turn number. You own the counter. Before each retry, rewrite the last line of PROMPT.txt with the new N, and confirm turn-<N-1>.md is still on disk before dispatching."

§10 recovery talimatina da ayni sekilde `— this is turn <N>` ekle.

### 2. [BLOCKER] dispatch.py:252-259 + SKILL.md §6 son paragraf ("Read FINAL.txt")

**Sorun.** `final_path.write_text(...)` (satir 259) `with open(log_path)` blogunun DISINDA. `except DispatchError: ... return 1` (satir 252-255) o satira hic ulasmadan cikiyor. Yani timeout, `turn/failed`, app-server'in beklenmedik olumu veya protokol hatasi durumunda FINAL.txt HIC YAZILMIYOR. Ayni task-dir'de ikinci bir tur kosuluyorsa bir onceki turun FINAL.txt'si diskte oldugu gibi duruyor. SKILL.md ise kosulsuz "Read FINAL.txt" diyor ve hicbir yerde dispatch.py'nin cikis kodunu kontrol etmeyi soylemiyor.

**Neden onemli.** 1. tur basarili biter (FINAL.txt: STATUS: completed / ACCEPTANCE: pass). Reviewer request-changes verir, mimar retry dispatch eder, retry 3600s timeout'a girer veya turn/failed alir. Mimar arka plandan uyanir, §6'nin dedigi gibi FINAL.txt'yi okur, 1. TURUN 'completed/pass' raporunu gorup basarili sanir, §7'ye gecer, `test -f turn-1.md` de gecer (dosya 1. turdan kalma), acceptance komutunu kendisi kosar -- kod 1. turdaki haliyle durdugu icin muhtemelen yine gecer -- ve kullaniciya 'reviewer bulgulari duzeltildi' diye rapor eder. Hicbir sey duzeltilmemistir. Protokolun tum guvenmeme mimarisini tek noktadan delen sessiz bir yanlis-pozitif.

**Onerilen duzeltme.**

Kod tarafi (asil duzeltme): main() icinde prompt okunduktan hemen sonra stale dosyayi sil, hata yolunda da yaz:

    final_path = task_dir / "FINAL.txt"
    final_path.unlink(missing_ok=True)   # a stale report from an earlier round must never be re-read

ve except blogunda:

    final_path.write_text(f"DISPATCH FAILED: {exc}\n", encoding="utf-8")

Dokuman tarafi, §6'nin sonuna:
"`dispatch.py` exits non-zero when the turn never completed (timeout, protocol error, worker crash). Check its exit status BEFORE reading FINAL.txt. On non-zero the turn produced no report: read the last ~40 lines of RAW_OUTPUT.log, tell the user what happened, and go to §10 — do not treat any FINAL.txt on disk as this round's result."

### 3. [BLOCKER] SKILL.md §6 ("must not start a second delegation") vs §8 L1 ("Dispatch a second, read-only run")

**Sorun.** §6 IN_FLIGHT kilidi altinda "must not start a second delegation" diyor ve kilidin closeout'a (§9) kadar tutuldugunu vurguluyor. §8 L1 ise tam da o kilit altinda ikinci bir dispatch istiyor. Dahasi reviewer dispatch'inin `--task-dir`'i hicbir yerde belirtilmemis; §8 "Verdict comes back in FINAL.txt" diyor -- hangi FINAL.txt?

**Neden onemli.** Iki ayri arizadan biri olur. (a) Kurali harfi harfine uygulayan bir ajan §6'yi ust kural sayip L1'i tamamen atlar; protokolun 'bedava' review katmani hic calismaz ve kullanici bunu fark etmez, cunku atlandigina dair hicbir iz yok. (b) Ajan L1'i ayni `--task-dir .delegate-runs/<task-id>/` ile dispatch eder: dispatch.py FINAL.txt'yi reviewer'in <=5 satirlik verdict'iyle EZER, iscinin alti satirlik raporu yok olur, RAW_OUTPUT.log'a reviewer transcript'i karisir ve §7 (henuz kosulmadiysa) kosulamaz hale gelir.

**Onerilen duzeltme.**

§6'daki cumleyi kesinlestir:
"While IN_FLIGHT exists Claude must not write to the working tree, must not use the MCP servers granted to the worker, and must not start a second IMPLEMENTATION delegation. The §8 L1 review run is the single exception: it is read-only, gets no MCP grant, and MUST use its own task dir (`.delegate-runs/<task-id>-review/`) so it cannot overwrite the worker's FINAL.txt or RAW_OUTPUT.log."

§8 L1'in komutuna da acikca `--task-dir .delegate-runs/<task-id>-review/` yaz.

### 4. [MAJOR] SKILL.md §8 L1 + references/review-protocol.md satir 15

**Sorun.** §4, iscinin PROMPT.txt'sinin nasil kurulacagini kelime kelime tarif ediyor. Reviewer icin boyle bir tarif YOK. review-protocol.md ise "Read the SPEC.md you were pointed at" diyor -- ama onu bir yere isaret eden mekanizma hic tanimlanmamis. Reviewer'a task-id, SPEC.md yolu, BASE_SHA, BASELINE.txt'nin varligi hicbiri ulasmiyor.

**Neden onemli.** Ajan review-protocol.md'yi oldugu gibi PROMPT.txt yapip dispatch eder (§4'teki tek ornegi taklit ederek). Reviewer 'isaret edildigim SPEC.md' diye bir sey bulamaz; ya `.delegate-runs/` altinda kendi arar (bulursa sansi), ya da sozlesmesinin 1. onceligi olan 'Spec compliance'i atlayip sadece kodun ic tutarliligina bakip `VERDICT: approve` doner. Mimar bunu 'bagimsiz ikinci goz onayladi' diye kaydeder; oysa whitelist ihlali ve GOAL sapmasi hic kontrol edilmemistir. Sessiz yanlis-onay, protokolun en pahalisi.

**Onerilen duzeltme.**

§8 L1'i somutlastir:
"Build the reviewer's PROMPT.txt exactly as in §4: the full contents of `references/review-protocol.md`, followed by one line:
`Review the current working tree against .delegate-runs/<task-id>/SPEC.md — that file holds the GOAL, the FILE WHITELIST and BASE_SHA. The pre-existing dirty state is in .delegate-runs/<task-id>/BASELINE.txt. Do not modify anything.`
Dispatch with `--task-dir .delegate-runs/<task-id>-review/ --sandbox read-only` and no `--mcp`."

review-protocol.md satir 15'i de "Read the SPEC.md named in your instruction line" olarak degistir.

### 5. [MAJOR] SKILL.md §3 preflight blogu (satir 75-94) ve §4 dizin semasi (satir 98-106)

**Sorun.** Uc ayri sira/tanim bosluğu ayni blokta: (a) `git status --porcelain > .delegate-runs/<task-id>/BASELINE.txt` §3'te, ama o dizini yaratan adim §4'te -- komut oldugu gibi kosulursa 'No such file or directory' ile patlar; (b) `<task-id>` formati (YYYY-MM-DD-shortname) da §4'te tanimlaniyor, §3'te henuz elde yok; (c) §4'un dizin semasi BASELINE.txt'yi hic listelemiyor, oysa §7'nin footprint kontrolu tamamen ona dayaniyor.

**Neden onemli.** Protokolu ilk kez uygulayan ajan §3 blogunu sirasiyla kosar, redirection hata verir. Iki tipik toparlanma: BASELINE'i /tmp'ye yazar (sonra §7'de o yolu hatirlamaz, cunku hicbir sema onu listelemiyor) veya adimi 'sonra hallederim' deyip atlar. Sonuc ayni: §7'ye gelindiginde karsilastirilacak baseline yoktur, dirty bir agacta `git status --porcelain` ciktisinin tamami 'iscinin ayak izi' gibi gorunur; mimar ya kullanicinin kendi yarim islerini scope ihlali diye raporlar ya da footprint kontrolunu tumden birakir -- ki o kontrol §7'nin varlik sebebi.

**Onerilen duzeltme.**

§3 blogunu su hale getir (task-id'yi §3'e cek, mkdir ekle):

```bash
TASK_ID=$(date +%F)-<shortname>                 # e.g. 2026-07-26-inventory-ui
ls .delegate-runs/*/IN_FLIGHT 2>/dev/null       # must be empty - the only hard gate
python3 ".../doctor.py" --check                 # worker home + login + codex version
git rev-parse HEAD                              # record as BASE_SHA
ls -dt .delegate-runs/*/ 2>/dev/null | head     # >~3 days old = abandoned -> ask, then clean
mkdir -p ".delegate-runs/$TASK_ID"
git status --porcelain > ".delegate-runs/$TASK_ID/BASELINE.txt"
```

§4 semasina satir ekle:
`    BASELINE.txt              # pre-existing dirty state, written in §3 - §7 diffs against it`

### 6. [MAJOR] SKILL.md §7 footprint check + setup.md satir 64 ("Add .delegate-runs/ to .gitignore")

**Sorun.** `.delegate-runs/`'in HEDEF projede gitignore'lanmasi yalnizca setup.md'nin dibinde tek bir cumle. §3 preflight kontrol etmiyor, doctor.py --check kontrol etmiyor, README'de hic gecmiyor, spec-template'in FILE WHITELIST'i ise run dizininden sadece `turn-*.md`'yi listeliyor.

**Neden onemli.** Yeni kullanici o satiri kacirir. Ilk kosumda §7'de `git status --porcelain` sunlari basar: `?? .delegate-runs/<id>/SPEC.md`, `PROMPT.txt`, `IN_FLIGHT`, `BASELINE.txt`, `RAW_OUTPUT.log`, `FINAL.txt`. Hicbiri whitelist'te yok; RAW_OUTPUT.log ve FINAL.txt BASELINE alindiktan sonra olustugu icin baseline'da da yok. §7'nin kurali net: 'not whitelisted is a scope violation: stop and report'. Protokol ilk kosumda kendi iskelesini iscinin scope ihlali sanip duruyor. Ayrica L1 reviewer'a 'her untracked dosyayi tam oku' dendigi icin reviewer RAW_OUTPUT.log'un tamamini -- iscinin butun transcript'ini -- okumaya calisir; hem bosa token yakar hem de baglam izolasyonunun ruhuna aykiri bulgular uretir.

**Onerilen duzeltme.**

§3 preflight'a satir ekle:
```bash
git check-ignore -q .delegate-runs || echo "BLOCK: .delegate-runs/ is not git-ignored — add it to .gitignore first, otherwise every run file counts as an unwhitelisted footprint in §7"
```
ve §7'deki footprint komutunu savunmaya al:
```bash
git status --porcelain -- . ':(exclude).delegate-runs/'   # footprint check vs BASELINE.txt
```
(BASELINE.txt de ayni pathspec ile alinmali, yoksa iki liste karsilastirilamaz.)

### 7. [MAJOR] SKILL.md §8 (L0 max 5 / L1 max 2 rounds / L3 max 2 retries / "Spin detection") ile §6 ("This survives context compaction, which is the point") arasindaki celiski

**Sorun.** Protokolun her kalici seyi diskte: SPEC.md, BASE_SHA, BASELINE.txt, IN_FLIGHT, turn-N.md. Ama §8'in butun sayaclari ve "two consecutive rounds failing with the same error signature" karsilastirmasi yalnizca Claude'un baglaminda yasiyor. Sayaci kimin tuttugu, nereye yazildigi hic soylenmemis.

**Neden onemli.** Uzun bir delegasyon zaten dakikalar-saatler suruyor ve §6 arka planda kosmayi emrediyor -- yani compaction olasiligi yuksek. Compaction sonrasi Claude task dizinini yeniden okur: SPEC.md, IN_FLIGHT ve bir FINAL.txt gorur. Kacinci review turunda oldugunu, kac mimar-retry harcadigini, onceki hatanin imzasini bilmesinin hicbir yolu yoktur. Sifirdan sayar: 2 review + 2 retry daha. Kapaklar tam da bu dongoyu kesmek icin var; compaction onlari sessizce sifirliyor ve kullanici saatler sonra ayni hatanin 5. turunda oldugunu fark ediyor.

**Onerilen duzeltme.**

§8'in basina disk-durumu kurali ekle:
"**Round bookkeeping lives on disk, not in your context.** Before every dispatch (worker, review, or retry) append one line to `.delegate-runs/<task-id>/ROUNDS.txt`:
```
<ISO-8601> | worker|review|architect-retry | turn <N> | signature: <first line of the failure, or ->
```
The caps are counted from this file, not from memory: L1 review <= 2 lines, architect-retry <= 2 lines. **Spin** = two consecutive lines with the same `signature:` -> stop and ask the user. After a context compaction, read ROUNDS.txt before doing anything else."

§4 dizin semasina da `ROUNDS.txt` satirini ekle.

### 8. [MAJOR] SKILL.md §3 + §6 (kilit) — basarisiz dispatch / timeout yolu belgesiz; dispatch.py:181 (--timeout 3600 varsayilan)

**Sorun.** §6 `touch IN_FLIGHT` sonrasi dispatch ediyor, kilit §9'a kadar tutuluyor. Ama dispatch.py'nin exit 1 (timeout/protokol hatasi), exit 3 (codex surumu eski) ve exit 4 (MCP kayitli degil) yollarinda kilit yerinde kaliyor ve protokol bu durumlar icin HICBIR sey soylemiyor. §3 ise "Abort only on an existing IN_FLIGHT lock" diyor; kilidi temizlemenin tek belgeli yolu "~3 gunden eski dizinler terk edilmis sayilir". Timeout'un varligi SKILL.md'de hic gecmiyor, §6 komut blogunda --timeout bile yok.

**Neden onemli.** Makine uyur veya dispatch 3600s'de timeout'a girer. Kullanici 10 dakika sonra ikinci bir gorev delege etmek ister. Claude §3'u uygular, IN_FLIGHT'i gorur, abort eder. Belgeli tek cikis 3 gun beklemek. Pratikte iki kotu sonuctan biri: Claude 3 gun boyunca delegasyonu reddeder, ya da kurali kendi kafasina gore esneterek kilidi siler -- gercekten hala kosan bir codex app-server varsa iki isci ayni agaca yazar ve §7'nin tum footprint mantigi coper olur.

**Onerilen duzeltme.**

§3'e, IN_FLIGHT satirinin altina:
"A lock is only meaningful while its dispatch is alive. Before aborting, establish which:
```bash
ls -l .delegate-runs/*/IN_FLIGHT           # mtime = when the dispatch started
pgrep -fl 'codex app-server' || echo 'no worker process alive'
```
Process alive -> genuinely in flight, abort as stated. No process alive -> the lock is stale (crash, timeout, machine sleep). Report it to the user by task dir, say the partial changes are uncommitted, and ask before removing it. Never remove a lock while a `codex app-server` process is running."

§6 komut blogunda timeout'u gorunur kil:
`  --timeout 3600            # dispatch gives up after this and exits non-zero; the lock does NOT clear itself`

### 9. [MAJOR] SKILL.md §3 ("Run --check BEFORE writing the spec") vs doctor.py cmd_check (satir 220-246) vs dispatch.py:184-193

**Sorun.** §3 --check'in gerekcesini acikca yaziyor: 'bozuk bir isci ancak spec yazilip dispatch edildikten sonra ortaya cikmasin'. Ama cmd_check codex surumunu sadece EKRANA BASIYOR, PERMISSION_SCHEMA_MIN (0.145.0) ile karsilastirmiyor. Surum tabani yalnizca dispatch.py'de, yani spec yazildiktan sonra, exit 3 olarak devreye giriyor. Ayni sekilde config.toml'daki `model = "gpt-5.6-sol"` degeri hic dogrulanmiyor.

**Neden onemli.** codex 0.144'te olan kullanici: --check 'ok codex CLI: 0.144.0' der, preflight yesil gorunur; mimar tam bir SPEC.md yazar (Claude'un en pahali adimi), MCP icin kullanicidan onay alir, IN_FLIGHT'i atar, dispatch eder ve exit 3 alir. Kilit atilmis, spec bosa yazilmis -- §3'un onlemek icin var oldugu senaryonun kelimesi kelimesine kendisi. Model adi icin ayni: hesabinda gpt-5.6-sol olmayan kullanicida --check yesil, dispatch `turn/failed` ile oluyor ve hata sadece RAW_OUTPUT.log'da kaliyor.

**Onerilen duzeltme.**

doctor.py cmd_check'e surum tabani ekle:
```python
MIN_CODEX = (0, 145, 0)
...
if ver and ver < MIN_CODEX:
    say(BAD, f"codex {version} < 0.145 — MCP grants will fail at dispatch (exit 3). Upgrade, or dispatch without --mcp.")
    problems += 1
```
ve modeli gorunur kil:
```python
say(OK, f"worker model: {model_from_config}  (only --smoke proves your account can run it)")
```
§3'e bir cumle: "`--check` proves structure, login and CLI version. It does not prove the model in the worker config is available to your account — run `--smoke` once after install and after every codex upgrade."

### 10. [MAJOR] SKILL.md §5 (MCP secimi) ile dispatch.py:200-210 (exit 4) arasindaki kayip adim

**Sorun.** §5 sunu diyor: "Name it in the spec's MCP field and pass it as `--mcp <name>`." Sunucunun once `doctor.py --add-mcp` ile isci home'una KAYDEDILMESI gerektigi §5'te hic gecmiyor; sadece setup.md ve README'de var. dispatch.py kayitli olmayan sunucuda exit 4 ile duruyor.

**Neden onemli.** Ajan §5'i okur, kullaniciya 'bu gorev icin unityMCP'yi isciye vermem gerekiyor, onayliyor musun?' diye sorar, onay alir, spec'in MCP alanini doldurur, IN_FLIGHT'i atar, dispatch eder ve exit 4 yer. Simdi kilit tutulurken bir konfigurasyon yazmasi (--add-mcp, worker config.toml'a append) gerekiyor -- §6'nin 'kilit altinda yazma' cizgisine aykiri ve kullaniciya ikinci kez donmeyi gerektiren bir kesinti. Bu, §3'un login icin cozdugu problemin MCP kopyasi ve preflight'ta hic yok.

**Onerilen duzeltme.**

§3 preflight'a kosullu satir ekle:
```bash
# only if the task needs an MCP server:
python3 ".../doctor.py" --list-mcp          # the server must show status 'installed'
python3 ".../doctor.py" --add-mcp <name>    # register it now, not after the lock is taken
```
§5'in "Two limits" listesinin ustune:
"Registration is a preflight step, not a dispatch step. `--mcp` only *grants* a server that is already registered in the worker home; dispatch.py exits 4 on an unregistered name and you will be holding the lock when it does."

### 11. [MAJOR] doctor.py BASE_CONFIG (satir 33-47) ile makinedeki gercek ~/.codex-worker/config.toml farki

**Sorun.** Sahadaki config.toml, --init'in yazdigi BASE_CONFIG'de olmayan iki sey tasiyor: `approval_policy = "never"` ve dort adet `[projects."<path>"] trust_level = "trusted"` blogu (/private/tmp, /private/tmp/cdtest, smoke dizini ve calisma deposu). Bunlar elle eklenmis; ne setup.md'de ne doctor.py'de trust seviyesinden hic bahsedilmiyor, --check de kontrol etmiyor.

**Neden onemli.** Temiz kurulum yapan ikinci kullanici: `--init`, sonra `--smoke`. Smoke /tmp/codex-delegate-smoke'ta kosar -- o dizin trusted degil. Codex guvenilmeyen bir cwd icin client'a onay/trust istegi gonderir; dispatch.py'nin bilinmeyen istekler icin catch-all'i `self.send({"id": rid, "result": {}})` (satir 147) bos sonuc doner, ki bu ya sema hatasi ya 'reddedildi' demektir. Sonuc: smoke ya asilir ya turn/failed verir; hata login ya da model sorunu gibi gorunur ve kullanici setup.md'nin tamami login uzerine kurulu troubleshooting bolumunde yanlis izi kovalar. Ayni sey ilk gercek dispatch'te de olur. Bu kullanicinin config'inde dort adet elle eklenmis trust satiri olmasi arizanin sahada yasandiginin ve elle yamandiginin kaniti -- ama yama urune girmemis.

**Onerilen duzeltme.**

doctor.py'ye `--trust <path>` ekle, --check'e de dogrulama koy:
```python
def project_trusted(home: Path, repo: Path) -> bool:
    with open(home / "config.toml", "rb") as fh:
        projects = tomllib.load(fh).get("projects", {})
    return projects.get(str(repo.resolve()), {}).get("trust_level") == "trusted"
```
cmd_check icinde:
`say(BAD, f"{repo} is not trusted in {home}/config.toml — the worker's first turn will stall on a trust request. Run: doctor.py --trust '{repo}'")`
setup.md'ye bolum ekle: "Codex asks for folder trust per project. The worker home needs `[projects.\"<repo>\"] trust_level = \"trusted\"`; dispatch.py cannot answer that request meaningfully, so an untrusted repo looks like a hung or failed turn."
Ayrica BASE_CONFIG'e `approval_policy = "never"` ekle ya da neden gerekmedigini yaz — su an shipped config ile sahada calisan config farkli.

### 12. [MAJOR] SKILL.md §7 satir 170 (`git rev-parse HEAD  # must equal BASE_SHA`)

**Sorun.** Kural kosulsuz ve dallanmasiz. Isci git kosamadigi icin (worker-contract madde 1) HEAD'i degistirebilecek tek aktor KULLANICIDIR -- ve §3 dirty agaci normal ilan ettigi icin kullanicinin kendi isine devam etmesi tamamen beklenen bir sey. Protokol bu durumu hic ele almiyor.

**Neden onemli.** Delegasyon 40 dakika suruyor, kullanici bu sirada kendi yarim isini commit'liyor (tam da §1'in 'rollback noktasi istiyorsan kendin yarat' onerisine uyarak). §7'de HEAD != BASE_SHA. Harfi harfine uygulayan mimar turu reddeder ya da guvenlik ihlali gibi raporlar. Ustelik BASELINE.txt de gecersizlesir: commit'lenen dosyalar `git status --porcelain` ciktisindan cikar, footprint diff'i 'kaybolan' yollar uretir ve mimar bunlari yorumlayamaz. Kullanicinin en dogal davranisi protokolu kilitliyor.

**Onerilen duzeltme.**

§7'deki satiri gerekce ve dallanmayla degistir:
```bash
git rev-parse HEAD   # differs from BASE_SHA? The worker cannot run git (contract rule 1),
                     # so the USER committed during the run. Do not discard the turn.
```
Altina paragraf:
"If HEAD moved, BASELINE.txt no longer describes the tree: paths the user committed have left `git status --porcelain`, so the footprint diff shows them as disappeared rather than as the worker's work. Say this plainly in the §9 report, attribute what you can, and state which files you could not attribute. Only treat a moved HEAD as a violation if the changelog or RAW_OUTPUT.log shows the worker ran git."

### 13. [MAJOR] references/spec-template.md, "Writing an ACCEPTANCE command when the project has no test runner" (satir 75-90) ile SKILL.md §3 BASELINE snapshot'i / §7 footprint kontrolu arasindaki sira

**Sorun.** spec-template mimara "Write that script yourself, keep it in the repo" diyor -- yani mimar calisma agacina yeni dosya yaziyor. Bu adimin BASELINE.txt snapshot'ina gore ne zaman yapilacagi hicbir yerde soylenmemis; dosya isci tarafindan yazilmadigi icin FILE WHITELIST'te de yeri yok (whitelist'in tanimi: "Every path the worker may create or modify").

**Neden onemli.** Mimar §3'te BASELINE'i alir, sonra spec yazarken 'bu projede test runner yok' diye `tools/typecheck.sh` yazar, ACCEPTANCE'i ona isaret eder, dispatch eder. §7'de footprint: `?? tools/typecheck.sh` -- baseline'da yok, whitelist'te yok. Kural net: 'stop and report'. Mimar KENDI yazdigi dosyayi iscinin scope ihlali olarak raporlar ve dogru bitmis bir turu bloke eder. Test runner'i olmayan her projede, yani spec-template'in bu bolumunun hedefledigi tam senaryoda, garantili yanlis-pozitif.

**Onerilen duzeltme.**

spec-template'in o bolumunun sonuna:
"**Write the acceptance script before you snapshot BASELINE.txt (§3), or declare it.** Anything the architect writes into the tree is invisible to the worker's whitelist and will otherwise surface in §7 as an unattributable footprint. If you write it after the snapshot, add a line to the whitelist marked as yours:
```
- (architect) tools/acceptance.sh   # written by the architect, not the worker
```
and say so in the §9 report — the user will see this file in their uncommitted diff and nobody else will have mentioned it."

### 14. [MAJOR] SKILL.md tum bash bloklari (§3, §6, §7) — `.delegate-runs/` goreli, `--repo "$PWD"`

**Sorun.** Protokol calisma dizininin depo koku oldugunu her yerde varsayiyor ama hicbir yerde soylemiyor. `.delegate-runs/` goreli, `--repo "$PWD"` veriliyor, whitelist yollari ise `git status --porcelain` ciktisiyla karsilastiriliyor -- ki o cikti her zaman depo koku goreli.

**Neden onemli.** Monorepo'da kullanici `packages/web/` icinde calisiyor ve orada delegasyon istiyor. Claude komutlari oradan kosar: `.delegate-runs/` `packages/web/` altinda olusur, `--repo` `packages/web` olur (yani Codex'in workspace-write sandbox koku da orasi). Iki ariza ust uste biner: (1) §7'de `git status --porcelain` `packages/web/src/x.ts` basar, whitelist'e `src/x.ts` yazilmistir -- hicbir yol eslesmez, her dosya scope ihlali gorunur; (2) whitelist'te `packages/shared/` altinda bir yol varsa isci oraya yazamaz ve setup.md'nin belgeledigi 'patch rejected: writing outside of the project' hatasini alir -- ama setup.md o hatayi sadece `.codex/` baglaminda acikladigi icin mimar sebebi bulamaz.

**Onerilen duzeltme.**

§3'un ilk satirina koy:
```bash
test "$PWD" = "$(git rev-parse --show-toplevel)" || { echo 'BLOCK: run codex-delegate from the repository root'; }
```
ve bir cumle ekle: "Everything in this protocol is repository-root relative: the run dir, `--repo`, the FILE WHITELIST, and `git status --porcelain` output. Running from a subdirectory silently misaligns the whitelist against the footprint check and shrinks the worker's sandbox to that subdirectory."

### 15. [MINOR] doctor.py reaches_outside (satir 123-134) ve cmd_list ciktisindaki "local only — safe to hand over" (satir 189)

**Sorun.** Etiket, gercekte yapilan kontrolden cok fazlasini vaat ediyor. Kontrol edilenler sadece: isim tam eslesmeyle {"codex","codex-delegate"} mi, `cfg["url"]` uzak mi, `cfg["env"]` icinde _KEY/_TOKEN/_SECRET/_PAT ile biten anahtar var mi. Kimlik bilgisini env yerine kendi konfig dosyasindan okuyan stdio sunuculari (cok yaygin: `npx @x/slack-mcp`, token'i ~/.config'ten okur) ve `codex-bridge` / `plugin_codex_codex` gibi isimlendirilmis Codex sunuculari bu elekten 'local only — safe to hand over' etiketiyle geciyor. Ayrica claude_mcp_servers ~/.claude.json'i tum agac boyunca gezdigi icin BASKA projelerin sunucularini da listeliyor.

**Neden onemli.** Kullanici `--list-mcp` kosar, Slack/Linear/mail sunucusunu 'safe to hand over' etiketiyle gorur, --add-mcp ile kaydeder ve bir gorevde grant eder. §5'in 'outward-facing servers need the user's word per task' kurali bosa duser, cunku kullanici zaten 'guvenli' dendigi icin ikinci kez dusunmez. Ismi 'codex' olmayan bir Codex koprusu de recursive delegasyon kapisini acar -- setup.md'nin 'no task needs this' dedigi sey.

**Onerilen duzeltme.**

Etiketi iddiasiz yap ve kontrolu genislet:
```python
SELF_HINTS = ("codex",)
def reaches_outside(name, cfg):
    blob = f"{name} {cfg.get('command','')} {' '.join(cfg.get('args') or [])}".lower()
    if any(h in blob for h in SELF_HINTS):
        return "looks like a Codex server — would let the worker delegate recursively"
    ...
```
cmd_list'te son sutunu degistir:
`reason or "no remote url, no credentials in env — heuristic only, check the server yourself"`
Ciktinin altina: "Servers are collected from every project entry in ~/.claude.json, not just this one."

### 16. [MINOR] references/spec-template.md satir 87-90 ("Introduce a deliberate syntax error, confirm the command exits non-zero, and revert")

**Sorun.** Bu adim mimara, §3'un 'dirty ve rollback noktasi yok' diye tanimladigi bir calisma agacinda kasitli olarak bozuk kod yazdirtiyor ve geri almasini soyluyor. Geri alma adimi hicbir yedege yaslanmiyor.

**Neden onemli.** Mimar falsifikasyon icin kullanicinin uzerinde calistigi, commit'lenmemis bir dosyaya syntax hatasi enjekte eder. Tam o noktada context compaction olur, kullanici Esc'ler, ya da oturum duser. Geri alma yapilmaz. Kullanicinin saatlerdir commit'lemedigi degisiklikler artik enjekte edilmis bir hata tasiyor ve §1 geregi geri donulecek hicbir nokta yok. Kullanici bunu genelde gunler sonra, alakasiz bir hatayi kovalarken fark eder.

**Onerilen duzeltme.**

O paragrafi degistir:
"**Then verify the check can fail — safely.** Never break a file in place: the tree is dirty by design and there is no rollback point. Copy first, restore from the copy:
```bash
cp <file> .delegate-runs/<task-id>/falsify.bak
# introduce the error, run the acceptance command, expect non-zero
cp .delegate-runs/<task-id>/falsify.bak <file>
```
Prefer a file that is clean in `git status`, so `git checkout -- <file>` is also available as a fallback. Do this once, when you write the command."

### 17. [MINOR] doctor.py cmd_smoke satir 252-253 (`/tmp/codex-delegate-smoke`), SKILL.md §3/§6 bash bloklari, README satir 47

**Sorun.** Kurulumun tek gercek dogrulama adimi olan --smoke sabit `/tmp` yoluna yaziyor: Windows'ta boyle bir dizin yok, cok kullanicili Linux'ta baska bir kullanicinin ayni yolu yaratmis olmasi PermissionError uretir. SKILL.md'nin komutlari da (`ls -dt`, `touch`, `$PWD`, pathspec'li git cagrilari) POSIX kabuk varsayiyor. README 'Verified on macOS' diyor ama SKILL.md hicbir platform kaydi tasimiyor -- ve ajanin okudugu dosya SKILL.md.

**Neden onemli.** Windows'ta plugin kuran kullanici `--init` sonrasi `--smoke` kosar, FileNotFoundError/PermissionError alir ve bunun kurulum hatasi mi login sorunu mu oldugunu ayirt edemez -- setup.md'nin butun troubleshooting'i login uzerine kurulu. Devaminda §3 preflight'in `touch` / `ls -dt` satirlari da PowerShell'de patlar ve kullanici protokolu yarim uygular; footprint kontrolu gibi kritik adimlar sessizce atlanir.

**Onerilen duzeltme.**

doctor.py:
```python
import tempfile
with_tmp = Path(tempfile.mkdtemp(prefix="codex-delegate-smoke-"))
```
(ve bu dizini trust_level bulgusundaki --trust ile guvenilir isaretle).
setup.md Requirements satirina ekle: "POSIX shell (macOS/Linux). The preflight and lock commands are POSIX; Windows is untested."
Ayni cumleyi SKILL.md §3'un basina da koy — README'deki 'Verified on macOS' notu SKILL.md'yi okuyan ajana ulasmiyor.

### 18. [MINOR] SKILL.md §9 adim 2 ("Delete the run directory")

**Sorun.** Closeout SPEC.md'yi ve turn-*.md'leri de siliyor, ama degisiklikler commit'lenmemis halde agacta kaliyor (§9 adim 1). Yani kodun hangi whitelist altinda, hangi GOAL icin, iscinin hangi `uncertain:` varsayimlariyla yazildigina dair tek kalici kayit, kullanici o kodu incelemeden once yok ediliyor.

**Neden onemli.** Kullanici raporu aksam okur, 'yarin bakarim' der. Ertesi gun `git status` alti dosya gosterir. Hangisi izinliydi, hangisi surpriz, isci nerede belirsizlik beyan etmisti -- bakacak hicbir yer yok; sadece sohbet gecmisi kalmistir ve yeni bir oturumda ona erisim yoktur. Protokolun 'diski kalici durum say' ilkesi tam da kullanicinin ihtiyac duydugu anda terk ediliyor.

**Onerilen duzeltme.**

§9 adim 2'yi degistir:
"2. Keep the contract, drop the noise. Move `SPEC.md` and `turn-*.md` to `.delegate-runs/ARCHIVE/<task-id>/`, then delete the rest of the run dir (PROMPT.txt, RAW_OUTPUT.log, FINAL.txt, BASELINE.txt, ROUNDS.txt, IN_FLIGHT). The changes stay uncommitted for as long as the user wants; until they are reviewed, the spec is the only record of what was sanctioned. Once the user has accepted or discarded the diff, the archive can go too."
(Arsiv istenmiyorsa alternatif: §9 adim 1'deki rapora FILE WHITELIST'i ve iscinin `uncertain:` satirlarini kelimesi kelimesine kopyalamayi zorunlu kil — ama o zaman bunu acikca yaz; su anki §9 ikisini de soylemiyor.)


## Kurulum, scriptler, paketleme — 21 bulgu

_doctor.py'nin kendi mantığı sağlam, ama kurulum yüzeyi bir yabancıda üç ayrı yerden kırılıyor: (1) README/setup.md/SKILL.md'deki tüm komutlar `${CLAUDE_PLUGIN_ROOT}` ile başlıyor — bu değişken kullanıcının kendi terminalinde ve bu makinede ölçtüğüm Bash ortamında BOŞ, yani ilk kurulum komutu `python3 "/skills/.../doctor.py"` olup "No such file" veriyor; (2) stok macOS'un `/usr/bin/python3` sürümü 3.9.6 ve `tomllib` yok — doctor.py import satırında traceback ile ölüyor, README "Python 3.11+" yazıyor ama tuzağı söylemiyor (bu makinede doğruladım); (3) `--check` codex sürümünü kontrol etmiyor, dolayısıyla "spec yazmadan önce çalıştır" vaadi 0.145 altındaki kullanıcı için tutmuyor — hata ancak dispatch'te exit 3 olarak çıkıyor, üstelik README "0.145+ sadece MCP için gerekli" derken kod --mcp verilmese de reddediyor. Ayrıca dispatch.py'de gerçek bir kilitlenme var: `--timeout` yalnız `read()` döndükten sonra kontrol ediliyor, `readline()` sonsuza kadar blokluyor — asılan bir worker IN_FLIGHT kilidini kalıcı bırakıp tüm protokolü durduruyor. Güvenlik tarafında `reaches_outside` yalnızca `url` ve `env` sonekine bakıyor; bu makinedeki `playwright` (stdio, npx, env yok) "local only — safe to hand over" olarak işaretleniyor, oysa README bizzat "a browser server" örneğini veriyor — verilen MCP sunucusu Codex sandbox'ının DIŞINDA çalışıp ağa çıkıyor ve `default_tools_approval_mode:"approve"` ile her çağrısı otomatik onaylanıyor. Paketleme tarafında plugin.json'da license/repository/homepage, marketplace.json'da version/author yok; `commands/` dizini olmadığı için bu makinede plugin skill'i `codex-delegate:codex-delegate` adıyla görünüyor, README'nin vaat ettiği `/codex-delegate` değil. Toplam 21 bulgu._

### 1. [BLOCKER] README.md:37-40, 56-59; references/setup.md:5-8, 30-33; SKILL.md:77, 144

**Sorun.** Kurulumun ilk iki komutu dahil, dokümandaki her script çağrısı `"${CLAUDE_PLUGIN_ROOT}/skills/codex-delegate/scripts/..."` ile yazılmış. Bu değişken kullanıcının kendi kabuğunda tanımlı değil. Bu makinede Bash ortamında ölçtüm: `CLAUDE_PLUGIN_ROOT=[]` (boş). Boş değişkenle komut `python3 "/skills/codex-delegate/scripts/doctor.py" --init` haline geliyor.

**Neden onemli.** README'nin 'Then, once:' bloğu bir bash kod bloğu — yabancı bunu kendi terminaline yapıştırıyor ve ilk denemede `python3: can't open file '/skills/codex-delegate/scripts/doctor.py': [Errno 2] No such file or directory` alıyor. Hata mesajı yol hakkında hiçbir ipucu vermiyor (kök '/' ile başlayan yol, kullanıcı bunu bir plugin yolu sanmıyor bile). Kurulum sıfırıncı adımda ölüyor. Aynı şey SKILL.md §3 preflight ve §6 dispatch satırlarında da geçerli: Claude Bash tool ile bu satırı koşturduğunda değişken genişlemezse aynı hata düşer, üstelik sessizce — çünkü §3 çıktısı 'check başarısız' gibi değil 'dosya yok' gibi görünür.

**Onerilen duzeltme.**

README ve setup.md'ye literal yol ver, değişkeni tek yerde çöz. Gerçek kurulu yol bu makinede `~/.claude/plugins/cache/codex-delegate/codex-delegate/1.0.0/skills/codex-delegate/scripts/doctor.py`. Öneri: README'ye şu tek satırı koy —
```bash
DOCTOR=$(ls -d ~/.claude/plugins/cache/codex-delegate/*/*/skills/codex-delegate/scripts/doctor.py 2>/dev/null | tail -1) \
  || DOCTOR=~/.claude/skills/codex-delegate/scripts/doctor.py
python3 "$DOCTOR" --init && python3 "$DOCTOR" --smoke
```
Alternatif ve daha basit olanı: kullanıcıya 'terminale yapıştır' demek yerine 'Claude'a `/codex-delegate` deyip "run the one-time setup" de' demek, ve SKILL.md'ye scriptleri kendi konumundan bulan bir adım koymak (SKILL.md zaten kendi dizinini biliyor). SKILL.md:28-30'daki tek cümlelik not yetmiyor — orada sadece 'user skill kurulumu' senaryosu var, 'değişken boş' senaryosu yok.

### 2. [BLOCKER] scripts/doctor.py:26 (`import tomllib`), scripts/dispatch.py:26; README.md:46

**Sorun.** Her iki script de `tomllib` kullanıyor (Python 3.11+). README 'Python 3.11+' diyor ama komutları `python3` ile yazıyor. Stok macOS'ta `/usr/bin/python3` = 3.9.6 ve tomllib yok. Bu makinede doğruladım: `/usr/bin/python3 -c "import tomllib"` → `ModuleNotFoundError: No module named 'tomllib'`.

**Neden onemli.** Homebrew Python'u olmayan bir macOS kullanıcısı (yani ortalama kullanıcı) `python3 .../doctor.py --init` çalıştırdığında dostane bir mesaj değil, ham traceback görüyor. `tomllib` adı ona hiçbir şey ifade etmiyor; README'deki 'Python 3.11+' satırıyla bağ kurması gerekiyor. Kurulum ikinci adımda ölüyor ve doctor'ın tüm 'yol gösterici hata mesajı' katmanı devreye bile girmiyor — çünkü import satırında ölüyor.

**Onerilen duzeltme.**

Her iki script'in en üstüne, tomllib import'undan ÖNCE sürüm kapısı koy:
```python
import sys
if sys.version_info < (3, 11):
    sys.exit(
        f"codex-delegate needs Python 3.11+ (running {sys.version.split()[0]} at {sys.executable}).\n"
        "macOS ships 3.9 at /usr/bin/python3. Try: brew install python@3.13, then re-run with python3.13."
    )
import tomllib
```
Ayrıca README'de Requirements'a şu cümleyi ekle: 'macOS'un sistem python3'ü 3.9'dur; `python3 --version` ile doğrula, gerekiyorsa `python3.11`/`python3.13` ile çağır.'

### 3. [MAJOR] scripts/dispatch.py:110-119 (`read`), 244-251 (ana döngü), 98-108 (`request`)

**Sorun.** `--timeout` (varsayılan 3600s) yalnızca `server.read()` DÖNDÜKTEN sonra kontrol ediliyor. `read()` ise `self.proc.stdout.readline()` üzerinde süresiz bloklanıyor. Worker veya app-server takılırsa (MCP sunucusu yanıt vermiyor, model isteği asılı kaldı, ağ duvarı) `readline()` hiç dönmez ve timeout ASLA tetiklenmez. `request()` döngüsünde ise deadline hiç yok.

**Neden onemli.** SKILL.md §6 'arka planda çalıştır, harness çıkınca seni uyandırır, polling yapma' diyor ve kilidi (`IN_FLIGHT`) closeout'a kadar tutuyor. Process hiç çıkmazsa Claude hiç uyanmaz, IN_FLIGHT hiç silinmez, §3 preflight'ın 'tek sert kapısı' kalıcı olarak kapanır: sonraki oturumda kullanıcı delegasyon isteyince Claude 'IN_FLIGHT var, abort' der ve kimse kilidi kimin bıraktığını bilmez. Kullanıcının elle `ps` bakıp `.delegate-runs/*/IN_FLIGHT` silmesi gerekir — ama bu kurtarma yolu hiçbir yerde yazmıyor. Timeout parametresinin varlığı yanlış güven veriyor.

**Onerilen duzeltme.**

stdout okumasını gerçekten süreli yap. En az invaziv hali, okuma thread'i + queue:
```python
import queue, threading
# __init__ içinde:
self._q: queue.Queue[str|None] = queue.Queue()
threading.Thread(target=self._pump, daemon=True).start()

def _pump(self):
    for line in self.proc.stdout:
        self._q.put(line)
    self._q.put(None)

def read(self, deadline: float) -> dict:
    try:
        line = self._q.get(timeout=max(1.0, deadline - time.monotonic()))
    except queue.Empty:
        raise DispatchError(f"no output from codex app-server for {self.timeout}s — giving up")
    if line is None:
        raise DispatchError("codex app-server exited unexpectedly")
    ...
```
ve `request()`'e de aynı deadline'ı geçir. Ayrıca SKILL.md §10 Recovery'ye 'asılı kalmış IN_FLIGHT'ı nasıl temizlersin' adımını ekle (`ls .delegate-runs/*/IN_FLIGHT`, process yoksa sil).

### 4. [MAJOR] scripts/doctor.py:123-134 (`reaches_outside`), 188-192 (`cmd_list` çıktısı); README.md:18-20, 61-63

**Sorun.** `reaches_outside` bir sunucuyu yalnızca üç şeye bakarak 'blocked' işaretliyor: adı codex mi, `cfg["url"]` uzak mı, `cfg["env"]` anahtarları `_KEY/_TOKEN/_SECRET/_PAT` ile mi bitiyor. Ağa çıkan ama bunların hiçbirine uymayan stdio sunucular 'local only — safe to hand over' etiketi alıyor. Bu makinedeki gerçek config ile doğruladım: `playwright` → `{type: stdio, command: npx, args: ['@playwright/mcp@latest'], env: {}}` → hiçbir kurala takılmıyor → 'local only — safe to hand over'.

**Neden onemli.** README:18-20 bizzat 'the Unity server, a browser server, whatever the task genuinely needs' diyerek browser sunucusunu örnek veriyor. Kullanıcı `--add-mcp playwright` yapıyor, doctor yeşil ışık yakıyor, dispatch onu `default_tools_approval_mode: "approve"` ile veriyor (dispatch.py:228) — yani her tool çağrısı otomatik onaylı. MCP sunucusu Codex'in sandbox'ının DIŞINDA, ayrı bir process olarak koşuyor: `sandbox_workspace_write.network_access = false` ayarı onu bağlamıyor. Sonuç: 'sandboxed worker' anlatısı geçerliliğini kaybediyor — worker rastgele URL'e gidebilir, kullanıcının oturum açmış tarayıcısını sürebilir, veri sızdırabilir. Aynı boşluk `@modelcontextprotocol/server-filesystem /Users/me` gibi bir sunucuda sandbox'ın dosya sınırını da deler. Doktor bunu 'safe' diye yazdığı için kullanıcı §5'teki 'outward-facing servers need the user's word' korumasını da atlamış olur — çünkü outward-facing sayılmıyor.

**Onerilen duzeltme.**

İki değişiklik. (a) Sınıflandırmayı üç kovaya çıkar ve varsayılanı 'unknown' yap:
```python
NETWORKING_HINTS = ("playwright", "puppeteer", "browser", "fetch", "http", "curl",
                    "github", "slack", "gmail", "notion", "linear", "jira", "aws", "docker")
...
if cfg.get("command"):
    blob = " ".join([str(cfg.get("command"))] + [str(a) for a in cfg.get("args") or []]).lower()
    if any(h in blob or h in name.lower() for h in NETWORKING_HINTS):
        return "stdio server that reaches the network (runs outside the Codex sandbox)"
    return None  # ama etiketi 'local?' yap, 'safe to hand over' değil
```
(b) `cmd_list` çıktısındaki 'local only — safe to hand over' ifadesini değiştir. Doğrusu: `'no remote URL or credential in its config — still runs OUTSIDE the sandbox'`. Ve tablo altına şu satırı ekle: 'Any granted MCP server executes outside Codex's sandbox and its calls are auto-approved. network_access=false does not constrain it.' Aynı uyarı setup.md:17-27 'Why the worker gets its own CODEX_HOME' bölümüne de girsin — orada 'defence in depth, not a guarantee' deniyor ama gerekçe olarak sadece 'worker'ın shell'i var' gösteriliyor; asıl delik verilen MCP sunucusu.

### 5. [MAJOR] scripts/doctor.py:220-246 (`cmd_check`); scripts/dispatch.py:184-193; README.md:46

**Sorun.** `--check` codex sürümünü yazdırıyor ama karşılaştırmıyor. Sürüm kapısı yalnızca dispatch.py'de (`PERMISSION_SCHEMA_MIN = (0,145,0)`) ve orada `args.mcp` boş olsa bile uygulanıyor — kod `--mcp` verilip verilmediğine bakmadan exit 3 veriyor. README:46 ise '`codex` on PATH (0.145+ to grant MCP servers)' diyerek MCP kullanmayanın daha eski sürümle çalışabileceğini ima ediyor. Ayrıca `subprocess.run(["codex","--version"])` returncode kontrolsüz ve timeout'suz; codex bozuksa `stdout` boş döner ve doctor `[  ok  ] codex CLI: ` yazıp geçer.

**Neden onemli.** codex 0.140 kullanan bir yabancı: `--check` tamamen yeşil → SKILL.md §3'ün 'Run --check BEFORE writing the spec, a broken worker otherwise surfaces only after the spec is written' vaadine güvenip tam spec yazıyor → dispatch anında exit 3 ve 'Upgrade codex, or dispatch without --mcp' mesajını okuyor → --mcp'siz deniyor → yine exit 3, çünkü kapı koşulsuz. Mesaj kullanıcıya olmayan bir çıkış yolu öneriyor. Harcanan: bir spec'lik Claude context'i, tam da bu projenin korumaya çalıştığı kaynak.

**Onerilen duzeltme.**

(a) `cmd_check`'e sürüm kapısı taşı — dispatch.py'deki `codex_version()`/`PERMISSION_SCHEMA_MIN`'i paylaşılan bir yerden kullan veya kopyala:
```python
ver = codex_version()
if ver < (0, 145, 0):
    say(BAD, f"codex {'.'.join(map(str,ver))} < 0.145 — MCP grants use a reply schema this script does not implement. Upgrade: npm i -g @openai/codex")
    problems += 1
```
(b) dispatch.py:185'i `if args.mcp and version < PERMISSION_SCHEMA_MIN:` yap ki mesajdaki 'or dispatch without --mcp' gerçekten bir çıkış yolu olsun. (c) `subprocess.run(["codex","--version"], timeout=30)` ekle ve `returncode != 0` ise FAIL ver.

### 6. [MAJOR] scripts/doctor.py:58-63 (`auth_identity`), 234-246 (`cmd_check`); scripts/dispatch.py:80-83; README.md:46

**Sorun.** `auth_identity` kimliği yalnız `auth.json` içindeki `tokens.account_id`'den okuyor — yani ChatGPT/abonelik login'i varsayıyor. API anahtarıyla kimlik doğrulayan kullanıcıda (`auth.json` içinde `OPENAI_API_KEY`, `tokens` yok) `work_id` None döner. Dahası dispatch.py:80-83, worker'a geçen ortamdan `*_API_KEY` ile biten her değişkeni siliyor — yani `OPENAI_API_KEY` env yoluyla da çalışmıyor. README ise sadece 'and a Codex login' diyor.

**Neden onemli.** API-key ile Codex kullanan bir yabancıda `--check` her seferinde `[ FAIL ] no usable login in ~/.codex-worker — run --init` diyor; kullanıcı `--init` çalıştırıyor, `--init` 'auth: already linked' diyerek başarı raporluyor, `--check` yine aynı FAIL'i veriyor. Sonsuz ve çelişkili bir döngü: iki komut birbirini yalanlıyor ve hiçbiri gerçek sebebi ('bu araç ChatGPT login'i bekliyor') söylemiyor. Kullanıcı token'ının bozuk olduğunu sanıp `codex login`/`logout` deniyor.

**Onerilen duzeltme.**

(a) `auth_identity`'yi API-key durumunu tanıyacak hale getir ve `cmd_check`'te ayrı mesaj ver:
```python
def auth_identity(home):
    try: data = json.loads((home/"auth.json").read_text())
    except Exception: return None, None, None
    tok = data.get("tokens") or {}
    kind = "chatgpt" if tok.get("account_id") else ("apikey" if data.get("OPENAI_API_KEY") else None)
    return tok.get("account_id"), data.get("last_refresh"), kind
```
ve `kind == "apikey"` ise: `say(WARN, "worker home uses an API key, not a ChatGPT login — account-id desync check is skipped; verify with --smoke")` (FAIL değil). (b) README Requirements'a açıkça yaz: 'a Codex login — `codex login` (ChatGPT sign-in). API-key auth is untested; --check's account comparison does not apply.'

### 7. [MAJOR] scripts/doctor.py:166-175 (`cmd_init`), 76 (`sync_auth` erken dönüş)

**Sorun.** `sync_auth`, ana home'da login yoksa `"no login in main home — run `codex login` first"` STRING'i döndürüyor; `cmd_init` bunu `say(OK, f"auth: {...}")` ile OK etiketiyle basıyor ve `return 0` ediyor.

**Neden onemli.** Yabancı `--init` çalıştırıyor, ekranda iki satır `[  ok  ]` görüyor, kurulumun bittiğini sanıyor. Exit code 0. Sonra `--smoke` çalıştırıyor ve dakikalarca bekledikten sonra opak bir `turn failed` blob'u alıyor — çünkü hiç login yok. İki komutluk kurulumun ilki, en yaygın eksik önkoşulu OK diye raporluyor. Bir kurulum script'i (veya Claude) exit code'a bakarsa yanlış sonuca varıyor.

**Onerilen duzeltme.**

`sync_auth`'ı `(ok: bool, msg: str)` döndürecek şekilde değiştir, `cmd_init`'i:
```python
ok, msg = sync_auth(home, MAIN_HOME)
say(OK if ok else BAD, f"auth: {msg}")
return 0 if ok else 1
```
ve mesajı eyleme çevir: `"no login in ~/.codex — run `codex login`, then re-run --init"`.

### 8. [MAJOR] scripts/doctor.py:37 (`BASE_CONFIG` içinde `model = "gpt-5.6-sol"`), 166-172 (`cmd_init`)

**Sorun.** Worker'ın modeli config'e sabit gömülü ve `--init` mevcut config.toml'u ASLA güncellemiyor ('already exists, left untouched'). Ne `doctor.py` ne `dispatch.py` modelin kullanıcının planında var olduğunu doğrulamıyor; `--check` model hakkında tek kelime etmiyor.

**Neden onemli.** İki ayrı arıza. (1) Bu modele erişimi olmayan bir plandaki yabancıda her dispatch `turn/failed` ile ölüyor; `--check` yeşil, `--smoke` opak bir JSON blob'u basıyor ('smoke test failed (exit 1): ERROR: turn failed: {...}') ve hiçbir yer 'modelini değiştir' demiyor. (2) Plugin v1.1'de model adı değişirse mevcut kullanıcılar hiç almıyor — `--init` sessizce eski config'i koruyup 'ok' diyor, `--check` de config içeriğine bakmadığı için fark etmiyor. Kullanıcı yeni sürüme geçtiğini sanıp eski davranışı yaşıyor.

**Onerilen duzeltme.**

(a) `--init`'e `--model` bayrağı ekle ve varsayılanı README'de belirt; (b) `cmd_init`'e sürüm damgası koy — BASE_CONFIG'in ilk satırına `# codex-delegate config schema v1` yaz, mevcut dosyada bu satır yoksa/eskiyse `say(WARN, "config.toml predates this plugin version; review it against BASE_CONFIG or delete and re-run --init")` de; (c) `cmd_check`'te config.toml'daki `model` değerini yazdır (`say(OK, f"worker model: {cfg.get('model')}")`) ki kullanıcı neyle koştuğunu görsün — CLAUDE.md §11.3'ün 'config dosyasına güvenme, çalıştığını teyit et' ilkesinin buradaki karşılığı bu.

### 9. [MAJOR] README.md:31-33, 51; SKILL.md:3, 36; .claude-plugin/ (commands/ dizini yok)

**Sorun.** README ve SKILL.md §0 çağrı biçimini `/codex-delegate` olarak veriyor. Repo'da `commands/` dizini yok; slash komut, skill'in kendisinden türüyor. Bu makinede yüklü skill listesinde plugin skill'i `codex-delegate:codex-delegate` adıyla, ayrıca elle kopyalanmış user-skill de `codex-delegate` adıyla görünüyor — yani plugin-only kuran birinde ad `codex-delegate:codex-delegate` oluyor.

**Neden onemli.** Yabancı plugin'i kuruyor, README'nin dediği gibi `/codex-delegate` yazıyor ve komut listesinde bulamıyor (ya da hiçbir şey olmuyor). §0 aktivasyon kapısı tam olarak bu çağrıya bağlı: 'User invoked /codex-delegate -> approved for this session'. Çağrı adı tutmazsa kullanıcı skill'in çalışmadığı sonucuna varıp kaldırıyor — oysa skill yükleniyor, sadece adı farklı.

**Onerilen duzeltme.**

İki seçenek: (a) repo'ya `commands/codex-delegate.md` ekle (3 satır: frontmatter + 'Load the codex-delegate skill and follow its protocol; treat this invocation as the §0 session approval.') — böylece `/codex-delegate` her kurulumda garanti çalışır; (b) README ve SKILL.md §0'ı 'invoke `/codex-delegate` (plugin installs may expose it as `/codex-delegate:codex-delegate`)' diye düzelt. (a) tercih edilir, çünkü §0 metni tek bir kanonik tetikleyiciye dayanıyor.

### 10. [MAJOR] scripts/doctor.py:93-115 (`claude_mcp_servers`)

**Sorun.** MCP keşfi yalnız proje `.mcp.json` ve `~/.claude.json`'a bakıyor. Plugin üzerinden gelen MCP sunucuları bu iki dosyada değil, plugin manifest'lerinde duruyor ve bulunmuyor. Bu makinede doğruladım: oturumda `mcp__plugin_context7_context7__*` araçları mevcut, ama `~/.claude.json` taramasında `context7` yok — yalnız `UnityMCP, codex, meshy, playwright, unityMCP` çıkıyor. Ayrıca `walk()` tüm proje kayıtlarını geziyor: `--list-mcp` başka projelerin sunucularını da listeliyor ve `found.setdefault` yüzünden aynı adın ilk rastlanan (muhtemelen yanlış projedeki) config'i kazanıyor.

**Neden onemli.** Kullanıcı bir plugin MCP sunucusunu worker'a vermek istiyor, `--add-mcp context7` diyor, `[ FAIL ] 'context7' is not among Claude's MCP servers: UnityMCP, codex, meshy, playwright, unityMCP` alıyor. Mesaj kesin konuşuyor ('Claude'un sunucuları arasında değil') ama yanlış — Claude'da var, doctor bakmadığı yerde. Kullanıcı sunucusunun adını yanlış yazdığını sanıp uğraşıyor. Çapraz-proje karışıklığı ise daha sinsi: A projesinde `--add-mcp x` yapıyorsun, B projesindeki `x` config'i worker'a yazılıyor.

**Onerilen duzeltme.**

(a) `~/.claude/plugins/` altındaki plugin `.mcp.json` / `plugin.json` `mcpServers` alanlarını da tara; en azından bulunamayan ad için mesajı dürüst yap: `f"'{name}' not found in {project}/.mcp.json or ~/.claude.json. Plugin-provided servers are not discoverable here — add it manually with: codex mcp add {name} ..."`. (b) `--list-mcp` çıktısında her sunucunun hangi kaynaktan geldiğini bir sütun olarak göster (project / user / other-project) ve varsayılan olarak yalnız project+user kapsamını listele, tümü için `--all` iste.

### 11. [MAJOR] scripts/doctor.py:118-120 (`SELF_SERVERS`)

**Sorun.** Özyinelemeli delegasyon koruması sunucu ADINA bakıyor: `SELF_SERVERS = {"codex", "codex-delegate"}`. Oysa asıl imza komutta: bu makinedeki kayıt `{command: "codex", args: ["mcp-server"], env: {CODEX_HOME: ...}}`.

**Neden onemli.** Kullanıcı Codex MCP sunucusunu `codexServer`, `openai-codex`, `worker` ya da `impl` gibi bir adla kaydetmişse (ki çok yaygın — insanlar MCP sunucularını kendi kafalarına göre adlandırır) ad eşleşmiyor, url yok, kimlik bilgisi env'i yok → `--list-mcp` onu 'local only — safe to hand over' diye işaretliyor. Worker kendi worker'larını doğurabiliyor: bütçesiz, denetimsiz, ve her biri ana `~/.codex` home'una (env'deki CODEX_HOME sayesinde) işaret edebiliyor — yani izolasyonun tamamı gidiyor. Kodun kendi yorumu (satır 118-119) bunun 'hiçbir görevin ihtiyacı olmadığı' bir şey olduğunu söylüyor; kontrol o iddiayı taşımıyor.

**Onerilen duzeltme.**

Komuta bak, ada değil:
```python
cmd = str(cfg.get("command") or "")
args = " ".join(str(a) for a in cfg.get("args") or [])
if name.lower() in SELF_SERVERS or Path(cmd).name in {"codex", "claude"} or "mcp-server" in args and Path(cmd).name == "codex":
    return "is a coding-agent server — would let the worker delegate recursively"
```
Ayrıca `claude`/`claude mcp serve` durumunu da aynı kovaya al.

### 12. [MAJOR] README.md:28-47 (Install + Requirements)

**Sorun.** README kurulum akışında codex CLI'ın kendisinin nasıl geleceği ve login adımı yok. Requirements satırı '`codex` on PATH (0.145+ ...), Python 3.11+, and a Codex login' diyor ama bunları sağlamanın komutu hiçbir yerde yok. `.delegate-runs/` gitignore adımı da README'de yok (yalnız setup.md:64'te, hem de 'Why the run directory is .delegate-runs/' başlıklı bir gerekçe bölümünün sonuna gömülü). İlk delegasyonun neye benzediğine dair tek bir örnek de yok — 'Invoke /codex-delegate' sonrası kullanıcı ne yazacağını bilmiyor.

**Neden onemli.** Yabancı README'yi yukarıdan aşağı takip ediyor: plugin kuruluyor, `--init` çalışıyor ama login yok diye anlamsız çıktı veriyor (bkz. ayrı bulgu), `--smoke` opak hata veriyor. Codex'i nereden kuracağını README'de bulamayıp projeyi terk ediyor. Gitignore atlanınca ise ilk delegasyonda §7 footprint kontrolü `?? .delegate-runs/` yolunu 'whitelist dışı, atfedilemez' diye işaretliyor ve Claude ilk raporunda kullanıcıya sahte bir kapsam ihlali bildiriyor.

**Onerilen duzeltme.**

Install bölümünü sıralı ve eksiksiz yap:
```markdown
## Install
0. Codex CLI and a login (once, outside this plugin):
   npm install -g @openai/codex   # 0.145 or newer
   codex login
   python3 --version               # must be 3.11+; macOS ships 3.9
1. /plugin marketplace add BurakErdemci/codex-delegate
   /plugin install codex-delegate
2. python3 "<doctor path>" --init && python3 "<doctor path>" --smoke
3. echo '.delegate-runs/' >> .gitignore   # in every repo you delegate in
```
ve 'Use' bölümüne somut bir ilk delegasyon örneği ekle ('/codex-delegate, then: "implement the X module per these rules; acceptance is `npm test`"'). Ayrıca `cmd_check`'e `.delegate-runs/` gitignore kontrolü koy (proje bir git repo'suysa `git check-ignore -q .delegate-runs` çalıştır, değilse WARN).

### 13. [MAJOR] scripts/doctor.py:196-217 (`cmd_add`), 141-153 (`render_server`)

**Sorun.** `--add-mcp` tek yönlü: kayıt eklenir, kaldırma komutu yok. `--force` ile eklenen bir sunucunun env değerleri (gerçek sırlar) `~/.codex-worker/config.toml`'a düz metin kopyalanıyor; config.toml `write_text` ile 644 modda yazılıyor (auth.json kopyası 0600 alıyor, config almıyor). Ve dispatch anında `reaches_outside` YENİDEN değerlendirilmiyor — dispatch.py:200-211 yalnız 'config.toml'da kayıtlı mı' diye bakıyor.

**Neden onemli.** README:63 ve setup.md:40 'Registering a server does not grant it. Grants are per dispatch' diyor — bu yalnızca yarı doğru. Bir kez `--force` ile eklenmiş uzak/kimlik-bilgili sunucu kalıcı olarak orada duruyor ve sonraki her dispatch onu `--mcp` ile adlandırırsa hiçbir uyarı çıkmadan veriliyor; ilk seferdeki 'bu gerçekten istediğin mi' sorusu bir daha sorulmuyor. Kullanıcı geri almak istediğinde ne doctor'da bir komut var ne dokümanda bir yönerge — config.toml'u elle düzenlemesi gerekiyor ve orada bozuk TOML bırakırsa `--check` bunu fark etmiyor (aşağıdaki bulgu). Ayrıca izolasyonun amacı 'worker sırları görmesin' iken sır artık ikinci bir dosyada, dünya-okunur modda.

**Onerilen duzeltme.**

(a) `--remove-mcp NAME` ekle (tomllib ile oku, tabloyu çıkar, yeniden yaz) ve README/setup.md'de göster. (b) `cmd_add` sonunda `config.chmod(0o600)`. (c) Sırları kopyalamak yerine referansla: env değeri yerine `bearer_token_env_var` / `${VAR}` kullan, kopyalıyorsan `say(WARN, f"copied secret env {k} into {config} — it now exists in two places")` de. (d) dispatch.py'de granted her sunucu için `reaches_outside`'ı yeniden koştur ve blocked ise stderr'e görünür bir satır bas (ya da `--allow-outward` isteyerek reddet).

### 14. [MINOR] scripts/doctor.py:249-266 (`cmd_smoke`)

**Sorun.** Smoke testi üç ayrı kırılganlık taşıyor: (1) `/tmp/codex-delegate-smoke` sabit ve tahmin edilebilir bir yol — çok kullanıcılı bir Linux makinesinde başkası bu dizini önceden yaratmışsa `mkdir(exist_ok=True)` sonrası `write_text` PermissionError traceback'i veriyor; (2) `subprocess.run(..., timeout=300)` yakalanmıyor — 5 dakikada dönmezse kullanıcı `subprocess.TimeoutExpired` traceback'i görüyor; (3) o timeout'ta dispatch.py SIGKILL ile ölüyor, dolayısıyla `finally: server.close()` çalışmıyor ve `codex app-server` process'i öksüz kalıp arkada koşmaya devam ediyor. Ayrıca `RAW_OUTPUT.log` bu dizinde 'a' modunda sonsuza dek büyüyor.

**Neden onemli.** Kurulumun ikinci komutu bu. Yavaş bir bağlantıda veya modelin yavaş olduğu bir anda kullanıcı 5 dakika bekleyip Python traceback'i alıyor, arkada bir codex process'i bırakıyor ve bunu bilmiyor. 'the only check worth trusting' (README:43-44) diye tanıtılan komut, başarısızlığında en kötü mesajı veren komut oluyor.

**Onerilen duzeltme.**

```python
import tempfile
with tempfile.TemporaryDirectory(prefix="codex-delegate-smoke-") as d:
    tmp = Path(d)
    ...
    try:
        result = subprocess.run([...], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        say(BAD, "smoke test did not finish in 300s. Check `ps aux | grep 'codex app-server'` and kill leftovers, then re-run --check.")
        return 1
```
ve dispatch.py'de `Popen(..., start_new_session=True)` + `close()` içinde `os.killpg(os.getpgid(self.proc.pid), SIGTERM)` kullan ki öldürülen dispatch, app-server'ı ve onun doğurduğu MCP process'lerini de götürsün.

### 15. [MINOR] scripts/doctor.py:156-161 (`worker_servers`), 228-233 (`cmd_check`)

**Sorun.** `worker_servers` config.toml okuma hatalarının HEPSİNİ yutuyor (`except Exception: return set()`). `cmd_check` yalnız dosyanın var olup olmadığına bakıyor, ayrıştırılabilirliğine değil. Bozuk TOML → `--check` 'worker MCP servers: none' der ve OK verir.

**Neden onemli.** Kullanıcı config.toml'u elle düzenledi (model değiştirmek, bir MCP kaldırmak için — kaldırma komutu olmadığından bunu yapmak zorunda) ve bir yerde tırnak unuttu. `--check` tertemiz. Spec yazılır, dispatch edilir, dispatch.py `cannot read ~/.codex-worker/config.toml: ...` ile ölür — ama bu hata yalnızca `--mcp` verildiğinde tetiklenir (`configured_mcp_names` yalnız o zaman çağrılıyor); MCP'siz dispatch'te codex app-server bozuk config yüzünden başlangıçta ölür ve dispatch.py `codex app-server exited unexpectedly` der, çünkü stderr DEVNULL'a gidiyor (ayrı bulgu). Yani gerçek sebep hiçbir yerde görünmez.

**Onerilen duzeltme.**

`cmd_check`'te config'i açıkça ayrıştır:
```python
try:
    with open(home/"config.toml","rb") as fh: cfg = tomllib.load(fh)
    say(OK, f"config parses; model={cfg.get('model')}, mcp={', '.join(cfg.get('mcp_servers',{})) or 'none'}")
except Exception as exc:
    say(BAD, f"{home}/config.toml does not parse: {exc}"); problems += 1
```

### 16. [MINOR] scripts/dispatch.py:84-88 (`stderr=subprocess.DEVNULL`)

**Sorun.** `codex app-server` process'inin stderr'i tamamen atılıyor. Başlatma hatalarının (bozuk config, tanınmayan model, eksik auth, MCP sunucusu başlatılamadı) tek anlatıldığı yer orası.

**Neden onemli.** App-server başlangıçta ölürse `read()` boş satır alır ve kullanıcı tek bir cümle görür: `ERROR: codex app-server exited unexpectedly`. RAW_OUTPUT.log'da hiçbir şey yok. Kurulum sorunları — ki bu görevin konusu tam olarak bu — teşhis edilemez hale geliyor. Yabancı burada takılırsa gidecek yeri yok; setup.md'nin troubleshooting bölümü de bu mesajı içermiyor.

**Onerilen duzeltme.**

stderr'i transcript'e yönlendir: `stderr=log` (log dosya nesnesi zaten açık, text modda) veya ayrı bir `STDERR.log`'a. Ve `read()`'in EOF hatasını zenginleştir: `raise DispatchError(f"codex app-server exited (rc={self.proc.poll()}); see stderr in {log_path}")`. setup.md'nin 'Known behaviours' listesine bu mesajı ve nereye bakılacağını ekle.

### 17. [MINOR] scripts/doctor.py:30 (`MAIN_HOME = Path.home()/".codex"`), 66-88 (`sync_auth`)

**Sorun.** Ana Codex home'u sabit `~/.codex` varsayılıyor; codex'in kendi `CODEX_HOME` ortam değişkeni yok sayılıyor. `--codex-home` bayrağı yalnız WORKER home'unu değiştiriyor, MAIN_HOME'u değil.

**Neden onemli.** `CODEX_HOME=/Users/x/dev/codex-home` ile çalışan bir kullanıcı (kurumsal/çok hesaplı kurulumlarda yaygın) `--init` çalıştırınca `no login in main home — run codex login first` görüyor, oysa gayet login. `codex login` yapıyor, o da CODEX_HOME'a yazıyor, mesaj değişmiyor. Döngüden çıkış yolu yok, çünkü ana home'u işaret edecek bayrak yok.

**Onerilen duzeltme.**

`MAIN_HOME = Path(os.environ.get("CODEX_HOME") or Path.home()/".codex")` yap ve `--main-home` bayrağı ekle. Mesaja da yolu koy: `f"no login in {main} — run `codex login` first (set CODEX_HOME if your main home is elsewhere)"`.

### 18. [MINOR] scripts/doctor.py:74-88 (`sync_auth` symlink fallback), 84-88; scripts/dispatch.py:84 (Popen argv)

**Sorun.** Windows'ta iki ayrı kırık nokta. (1) `dst.symlink_to(src)` Windows'ta Developer Mode/yönetici olmadan OSError verir → `shutil.copy2` fallback'ine düşer → setup.md:42-49'da 'the failure that looks like a broken token' diye anlatılan desync senaryosunun tam kurulumu sessizce geri gelir; `dst.chmod(0o600)` de Windows'ta etkisiz. (2) npm ile kurulan codex Windows'ta `codex.cmd`'dir: `shutil.which("codex")` onu bulur (yani `--check` geçer), ama `subprocess.Popen(["codex", "app-server"], shell=False)` FileNotFoundError verir — ve bu istisna dispatch.py:221'de `try` bloğunun DIŞINDA olduğu için ham traceback olarak çıkar.

**Neden onemli.** Windows kullanıcısında `--check` tamamen yeşil (codex bulundu, config var, auth kopyalandı), `--smoke` ise Python traceback'i ile ölüyor. Doktorun bütün amacı olan 'önce kontrol et' katmanı, platformun tek gerçek arızasını göremiyor. README hiçbir yerde 'macOS/Linux only' demiyor; sadece 'Verified on macOS' diyor ki bu 'Windows'ta çalışmaz' demek değil.

**Onerilen duzeltme.**

(a) `Popen`'da argv[0] olarak `shutil.which("codex")` sonucunu kullan (Windows'ta tam `.cmd` yolunu döndürür). (b) `sync_auth`'ta copy fallback'ine düşünce sessiz kalma: `return "copied (symlink unavailable) — WARNING: re-run --init after any `codex login`, the two files can now diverge"`. (c) `server = AppServer(...)` çağrısını `try` bloğunun içine al ki FileNotFoundError de `ERROR:` satırına dönüşsün. (d) README Requirements'a net destek beyanı: 'macOS and Linux. Windows is untested.'

### 19. [MINOR] .claude-plugin/plugin.json:1-7; .claude-plugin/marketplace.json:1-11

**Sorun.** plugin.json'da `license`, `repository`, `homepage` yok. marketplace.json'daki plugin girdisinde `version`, `author`, `category`, `keywords`, `license` yok — sadece name/source/description var. Repo'da LICENSE (MIT) dosyası var ama manifest'lerin hiçbiri bunu beyan etmiyor.

**Neden onemli.** `/plugin` arayüzünde ve marketplace listelemesinde kullanıcı bir sürüm numarası, bir kaynak repo bağlantısı ya da lisans göremiyor. Güncelleme geldiğinde 'hangi sürümdeyim' sorusunun cevabı yok (bu makinede cache yolu 1.0.0 gösteriyor, ama liste göstermiyor). Bir yabancının 'bu ne, kim yazmış, güvenli mi' sorusuna manifest cevap vermiyor — ki bu plugin worker'a MCP sunucusu devreden bir araç; kaynak şeffaflığı burada süs değil.

**Onerilen duzeltme.**

plugin.json:
```json
{
  "name": "codex-delegate",
  "description": "...",
  "version": "1.0.0",
  "author": { "name": "Burak Erdemci", "url": "https://github.com/BurakErdemci" },
  "homepage": "https://github.com/BurakErdemci/codex-delegate",
  "repository": "https://github.com/BurakErdemci/codex-delegate",
  "license": "MIT",
  "keywords": ["codex", "delegation", "subagent", "mcp", "review"]
}
```
marketplace.json plugin girdisine `"version": "1.0.0"`, `"author": {"name": "Burak Erdemci"}`, `"category": "workflow"`, `"license": "MIT"`, `"homepage"` ekle. Sürümü tek kaynaktan tut ve dispatch.py:223'teki `clientInfo.version: "1.0.0"` string'inin de aynı sürümü göstermesi için not düş (şu an sabit ve sürüm yükseltmede unutulacak).

### 20. [MINOR] SKILL.md:28-30; makinedeki kurulum durumu (~/.claude/skills/codex-delegate/ + ~/.claude/plugins/cache/codex-delegate/)

**Sorun.** İki kurulum yolu (plugin ve düz user skill) birbirini dışlamıyor ve hiçbir yer 'birini seç' demiyor. Bu makinede İKİSİ birden kurulu ve içerikleri ayrışmış durumda: `diff -rq ~/.claude/skills/codex-delegate/ <repo>/skills/codex-delegate/` → SKILL.md ve spec-template.md farklı, references/research-task.md yalnız birinde var. Skill listesinde de iki ayrı giriş görünüyor: `codex-delegate` ve `codex-delegate:codex-delegate`.

**Neden onemli.** Aynı adı taşıyan iki skill yüklüyken `/codex-delegate` hangisini çağırdığı belirsiz, ve bu ikisi farklı protokol metinleri içeriyor. Kullanıcı SKILL.md'yi düzelttiğini sanıp diğer kopyayı çalıştırıyor; hata ayıklaması imkânsız bir tutarsızlık ('ben bunu düzeltmiştim'). Bir yabancıda tipik senaryo: önce user skill olarak deneyip sonra plugin'i kurmak — eski kopya kalır ve sessizce yarışır.

**Onerilen duzeltme.**

SKILL.md:28-30'daki cümleyi güçlendir ve README'ye bir 'Install (choose ONE)' başlığı koy: 'Plugin install and plain user-skill install are mutually exclusive. If you previously copied this into `~/.claude/skills/codex-delegate/`, delete it before installing the plugin — two copies of the same skill name load simultaneously and you cannot tell which protocol text is in effect.' Ayrıca `doctor.py --check`'e ucuz bir çakışma kontrolü ekle: `~/.claude/skills/codex-delegate` ve `~/.claude/plugins/cache/codex-delegate` ikisi de varsa WARN bas.

### 21. [POLISH] scripts/doctor.py:269-297 (`main`), 274-279 (argparse), 286-296 (`rc |=`)

**Sorun.** Üç küçük kullanılabilirlik pürüzü: (1) `--init`, `--check`, `--smoke`, `--list-mcp` bayraklarının hiçbirinde `help=` yok ve `ArgumentParser(description=...)` tek satırlık; argümansız çalıştırınca basılan `print_help()` çıktısı bayrakları anlamsız bir liste olarak gösteriyor — oysa modül docstring'i (satır 4-9) tam olarak istenen açıklamayı içeriyor. (2) `--project` bayrağı ne README'de ne setup.md'de geçiyor; kullanıcı repo dışından `--list-mcp` çalıştırınca proje `.mcp.json`'ı sessizce atlanıyor ve neden eksik olduğunu anlamıyor. (3) Çıkış kodları bitwise OR ile birleştiriliyor (`rc |= ...`): `--add-mcp` (2) + `--check` (1) birlikte verilirse 3 çıkıyor; anlamsız bileşik kod.

**Neden onemli.** Yabancının ilk refleksi `python3 doctor.py` (argümansız) çalıştırmaktır. Gördüğü şey ona hangi komutu ne zaman koşturacağını söylemiyor, README'ye dönmek zorunda kalıyor. `--project` bilinmediği için `--list-mcp` çıktısı eksik geliyor ve kullanıcı bunu 'sunucum yok' diye yorumluyor.

**Onerilen duzeltme.**

(1) `argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)` ve her bayrağa tek cümlelik `help=` ekle. (2) `--list-mcp` çıktısının başına `print(f"scanning: {project}/.mcp.json and ~/.claude.json")` koy, ve README'nin MCP bölümünde `--project` bayrağını göster. (3) `rc = max(rc, cmd_x(...))` kullan.


## dispatch.py — `-c key=value` gecidi

**Uygulanabilir:** True

**Config anahtari:** `sandbox_workspace_write.network_access = true  (TOML dotted path; `-c sandbox_workspace_write.network_access=true`. Sadece `sandbox = "workspace-write"` ile anlamli; `read-only` modunda karsiligi yok, sessizce yok sayilir. Ayni tablonun diger alanlari: writable_roots, exclude_tmpdir_env_var, exclude_slash_tmp.)`

**Nasil geciyor.** SPAWN ARGV'SINDE `-c` ILE. Kanit zinciri:

1) `codex app-server --help` (bizzat kosturuldu) global secenek olarak `-c, --config <key=value>` gosteriyor: "Override a configuration value that would otherwise be loaded from `~/.codex/config.toml`. Use a dotted path (`foo.bar.baz`) to override nested values. The `value` portion is parsed as TOML." Yani `-c` app-server alt komutunda da gecerli, dispatch.py zaten `model_reasoning_effort` icin kullaniyor (satir 76-77).

2) Bu bayragin urettigi katmanin adi protokolde belgeli. `codex app-server generate-ts --out ...` -> ConfigLayerSource.ts / binary string tablosu: `sessionFlags` = "Session-layer overrides supplied via `-c`/`--config`." Yani -c, user config.toml uzerine binen ayri bir katman; CODEX_HOME/config.toml'i degistirmeden per-dispatch override yapiyor.

3) Anahtarin gercek oldugunun kaniti - `ConfigToml` serde alan listesi (binary .rodata, `strings` ile):
   "...allow_login_shell / sandbox_mode / sandbox_workspace_write / default_permissions / notify..."
   ve hemen yanindaki `SandboxWorkspaceWrite` struct alan listesi:
   "...struct ToolsToml with 2 elements / state / writable_roots / network_access / exclude_tmpdir_env_var / exclude_slash_tmp..."
   Iki string bitisik duruyor -> tablo adi `sandbox_workspace_write`, icindeki alan `network_access`.

4) Codex'in KENDI gomulu dokumani (imagegen skill referansi, binary icinde):
   "- `--ask-for-approval never` suppresses approval prompts.
    - It does **not** by itself enable network access.
    - In `workspace-write`, network access still depends on your Codex configuration (for example `[sandbox_workspace_write] network_access = true`).
    ```toml
    approval_policy = "on-request"
    sandbox_mode = "workspace-write"
    [sandbox_workspace_write]
    network_access = true
    ```"
   Bu, anahtarin tam yazimini ve semantigini birinci elden dogruluyor.

5) Yerel dosya kaniti: ~/.codex-worker/config.toml zaten
   `[sandbox_workspace_write]\nnetwork_access = false` iceriyor (doctor.py BASE_CONFIG satir 41-42 bunu yaziyor). Yani anahtar dogru, sadece per-task acma gecidi yok.

SANDBOXPOLICY NESNESI ICINDE DEGIL. `thread/start` semasindan (codex app-server generate-json-schema -> codex_app_server_protocol.v2.schemas.json, ThreadStartParams):
   "sandbox": { "$ref": "#/definitions/SandboxMode" }, SandboxMode = enum ["read-only","workspace-write","danger-full-access"]
Duz string. Ic alan yok, ag icin yer yok. `sandbox` sadece varyanti seciyor; `sandbox_workspace_write` tablosu o varyantin alanlarini dolduruyor.

THREAD/START "config" PARAMS'I ALTERNATIF AMA ZAYIF KANITLI. Semada:
   "config": { "type": ["object","null"], "additionalProperties": true }
TS bindinginde: `config?: { [key in string]?: JsonValue } | null` - hic doc comment yok, tipsiz harita. Binary'de eski protokolden kalma doc string var: "Individual config settings that will override what is in CODEX_HOME/config.toml." dispatch.py bunu zaten `mcp_servers` icin kullaniyor ve calisiyor (satir 226-228), yani config.toml sekilli bir overlay oldugu kesin. AMA sandbox policy'nin bu overlay uygulandiktan SONRA mi turetildigi semadan/stringlerden dogrulanamadi. Bu yuzden oneri: argv `-c`. Zaten dosyada calistigi kanitlanmis yol o.

BONUS - ASIL HATANIN SEBEBI MUHTEMELEN AG DEGIL, UNIX SOCKET. Seatbelt politikasi binary'de duz metin duruyor. Ag acikken eklenen parca:
   "(allow network-outbound)\n(allow network-inbound)\n; allow unix domain sockets for local IPC\n; when network access is enabled, these policies are added after those in seatbelt_base_policy.sbpl"
ve ayri bir parca:
   "(allow system-socket (socket-domain AF_UNIX))\n(allow network-bind (local unix-socket))\n(allow network-outbound (remote unix-socket))"
Base policy `(deny default)` ile basliyor. "failed to initialize in-process app-server client: Operation not permitted" mesaji (binary'de var: `failed to initialize in-process app-server client: `, kaynak app-server/src/in_process.rs) TCP degil, yerel IPC/soket kurulumunda patliyor. Yani network_access=true yalnizca interneti degil, ic ice `codex exec`in ihtiyac duydugu AF_UNIX IPC'sini de aciyor. Ikinci bir olasilik da var ve gecit ikisini de cozer: ic ice codex, CODEX_HOME altina (sqlite + ipc/) yazmak zorunda ve ~/.codex-worker workspace-write'in writable root'u degil -> ayni EPERM. Genel `-c` gecidi bunu da `-c 'sandbox_workspace_write.writable_roots=["/Users/<u>/.codex-worker"]'` ile cozebilir. Tek basina bir `--network` boolean'i cozemezdi. Genel gecidin tek-amaclı bayraktan ustun olmasinin somut gerekcesi budur.

**Nerede degisecek.** Dosya: /Users/burakemreerdemci/Documents/codex-delegate/skills/codex-delegate/scripts/dispatch.py

Dort nokta, hepsi kucuk:

1. Satir 73-77 — `AppServer.__init__` imzasi ve spawn listesi:
   `def __init__(self, codex_home: Path, cwd: Path, log, effort: str | None):`
   `spawn = ["codex", "app-server"]`
   `if effort: spawn += ["-c", f"model_reasoning_effort={effort}"]`
   -> imzaya `config_overrides: list[str]` eklenir, effort'tan SONRA append edilir (son yazan kazanir; kullanici acikca `-c model_reasoning_effort=...` verirse --effort'u ezebilsin).

2. Satir 179-181 — argparse, `--sandbox` ile `--timeout` arasi. Yeni: `-c/--config` (action="append") ve `--network`.

3. Satir 195-211 civari (arg dogrulama blogu, `--mcp` kontrolunun yanina) — override'lari parse et, deny-list uygula, `--network` + `--sandbox read-only` catismasini reddet.

4. Satir 219-221 — log banner'i ve constructor cagrisi:
   `log.write(f"... mcp={granted or 'none'} sandbox={args.sandbox} =====\n")`
   `server = AppServer(args.codex_home, args.repo, log, args.effort)`
   -> banner'a `network=` ve `config=` eklenir (RAW_OUTPUT.log adli tek forensik kaydin, hangi izinle kosuldugunu icermesi sart), constructor'a liste gecirilir.

Not: thread/start params blogu (satir 226-234) DEGISMEZ. `sandbox` alani string enum olarak kalir; ag ayari argv katmanindan gelir.

**Patch taslagi.**

```
Kod degil, taslak. Turkce yorum + Ingilizce identifier (proje konvansiyonu).

--- 1) Modul seviyesi: deny-list ---

# Bu anahtarlar izolasyon modelinin kendisini soker; -c ile gecilemez.
# sandbox_mode / default_permissions / permission_profile: kum havuzunu tamamen
#   kaldirabilir (binary'de "`sandbox_mode` and `default_permissions` overrides
#   cannot both be set" hatasi bu ucunun ayni seyi kontrol ettigini gosteriyor).
# approval_policy: dispatch onaylari kendisi cevapliyor, sessizce degisemez.
# mcp_servers: MCP devri doctor.py --add-mcp + --mcp uzerinden yurur.
# shell_environment_policy: kimlik bilgisi eleme filtresini devre disi birakir.
CONFIG_DENY_PREFIXES = (
    "sandbox_mode", "default_permissions", "permission_profile",
    "approval_policy", "approvals_reviewer",
    "mcp_servers", "shell_environment_policy",
)
NETWORK_KEY = "sandbox_workspace_write.network_access"


def parse_config_overrides(items: list[str]) -> list[str]:
    """KEY=VALUE dogrula; deny-list'i uygula. Deger TOML olarak parse edilecek."""
    out = []
    for raw in items:
        if "=" not in raw:
            raise DispatchError(f"--config expects KEY=VALUE, got {raw!r}")
        key, _, value = raw.partition("=")     # ilk = ; deger icinde = olabilir
        key = key.strip()
        if not key or not all(c.isalnum() or c in "._-" for c in key):
            raise DispatchError(f"--config key is not a dotted TOML path: {key!r}")
        head = key.split(".", 1)[0]
        if head in CONFIG_DENY_PREFIXES:
            raise DispatchError(
                f"--config {key} is refused: it changes the isolation boundary "
                f"itself. Change ~/.codex-worker/config.toml deliberately instead."
            )
        out.append(f"{key}={value}")
    return out


--- 2) argparse (satir 181'den once) ---

ap.add_argument("-c", "--config", action="append", default=[], metavar="KEY=VALUE",
                help="codex config override, dotted TOML path (repeatable). "
                     "Value is parsed as TOML: true/false are booleans, "
                     "strings need quotes.")
ap.add_argument("--network", action="store_true",
                help="shorthand for -c sandbox_workspace_write.network_access=true. "
                     "Only when the SPEC's NETWORK field says allowed.")


--- 3) main(), --mcp dogrulamasinin hemen ardindan (satir ~211) ---

try:
    overrides = parse_config_overrides(args.config)
except DispatchError as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    return 5

if args.network:
    # read-only kum havuzunda sandbox_workspace_write tablosu hic okunmuyor;
    # bayragi sessizce yutmak "actim sandim" tuzagi yaratir (bkz. CLAUDE.md §11.3).
    if args.sandbox != "workspace-write":
        print("ERROR: --network only applies to --sandbox workspace-write.",
              file=sys.stderr)
        return 5
    if any(o.startswith(NETWORK_KEY + "=") for o in overrides):
        print("ERROR: --network and an explicit -c "
              f"{NETWORK_KEY} were both given.", file=sys.stderr)
        return 5
    overrides.append(f"{NETWORK_KEY}=true")

network_on = any(
    o.startswith(NETWORK_KEY + "=") and o.split("=", 1)[1].strip() == "true"
    for o in overrides
)


--- 4) Banner + constructor (satir 219-221) ---

log.write(f"\n===== dispatch {time.strftime('%Y-%m-%d %H:%M:%S')} "
          f"mcp={granted or 'none'} sandbox={args.sandbox} "
          f"network={'ON' if network_on else 'off'} "
          f"config={overrides or 'none'} =====\n")
server = AppServer(args.codex_home, args.repo, log, args.effort, overrides)

# Ag acilmissa stdout'ta da gorunsun: mimar bunu FINAL.txt'yi okumadan once gorur.
if network_on:
    print("NOTE: worker ran with network access ENABLED.", file=sys.stderr)


--- 5) AppServer.__init__ (satir 74-77) ---

def __init__(self, codex_home: Path, cwd: Path, log,
             effort: str | None, config_overrides: list[str] | None = None):
    spawn = ["codex", "app-server"]
    if effort:
        spawn += ["-c", f"model_reasoning_effort={effort}"]
    # Kullanici override'lari effort'tan SONRA: acik istek varsayilani ezsin.
    for item in (config_overrides or []):
        spawn += ["-c", item]


--- Kullanim ---

# ag gerektiren gorev (SPEC NETWORK: allowed)
dispatch.py --repo "$PWD" ... --network

# ic ice codex exec kosturmasi gereken gorev: ag + worker home yazilabilir
dispatch.py --repo "$PWD" ... --network \
  -c 'sandbox_workspace_write.writable_roots=["'"$HOME"'/.codex-worker"]'


--- --network kisayolu mantikli mi? ---

Evet, ama tek basina yeterli degil, bu yuzden ikisi birlikte olmali:
- --network okunabilirlik + tek yerde denetlenebilir sinyal saglar (banner, stderr
  notu, ileride SPEC'in NETWORK alaniyla otomatik eslestirme).
- Genel -c olmadan yasanan tikanma tekrar eder: ic ice codex vakasinda ihtiyac
  duyulan ikinci ayar writable_roots idi, ag degil. Tek amacli bayrak ekleyerek
  ilerlemek her yeni ihtiyacta yeni bayrak demek.
- Sadece -c birakip --network'u atlamak da kotu: cikplak
  `-c sandbox_workspace_write.network_access=true` yazimi kolayca yanlis yazilir
  ve YANLIS ANAHTAR SESSIZCE YOK SAYILIR (codex --strict-config vermedikce).
  --network o riski tek dogru yazima kilitler.

Ek oneri: spawn'a `--strict-config` eklemek dusunulmeli. `codex app-server --help`:
"Error out when config.toml contains fields that are not recognized by this
version of Codex". Bu, yanlis yazilmis bir -c anahtarinin sessizce yutulmasini
hataya cevirir - CLAUDE.md §13 "bayrak dogrulama" ve §11.3 "config'e guvenme"
ilkelerinin tam karsiligi. Ancak mevcut config.toml'daki tanimsiz alanlari da
patlatabilir, once bosta denenmeli.
```

**Riskler.**

- Ag erisimi MCP onayiyla AYNI kategoriye girer, hatta daha genistir. SKILL.md 'outward-facing MCP sunuculari kullanicinin acik per-task onayini ister' diyor; ama isimlendirilmis bir MCP sunucusu tanimli bir yuzey iken network_access=true isciye ACIK BIR SHELL uzerinden sinirsiz giden trafik verir. spec-template.md'de NETWORK alani zaten var ('Default not-allowed... This is a per-task decision, never a standing one') - yani kural yazili, sadece uygulanmiyor. --network bayragi bu alanin karsiligi olarak konumlandirilmali ve SKILL.md §6'ya 'NETWORK: allowed ise ve kullanici sohbette onayladiysa --network ekle' cumlesi eklenmeli. Varsayilan kapali kalmali (dispatch.py hicbir sey yapmazsa config.toml'daki network_access=false gecerli).
- Sizinti yuzeyi: dispatch.py satir 81-83 env temizligi (`_API_KEY`, `_TOKEN`, `_SECRET`, `GITHUB_`, `GH_`) ag KAPALIYKEN bile eksik, ag ACIKKEN kritik hale gelir. AWS_SECRET_ACCESS_KEY ('_KEY' ile biter, uc kalibin hicbirine uymaz) ve ozellikle SSH_AUTH_SOCK hayatta kaliyor. SSH_AUTH_SOCK, GITHUB_TOKEN'i atmanin amacini tamamen bosa cikarir - isci ssh-agent uzerinden push edebilir. Ag acilmadan once bu filtre allow-list'e cevrilmeli (inherit=core + acik beyaz liste), veya en azindan SSH_AUTH_SOCK, AWS_*, GOOGLE_APPLICATION_CREDENTIALS eklenmeli.
- Kanit toplama tarafinda ag, iscinin raporunu dogrulanamaz kilar. Ag kapaliyken iscinin her iddiasinin izi diskte kalir (SKILL.md §7 footprint kontrolu). Ag acikken isci bir seyi indirip kullanabilir, bir yere gonderebilir ve bu RAW_OUTPUT.log disinda hicbir yerde gorunmez - ustelik log satir siniri (satir 154: [:4000]) uzun ciktilari kesiyor. Ag acik kosulan dispatch'lerde log truncation'i kaldirmak veya siniri buyutmek gerekir.
- Deny-list olmadan genel -c gecidi kum havuzunun kendisini kapatabilir: `-c sandbox_mode=danger-full-access`, `-c default_permissions=...`, `-c approval_policy=never`, `-c mcp_servers.x.command=...` (kayitli olmayan MCP'yi --mcp dogrulamasini atlayarak enjekte etmek), `-c shell_environment_policy.inherit=all` (kimlik bilgisi filtresini iptal). Taslakktaki CONFIG_DENY_PREFIXES sart; -c'yi filtresiz eklemek dispatch.py'ye --dangerously-bypass'in esdegerini gizlice koymak olur.
- Ic ice `codex exec` calistirmak icin ag acmak, tek bir dispatch icinde ikinci bir denetlenmemis ajan dogurmak demektir - bu ic ajan kendi CODEX_HOME'unu, kendi sandbox'ini ve kendi MCP izinlerini kullanir; dispatch.py'nin approve()/decline() mantigi ona ULASMAZ (satir 137-142 sadece dis app-server'in isteklerini goruyor). Yani ic ice codex, izolasyon modelinde bir kor nokta. Ag gecidi bunu mumkun kilarken skill bunu acikca 'ic ice ajan dogurmak yasak, ancak arastirma gorevlerinde ve acik onayla' diye yazmali; yoksa gecit ilk kullanimda kural haline gelir.
- Kalicilik yanilgisi: -c overrides yalnizca o app-server process'i icin gecerli (sessionFlags katmani), config.toml'a yazilmaz - bu iyi. Ama ~/.codex-worker/config.toml'i elle duzenleyip network_access=true birakmak AYNI etkiyi KALICI yapar ve kimse fark etmez. doctor.py'ye 'worker home'da network_access true ise FAIL/WARN ver' kontrolu eklenmeli; aksi halde bir kez elle acilan ag sonsuza kadar acik kalir. (doctor.py BASE_CONFIG satir 41-42 dosyayi yazarken false koyuyor ama sonradan degistirilmis olup olmadigini denetlemiyor.)
- Yanlis anahtar sessizce yok sayilir: `-c sandbox_workspace_write.network-access=true` (tire) veya `-c network_access=true` (tablosuz) hata vermez, sadece etkisiz kalir ve isci yine EPERM alir - bu sefer 'ama ben actim' yanilgisiyla. --network kisayolu + --strict-config denemesi bu riskin karsiligidir.

**dispatch.py'de gorulen diger sorunlar.**

- ONEMLI - --timeout hicbir zaman tetiklenmeyebilir. Satir 244-251: timeout kontrolu (245) BLOKLAYAN read()'ten (247) once yapiliyor; AppServer.read() satir 112 duz `self.proc.stdout.readline()`, hicbir zaman asimi yok. Server tek satir bile uretmeden asilirsa (ag beklerken, onay beklerken, model takilinca) readline() sonsuza kadar bloklar ve --timeout 3600 hic calismaz. Ayrica request() (satir 98-108) icindeki dongude timeout kontrolu HIC yok - initialize/thread/start/turn/start asamasinda takilan bir server dispatch'i kalici olarak asar. Cozum: stdout'u ayri bir thread + queue.get(timeout=...) ile okumak, ya da selectors ile fd'yi deadline'a kadar beklemek.
- ONEMLI - turn/completed yarisi. turn/completed sadece ana dongude (satir 248) taniniyor. Eger turn cok hizli biterse veya hata verirse, turn/completed bildirimi `server.request("turn/start", ...)` cagrisinin yanit bekleme dongusu (satir 102-108) icinde gelebilir; orada handle() -> note() yalnizca loglar, dongu kirilmaz. Sonra ana dongu asla gelmeyecek ikinci bir turn/completed bekler. Onceki maddeyle birlesince: sonsuz asilma. Cozum: AppServer'a `self.turn_done = False` bayragi koyup note() icinde set etmek, ana donguyu bayrakla kirmak.
- ONEMLI - HATA DURUMUNDA FINAL.txt YAZILMIYOR, ESKI DOSYA KALIYOR. Satir 252-255 `return 1` yaptiginda satir 259'daki `final_path.write_text(...)` atlanir. RAW_OUTPUT.log append modunda ('a', satir 218) ama FINAL.txt sadece basarida uzerine yaziliyor. Sonuc: ayni task-dir'de ikinci bir dispatch patlarsa, onceki KOSUNUN BASARILI RAPORU FINAL.txt'de durur ve SKILL.md §6 'Read FINAL.txt' der. Mimar bir onceki gorevin raporunu yeni gorevin sonucu sanabilir. En az zararli duzeltme: main() basinda FINAL.txt'yi silmek/truncate etmek, hata yolunda da FINAL.txt'ye '(dispatch failed: ...)' yazmak.
- stderr=subprocess.DEVNULL (satir 86) tum teshis bilgisini yok ediyor. app-server bir hata ile cikarsa read() satir 114 sadece 'codex app-server exited unexpectedly' der; sebebi (kayip binary, bozuk config, auth hatasi, panic) DEVNULL'a gitmistir. Bu, tam da yasanan sandbox/EPERM vakasinda tanı koymayi zorlastiran sey. stderr, RAW_OUTPUT.log'un fd'sine yonlendirilmeli (stderr=log) veya ayri bir STDERR.log'a alinmali - stdout'a KARISTIRILMAMALI (JSONL protokolu bozulur).
- AppServer kurulamazsa AttributeError. Satir 216 `server: AppServer | None = None`, satir 259 kosulsuz `server.final_text`. AppServer.__init__ icindeki Popen (satir 84) FileNotFoundError (codex PATH'te yok) veya NotADirectoryError (--repo yanlis) atarsa: exception `except DispatchError` tarafindan YAKALANMAZ, `finally: server.close()` satir 257 None uzerinde AttributeError atar, orijinal hata maskelenir ve kullanici alakasiz bir traceback gorur. `finally` blogu `if server: server.close()` olmali; --repo ve --prompt-file varlik kontrolu argparse'tan hemen sonra yapilmali.
- send() korumasiz: satir 94-96 BrokenPipeError'a karsi hicbir sey yok. Server olduyse (ornegin thread/start reddi sonrasi cikis) approve() icindeki send() BrokenPipe atar; bu DispatchError degil, satir 252'deki except'e takilmaz, FINAL.txt yazilmaz, cirkin traceback ile cikar.
- Zombi/oksuz process. close() (satir 161-166) sadece terminate() ediyor; app-server'in kum havuzunda BASLATTIGI cocuk process'ler (13 dakikalik test kosusu, npm, vs.) ayri process group'ta degil ve olmuyor. --timeout 3600 sonunda dispatch cikar, testler makinede kosmaya devam eder. Cozum: Popen(..., start_new_session=True) + close() icinde os.killpg(os.getpgid(pid), SIGTERM) -> bekle -> SIGKILL. Ayrica kill() sonrasi wait() cagrilmiyor (satir 165-166) -> zombi; stdin/stdout pipe'lari da hic kapatilmiyor. Nazik kapanis icin once stdin'i kapatmak (app-server EOF gorup temiz cikar), sonra terminate.
- Log satir limitleri kanit yok ediyor. Satir 154 item icin [:4000], satir 158 diger metodlar icin [:1000], satir 141 REDDEDILEN komut icin [:400], satir 156 turn/failed icin [:800]. RAW_OUTPUT.log tek forensik kayit ('The transcript exists for forensics' - SKILL.md §6). Uzun bir diff, uzun bir test ciktisi veya uzun bir komut sessizce kesiliyor ve kesildigine dair isaret bile yok. En azindan kesildiginde '...[+N bytes truncated]' eklenmeli; reddedilen komutlarin (izolasyon ihlali kaniti!) 400 karakterle kesilmesi ozellikle kotu.
- RAW_OUTPUT.log rotasyon/boyut siniri yok (satir 218, mode='a'). Ayni task-dir'e tekrar tekrar dispatch edilirse dosya sinirsiz buyur. Bu proje CLAUDE.md §11.2'de tam olarak bu tuzagi (972MB log) isaretlemis.
- Bilinmeyen server->client istegine sessiz bos onay. Satir 145-147: `else: self.send({"id": rid, "result": {}})` - hicbir log yazmadan. Yorumda 'Any unanswered server request hangs the session forever' deniyor, dogru; ama gelecek bir codex surumu yeni bir onay tipi eklerse (ornegin yeni bir izin akisi) bu dal onu SESSIZCE bos-onaylar ve hicbir iz birakmaz. En azindan `self.log.write(f"[unknown-request] {method} ...")` eklenmeli; ideali JSON-RPC error donmek.
- Surum kapisi kendi mesajiyla celisiyor. Satir 184-193 kontrol KOSULSUZ (`if version < PERMISSION_SCHEMA_MIN`), ama hata metni 'Upgrade codex, or dispatch without --mcp' diyor. --mcp verilmemisse bile 3 ile cikiyor; onerilen kacis yolu calismiyor. `if args.mcp and version < ...` olmali.
- Surum parse'i pre-release'de kiriliyor. Satir 49-52: '0.145.0-alpha.1' -> parts[:3] = ['0','145','0-alpha'], isdigit() False -> hicbir token eslesmez -> DispatchError. Ayrica codex_version() (satir 46-48) returncode'u kontrol etmiyor ve FileNotFoundError/TimeoutExpired'i yakalamiyor - codex kurulu degilse temiz mesaj yerine traceback.
- configured_mcp_names sadece TEK katmani okuyor. Satir 64-65 yalnizca `codex_home/config.toml` icindeki [mcp_servers]'i goruyor. Codex 0.145 katmanli config kullaniyor (ConfigLayerSource: user / profile-v2 dosyasi `$CODEX_HOME/<name>.config.toml` / project `.codex/` klasoru / managed). Profil dosyasinda veya proje katmaninda tanimli bir MCP sunucusu 'kayitli degil' diye reddedilir (satir 204-210, exit 4) - yanlis negatif. Daha saglami: `codex mcp list` veya app-server'in `mcpServerStatus/list` metoduyla sormak.
- --effort varsayilani 'high' kosulsuz enjekte ediliyor (satir 178 + 76-77). Bu, ~/.codex-worker/config.toml'daki model_reasoning_effort'u her zaman eziyor ve secilen modelin 'high' efor destekledigi varsayiliyor (model kataloglarinda supportedReasoningEfforts alani var, her model her eforu desteklemiyor). En azindan default=None olup config.toml'a birakilmali.
- approvalPolicy='on-request' (satir 231) + tum commandExecution/fileChange isteklerinin otomatik REDDI (satir 137-142) tasarim geregi dogru, ama isci ayni komutu tekrar tekrar deneyip turu bos yere tuketebilir; RAW_OUTPUT.log'da [decline] birikir ama isciye 'bu bir politika, tekrar deneme' sinyali gitmiyor. requestUserInput cevabi (satir 143-144) 'Proceed using your best judgment' diyor - reddedilen bir eylemin ardindan bu mesaj isciyi ayni duvara tekrar surer.
- final_text yalnizca item/completed + type=='agentMessage' olaylarindan toplaniyor (satir 150-153) ve her seferinde UZERINE yaziliyor. Isci raporu yazip ardindan kisa bir kapanis mesaji ('Done.') uretirse FINAL.txt'ye kisa olan gecer, asil rapor RAW_OUTPUT.log icinde 4000 karaktere kesilmis halde kalir. En azindan tum agentMessage'lari biriktirip sonuncusunu degil hepsini (veya en uzununu) yazma secenegi dusunulmeli.
