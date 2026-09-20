# Noggora Pipeline

CLI pipeline chạy local: nhận vào **1 chủ đề** (topic string), xuất ra **1 file
video dọc .mp4 (1080x1920)** sẵn sàng đăng TikTok/YouTube Shorts/IG Reels/FB
Reels — script → giọng đọc AI → phụ đề burn-in → hình nền/B-roll → nhạc nền →
ghép video.

Kênh: **Noggora** — niche tâm lý học/hành vi con người, video 30–45s, faceless,
hook 3 giây đầu. Pipeline hỗ trợ cả tiếng Anh và tiếng Việt (đổi `--lang` +
voice trong config), nhưng hiện tại kênh **chỉ đăng tiếng Anh** — vì vậy
`data/topic_bank.csv` (nguồn của `auto`) chỉ chứa chủ đề tiếng Anh; muốn làm
video tiếng Việt vẫn dùng được `single --topic "..." --lang vi`, chỉ là chưa
có sẵn chủ đề nào trong bank.

Xem [PLAN-automation-noggora.md](PLAN-automation-noggora.md) cho spec kỹ thuật
đầy đủ đã dùng để build pipeline này.

---

## 1. Cài đặt

### 1.1 Python + dependencies

Cần **Python 3.11+**.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 1.2 ffmpeg (bắt buộc)

Pipeline gọi `ffmpeg`/`ffprobe` qua subprocess — không có thư viện Python nào
thay thế được bước ghép video. `main.py` sẽ báo lỗi rõ ràng ngay khi khởi động
nếu không tìm thấy 2 tool này trên `PATH`.

- **Windows:** `winget install --id Gyan.FFmpeg -e` (hoặc `choco install ffmpeg`) — sau khi cài, **mở terminal mới** để `PATH` được nạp lại.
- **macOS:** `brew install ffmpeg`
- **Linux:** `sudo apt install ffmpeg` (Debian/Ubuntu) hoặc `sudo dnf install ffmpeg` (Fedora)

Kiểm tra: `ffmpeg -version` và `ffprobe -version` phải chạy được.

### 1.3 API keys (`.env`)

```bash
cp .env.example .env
```

Điền vào `.env`:

| Key | Bắt buộc? | Lấy ở đâu |
|---|---|---|
| `ANTHROPIC_API_KEY` | Không — viết script mặc định qua lệnh `claude` (Claude Code CLI, tài khoản đã đăng nhập, mục 2). Chỉ cần key nếu đặt `script.provider: anthropic_api` (tính tiền riêng, **không** dùng chung với gói Claude Code) | [console.anthropic.com](https://console.anthropic.com/) |
| `PEXELS_API_KEY` | Không — thiếu thì dùng riêng `PIXABAY_API_KEY` (nếu có), hoặc ảnh/video dự phòng trong `data/assets_local/` | Free tại [pexels.com/api](https://www.pexels.com/api/) (đăng ký tài khoản → tạo API key, có quyền thương mại) |
| `PIXABAY_API_KEY` | Không — có cả 2 key thì mỗi video chia đều clip từ Pexels + Pixabay (đa dạng hơn), không bắt buộc phải có key này | Free tại [pixabay.com/api/docs](https://pixabay.com/api/docs/) (đăng ký tài khoản → lấy API key, license cho phép dùng thương mại, không cần credit) |
| `ELEVENLABS_API_KEY` | Cần khi `voice.provider: elevenlabs` (đang bật); thiếu/lỗi thì đặt `edge_tts` để quay về giọng miễn phí | [elevenlabs.io](https://elevenlabs.io/) |

**Vì sao không lấy ảnh từ Pinterest:** Pinterest không có API để tải ảnh/video của người khác về dùng lại (API của họ chỉ để *đăng* pin lên tài khoản, không phải để *tìm & tải*), và phần lớn nội dung trên đó là người dùng ghim lại từ nơi khác, không rõ bản quyền gốc — dùng để đăng lên kênh kiếm tiền có rủi ro copyright strike thật. Pexels/Pixabay được chọn vì cả hai đều cấp license miễn phí, rõ ràng, dùng thương mại không cần credit.

Không key nào là bắt buộc để chạy thử pipeline lần đầu — mọi bước đều có
fallback (xem mục 5).

### 1.4 Chuyển / cài lại trên một máy Windows khác

Lặp lại toàn bộ mục 1.1–1.3 trên máy mới (venv không copy được giữa các máy —
phải `python -m venv .venv` lại từ đầu). Ngoài ra, **3 thứ sau đây bị
`.gitignore` nên `git clone` (hoặc copy code qua Git) sẽ KHÔNG mang theo** —
phải tự chép tay từ máy cũ sang (USB/OneDrive/zip...), nếu không `auto` sẽ
chạy được nhưng thiếu dữ liệu/tiến độ:

| Thư mục/file | Vì sao quan trọng nếu thiếu |
|---|---|
| `data/` (cả thư mục) | Chứa `topic_bank.csv` (chủ đề chưa dùng; lô đầu là 25 chủ đề viết tay), `topic_bank_used.csv` (chủ đề đã đăng), `effects_pool.csv`, `topics.csv`, `topic_bank_meta.json` và `assets_local/` (video nền placeholder). Thiếu thư mục này, `auto` không có chủ đề nào để chọn và sẽ lỗi ngay từ lần chạy đầu trên máy mới |
| `.env` | Chứa toàn bộ API key — không copy thì `auto`/`single` vẫn chạy được (nhờ fallback ở mục 5) nhưng chất lượng thấp hơn (manual script, không B-roll thật) |

Nếu **không** copy `data/topic_bank_meta.json` + `topic_bank_used.csv`
(danh sách chủ đề đã dùng), máy mới sẽ coi như chưa video nào được đăng và có thể chọn
lại đúng những chủ đề máy cũ đã dùng — không sai kỹ thuật, chỉ là dễ trùng nội
dung giữa 2 máy nếu cả hai cùng chạy `auto`.

`output/` và `logs/` không cần copy — pipeline tự tạo lại, chỉ mất lịch sử
video/log cũ (không ảnh hưởng chức năng).

Máy mới cũng cần **cài Claude Code CLI và đăng nhập** (chạy `claude` một lần) thì
`auto` mới nhờ Claude viết script được; chưa có thì `auto` vẫn chạy bằng bank viết sẵn.

Nếu muốn máy mới **tự chạy mỗi ngày** (mục 2.2), Task Scheduler là cấu hình
riêng theo từng máy — phải chạy lại
`powershell -ExecutionPolicy Bypass -File setup_scheduled_task.ps1` trên máy
mới, không có cách "copy" scheduled task từ máy cũ sang.

---

## 2. Chế độ `auto` — Claude viết video mới cho hôm nay

```bash
python main.py auto
```

Bạn tự chạy lệnh này khi muốn có video (không cần đặt lịch, không cần nhập
topic). Mỗi lần chạy:

1. **Claude viết chủ đề + script + mô tả bài đăng + từ khoá tìm hình mới** cho
   video hôm nay (`src/daily_topic.py`) — không sinh cả lô trước. Dùng lệnh
   `claude -p` (Claude Code CLI) bằng **tài khoản Claude Code bạn đang đăng
   nhập**, không cần `ANTHROPIC_API_KEY`; tốn hạn mức của gói, mỗi lần khoảng
   20–30 giây.
2. **Lưu lại ngay và kiểm tra trùng** (mục 2.1) rồi chạy hết pipeline ra `final.mp4`.
3. **In sẵn tiêu đề + mô tả (caption kèm hashtag) để copy vào bài đăng** ở cuối
   lệnh, đồng thời lưu ở `output/<job>/post.txt` (`title.txt` và
   `description.txt` là 2 phần riêng). `single`/`batch` cũng tạo mô tả cho topic
   bạn đưa vào.
4. Nếu Claude không viết được (chưa đăng nhập, hết hạn mức, lỗi mạng, hoặc 3 lần
   liền đều ra bản trùng) → **dừng và báo lỗi rõ**, không lặng lẽ lấy chủ đề từ
   file. Muốn tự dùng chủ đề viết sẵn trong [data/topic_bank.csv](data/topic_bank.csv)
   khi Claude lỗi thì đặt `script.fallback_to_bank: true`; hoặc chủ động chạy
   `python main.py auto --bank-only` bất cứ lúc nào.

Nội dung do AI viết — nên đọc lướt script + mô tả trước khi đăng (một số hiệu ứng
tâm lý còn tranh luận về cơ chế, Claude có thể kể theo 1 giả thuyết như thể đã chắc chắn).

Cách chọn nguồn viết script đổi ở `script.provider` trong `config/settings.yaml`:
`claude_cli` (mặc định) | `anthropic_api` (cần `ANTHROPIC_API_KEY`, tính tiền
theo token) | `auto` (API nếu có key, không thì CLI) | `manual`. `single` cũng
dùng cùng nguồn này để viết script cho topic bạn đưa vào.

Lưu ý: khi chạy bằng CLI, biến `ANTHROPIC_API_KEY` (nếu có trong `.env`) bị bỏ
qua có chủ ý, để CLI không tính tiền API thay vì dùng gói của bạn.

- `--lang vi` không dùng AI (chỉ tiếng Anh) — muốn video tiếng Việt dùng
  `single --topic "..." --lang vi` (mục 3).
- Thêm chủ đề viết tay vào bank: mở `data/topic_bank.csv`, thêm dòng với cột
  `used_at` để trống (id chỉ cần là số chưa dùng).

### 2.1 Không lặp lại, kể cả nội dung gần giống

Mọi video AI viết đều được **lưu ngay lúc sinh ra** (không chờ render xong, để
video lỗi giữa chừng vẫn không bị sinh lại vào hôm sau) vào
`data/topic_bank_used.csv`: chủ đề, script, từ khoá hình, tên hiệu ứng tâm lý
(cột `effect`) và giờ `used_at`. Script cũng nằm trong `output/<job>/script.txt`
— render lại cùng script bằng `python main.py single --resume "output/<job>"`.

Trước khi nhận một bản Claude viết, `daily_topic.find_duplicate` so với **toàn bộ
video đã làm và cả bank viết tay**, và từ chối nếu:

- trùng chủ đề (không phân biệt hoa/thường, dấu câu);
- **cùng hiệu ứng tâm lý** — so tên hiệu ứng (kể cả viết khác đi như "sunk cost"
  và "sunk cost fallacy"), và cả khi hiệu ứng đó đã được nêu tên trong script cũ
  của bank viết tay;
- tiêu đề chỉ là viết lại tiêu đề cũ (tỉ lệ giống chuỗi ≥ 0.72 hoặc ≥ 2/3 từ khoá trùng);
- nội dung script trùng ≥ 32% từ khoá với script cũ (video khác nhau thật sự chỉ
  chạm khoảng 18% khi đo trên dữ liệu hiện có).

Khi bị từ chối, Claude được báo đúng lý do ("effect X đã dùng ở video Y") và
được yêu cầu chọn hiệu ứng + tình huống khác; thử tối đa 3 lần, hết lượt thì
quay về bank. Ngoài ra danh sách chủ đề/hiệu ứng cũ cũng được đưa vào prompt
ngay từ đầu, và mỗi ngày prompt gợi ý 1 mảng tâm lý khác nhau (xoay vòng 10 mảng)
nên các video liên tiếp không dồn vào cùng một chủ đề.

Với **bank viết tay** (dự phòng): chủ đề đã dùng bị xoá khỏi
`data/topic_bank.csv` và chuyển sang `topic_bank_used.csv`. Khi bank cạn thì
`topic_bank.ensure_fresh_batch` bù thêm 1 lô (số chủ đề = số ngày của tháng): từ
Anthropic nếu có `ANTHROPIC_API_KEY`, không thì ghép từ
[data/effects_pool.csv](data/effects_pool.csv) (~60 hiệu ứng, văn phong khuôn
mẫu hơn). Hết cả hai thì quay vòng lại chủ đề cũ (có log cảnh báo).

### 2.2 Chạy hằng ngày (tuỳ chọn)

Hiện bạn chạy tay nên **không cần** Task Scheduler. Nếu sau này muốn tự chạy:
`powershell -ExecutionPolicy Bypass -File setup_scheduled_task.ps1 -Time "08:00"`
(tạo task "Noggora Daily Video", chạy `run_auto.ps1` → `main.py auto`, log ở
`logs/auto.log`). Lưu ý: task chạy trong môi trường Windows khác với terminal
của bạn — cần kiểm tra `claude` vẫn đăng nhập được ở đó, nếu không `auto` sẽ tự
rơi về bank.

Video mới luôn nằm trong `output/<slug>-<timestamp>/final.mp4` — hãy sort
`output/` theo thời gian sửa đổi để lấy video mới nhất.

---

## 3. Các chế độ chạy thủ công khác (khi cần chỉ định chủ đề)

```bash
# 1 video từ 1 ý tưởng do bạn tự chọn
python main.py single --topic "Why does silence after a question make people confess more?" --lang en

# Batch từ data/topics.csv (chỉ chạy các dòng status=pending)
python main.py batch --file data/topics.csv --limit 10
```

`single`/`batch` nhờ Claude viết script cho topic bạn đưa (cùng nguồn với `auto`,
xem `script.provider`). Chỉ khi không có nguồn nào dùng được (chưa cài/đăng nhập
`claude`, không có key, hoặc `provider: manual`) thì lệnh dừng ở bước script và in ra:

```
[1/1] "Why does silence after a question mak..." -> awaiting manual script: output/<job_slug>/script.txt ✋
```

→ mở file đó, xóa dòng comment, dán script của bạn vào, rồi chạy lại:

```bash
python main.py single --resume "output/<job_slug>"
```

(Không cần truyền lại `--topic`/`--lang` khi resume — pipeline đọc lại từ
`job_log.json` trong thư mục job.)

Với batch mode, chạy lại với `--resume` để pipeline tự retry **tất cả** các
dòng đang ở trạng thái `awaiting_manual` (sau khi bạn đã điền script tay vào
từng `script.txt` tương ứng — đường dẫn nằm ở cột `job_dir` trong CSV):

```bash
python main.py batch --file data/topics.csv --resume
```

---

## 4. Cấu trúc output

```
output/<slug-topic>-<timestamp>/
├── script.txt       # lời thoại (do Claude viết, lấy từ bank, hoặc bạn dán tay)
├── voice.mp3         # giọng đọc edge-tts
├── voice.srt          # phụ đề gốc: mỗi cue là 1 cụm ≤ 6 từ (ngắt theo dấu phẩy)
├── voice.words.json     # timing từng từ của mỗi cụm (để tô từ đang đọc)
├── voice.ass           # phụ đề đã style + title card mở đầu + CTA cuối (theo config/settings.yaml)
├── clips/                # B-roll đã tải (Pexels/Pixabay) hoặc copy từ data/assets_local/
├── final.mp4              # ✅ video hoàn chỉnh, sẵn sàng đăng
├── post.txt                # tiêu đề + mô tả (caption + hashtag) để copy khi đăng
├── title.txt               # riêng tiêu đề
├── description.txt         # riêng mô tả
├── cover.png               # ảnh bìa 1080x1920 (khung mở đầu + tên kênh + tiêu đề)
├── factcheck.txt           # Claude đã sửa/ghi chú gì ở bước kiểm tra sự thật
├── entry.json              # dữ liệu lần sinh (từ khoá hình, mô tả...) để `single --resume` render lại y hệt
└── job_log.json            # log từng bước + danh sách cảnh (khoảng thời gian → clip nào)
```

### 4.1 Video được dựng như thế nào

- **Cảnh theo câu:** `src/scenes.py` chia giọng đọc thành ~5 cảnh, cắt ở khoảng
  lặng giữa các câu (câu quá dài mới cắt giữa câu), độ dài các cảnh gần bằng nhau.
  Mỗi cảnh có 1 clip B-roll riêng, tìm theo nội dung cảnh đó.
- **Từ khoá tìm hình**, theo thứ tự ưu tiên: (1) cột `visual_keywords` trong
  `topic_bank.csv` (cụm từ cách nhau bằng `;`, mỗi cảnh 1 cụm, theo thứ tự
  hook → cơ chế → ví dụ → hành động); (2) vật cụ thể được nhắc trong câu (movie,
  coffee, clock... — bảng `_CONCEPT_MAP` trong `visual_fetcher.py`); (3) từ khoá
  chung về tâm lý. Cảnh lẻ ưu tiên Pexels, cảnh chẵn ưu tiên Pixabay (nguồn kia bù nếu thiếu).
- **Chọn clip theo độ khớp:** mỗi kết quả được chấm theo số từ khoá trùng với
  	ags của Pixabay / phần mô tả trong URL của Pexels; clip đủ khớp mới được lấy,
  không thì thử từ khoá tiếp theo, cuối cùng mới lấy clip khớp nhất tìm được.
  Số cảnh tự tăng với video dài (isuals.max_scene_sec).
- **Nhìn thống nhất:** cùng 1 bộ chỉnh màu (hơi tối, ngả tím) + vignette cho mọi
  clip; clip dọc pan chậm; clip ngang hiện trong khung vuông trên nền mờ; các clip
  crossfade 0.25s.
- **Hook:** chủ đề (câu hỏi) hiện to ở 2.8s đầu trên nền tối hơn; 2.5s cuối có dòng CTA.
- **Caption:** cụm ≤ 6 từ, cỡ chữ 68; cả cụm luôn hiện và **từ đang được đọc đổi màu vàng** (karaoke, tắt bằng `subtitle.karaoke: false`); tên hiệu ứng ("sunk cost fallacy"...) luôn tô xanh nhạt.
- **Âm thanh:** stereo; nhạc nền tự hạ khi có giọng đọc (sidechain ducking); có tiếng `hit` trầm khi title card hiện (`src/sfx.py`, tự tổng hợp; bỏ `hit.*` của bạn vào `data/assets_local/sfx/` để dùng thay). Tiếng `whoosh` ở chỗ chuyển clip đã **tắt** vì nghe chói tai — bật lại bằng `sfx.transitions: true`.
- **Ảnh bìa:** `cover.png` để chọn làm cover trên TikTok/Shorts/Reels (mục `cover:` trong config).
- **Kiểm tra sự thật:** sau khi viết, Claude rà soát script + caption thêm 1 lượt (`script.fact_check`): chỗ nào còn tranh cãi (ví dụ cơ chế của moon illusion) được viết lại dè dặt ("researchers still debate..."), số liệu/nghiên cứu không kiểm chứng được thì bỏ; ghi chú ở `factcheck.txt`. Thêm khoảng 20 giây mỗi lần chạy.

Mỗi lần chạy tạo 1 thư mục riêng theo slug + timestamp — không bao giờ ghi đè
job cũ, nên bạn luôn có thể debug/tái sử dụng asset của 1 job cụ thể.

`data/topics.csv` được pipeline tự thêm cột `job_dir` (ghi lại thư mục output
của mỗi dòng) để `--resume` biết chính xác job nào cần tiếp tục.

---

## 5. Cơ chế fallback của từng bước

| Bước | Không có key/mạng lỗi | Kết quả |
|---|---|---|
| Script (Claude) | `claude` CLI chưa đăng nhập / hết hạn mức / lỗi, hoặc 3 lần liền ra bản trùng | `single`/`batch`: dừng job, yêu cầu dán script tay. `auto`: tự lấy script viết sẵn từ `data/topic_bank.csv`, không dừng |
| Giọng đọc (edge-tts) | Lỗi mạng thoáng qua (`NoAudioReceived`, ...) | Tự retry tối đa 3 lần (exponential backoff) |
| Visual (Pexels + Pixabay) | Không có `PEXELS_API_KEY`/`PIXABAY_API_KEY` hoặc không đủ kết quả | Các cảnh xen kẽ giữa các nguồn đang có key (5 cảnh + cả 2 key → 3 Pexels + 2 Pixabay); mỗi cảnh thử lần lượt vài từ khoá, nguồn này không có thì nguồn kia bù; cảnh nào vẫn thiếu thì lấy ngẫu nhiên từ `data/assets_local/videos/`; nếu thư mục đó cũng trống, tự sinh ảnh nền màu trơn bằng ffmpeg — **không bao giờ trả về danh sách rỗng** |
| Nhạc nền | Luôn có, không phụ thuộc key/mạng | Dùng file nhạc của bạn nếu có, không thì tự tổng hợp — xem mục 5.1 |

`data/assets_local/videos/*.mp4` (gradient tối chuyển động chậm + grain/vignette)
hiện là **placeholder tự sinh bằng ffmpeg** — không lấy từ nguồn nào khác nên
không dính bản quyền, đủ đẹp để đăng thật, không chỉ để test. Muốn B-roll là
cảnh quay/người thật, đăng ký `PEXELS_API_KEY` và/hoặc `PIXABAY_API_KEY` (mục
1.3) — `visual_fetcher.py` tự ưu tiên dùng ngay khi có key, không cần đổi code.

### 5.1 Nhạc nền — tự chọn theo "tâm trạng" chủ đề, tự sinh 100% (không bản quyền)

**Ưu tiên nhạc thật của bạn:** nếu có file `.mp3/.wav/.m4a/.ogg` trong
`data/assets_local/music/` (hoặc `data/assets_local/music/<mood>/` cho mood cụ
thể), pipeline dùng file đó (chọn ngẫu nhiên). Chỉ dùng nhạc bạn có giấy phép
sử dụng thương mại. Track thật thường to hơn track tự tổng hợp — nhớ giảm
`music.volume_db` (khoảng -15 đến -20).

Nếu không có file nào, `src/music_composer.py` **không tải nhạc từ đâu cả** —
nó tự tổng hợp bằng ffmpeg 1 bản ambient pad (chuỗi 4 hợp âm, stereo, nhiều
giọng lệch nhẹ + hài âm, nền noise, echo/reverb) nên **chắc chắn không dính
bản quyền** (không có cách nào "tìm nhạc free trên mạng khớp chủ đề" mà đảm
bảo an toàn bản quyền 100% — kể cả nhạc gắn nhãn "free" vẫn có thể bị Content
ID nhầm hoặc đổi điều khoản). Chất lượng của bản tự tổng hợp chỉ ở mức nền
ambient — nhạc thật sẽ hay hơn.

Mỗi chủ đề được phân loại vào 1 trong 5 "tâm trạng" dựa trên từ khóa trong
topic/script (`_MOOD_KEYWORDS` trong `music_composer.py`):

| Mood | Khớp với chủ đề kiểu | Cảm giác nhạc |
|---|---|---|
| `mysterious` (mặc định) | thao túng, bí mật, kiểm soát | tối, chậm, hợp âm thứ |
| `tense` | sợ hãi, nguy hiểm, mất mát, khẩn cấp | nhanh hơn, gấp gáp |
| `curious` | "vì sao...", nghịch lý, ngạc nhiên | sáng, mở |
| `warm` | tin tưởng, kết nối, lời khuyên | ấm, chậm rãi |
| `playful` | đồ vật, tình huống hài hước nhẹ | vui, nhịp nhanh |

Mỗi mood chỉ tổng hợp 1 lần rồi cache vào
`data/assets_local/music/generated/mood_<tên>_v2.mp3` để không phải sinh lại mỗi
lần chạy trùng mood. Muốn nhạc "riêng biệt" cho từng video (không chỉ theo
mood) thì cần 1 dịch vụ AI sinh nhạc trả phí (vd. Suno) — ngoài phạm vi hiện
tại vì phải cân nhắc thêm chi phí + điều khoản bản quyền của dịch vụ đó.

---

## 6. Cấu hình (`config/settings.yaml`)

Chỉnh trực tiếp file này để đổi: độ phân giải/fps, giới hạn thời lượng, voice
(`edge_voice_en` / `edge_voice_vi`, hiện là `en-US-AndrewNeural`), font/màu/cỡ
chữ/tô vàng và số từ tối đa mỗi caption, title card + CTA (`hook:`), số cảnh
tối đa, độ dài crossfade và mức pan (`visuals:`), thư mục nhạc + volume (dB),
nguồn viết script (`script.provider`, `cli_model`, `cli_timeout_sec`).

---

## 7. Giới hạn kỹ thuật cần biết

- **edge-tts gọi endpoint TTS không chính thức của Microsoft Edge** — cần
  internet không bị chặn egress, không chạy được trong sandbox/CI bị hạn chế
  network. Chạy tốt trên máy cá nhân hoặc VPS thông thường.
- edge-tts đôi khi trả lỗi thoáng qua (`NoAudioReceived`) khi gọi liên tiếp
  nhiều request trong batch — pipeline đã tự retry, nhưng nếu vẫn fail, dòng
  đó chỉ bị đánh dấu `failed` trong CSV, không làm crash batch job; chạy lại
  `python main.py batch --file data/topics.csv` (không `--resume`) sẽ **không**
  tự retry dòng `failed` — bạn cần đổi status đó lại thành `pending` (hoặc
  chạy `single --resume` trực tiếp vào `job_dir` của dòng đó).
- **Lưu ý pháp lý:** edge-tts dùng API không công khai/không chính thức của
  Microsoft — phù hợp để thử nghiệm và sản xuất số lượng lớn ban đầu, nhưng
  **không có SLA hay cam kết pháp lý về quyền sử dụng thương mại lâu dài**.
  Khi kênh Noggora bắt đầu kiếm tiền ổn định, khuyến nghị chuyển sang
  **Azure Cognitive Services Speech (TTS chính thức)** — cùng chất lượng
  giọng Neural, có hợp đồng dịch vụ và giới hạn sử dụng rõ ràng.
- **ElevenLabs (đang bật: `voice.provider: elevenlabs`, giọng George):** gói free
  chỉ cho dùng các giọng **premade** qua API (giọng trong Voice Library báo
  `402 paid_plan_required`) và có ~10.000 ký tự/tháng — khoảng 15 video, mỗi video
  ~640 ký tự; xem đã dùng bao nhiêu: `GET /v1/user/subscription`. **Trước khi đăng
  lên kênh kiếm tiền, tự kiểm tra điều khoản của ElevenLabs:** theo hiểu biết của
  tôi gói free không có quyền thương mại (và yêu cầu ghi nguồn) — tôi chưa xác
  minh lại điều khoản hiện hành. Nếu hết hạn mức, lỗi, hoặc không muốn dùng: đặt
  `voice.provider: edge_tts` là quay về như cũ, không phải đổi gì khác.
- Mỗi lần render lại (kể cả `single --resume`) là **gọi lại ElevenLabs và tốn thêm
  ký tự**. Muốn chỉ ghép lại hình/âm mà không tốn ký tự thì dùng lại `voice.mp3` có sẵn.
- Âm lượng cuối mỗi video được chuẩn hoá về `audio.target_lufs` (mặc định -14 LUFS)
  vì các giọng đọc khác nhau ra mức to nhỏ rất khác nhau (ElevenLabs nhỏ hơn
  edge-tts ~4 dB).
- Nếu audio sinh ra dài hơn `video.max_duration_sec` (mặc định 45s), pipeline
  **không tự cắt** — chỉ log warning để bạn biết mà viết script ngắn hơn cho
  lần chạy tiếp theo (tránh mất mát nội dung do cắt tự động).

---

## 8. Kiểm thử nhanh từng module độc lập

Mỗi module trong `src/` có thể chạy riêng để debug nhanh (không cần chạy cả
pipeline):

```bash
python -m src.utils              # kiểm tra ffmpeg/ffprobe + slugify + load config
python -m src.voice_generator    # sinh voice.mp3 + voice.srt mẫu vào output/_selftest_voice/
python -m src.subtitle_burner    # convert srt mẫu -> ass đã style
python -m src.video_assembler    # ghép ass + voice mẫu với 3 ảnh nền màu trơn -> final.mp4
python -m src.visual_fetcher     # test fetch Pexels/fallback local
python -m src.script_generator   # test sinh script cho 1 topic mẫu (hoặc manual mode nếu không có nguồn nào)
python -m src.daily_topic        # Claude viết thử chủ đề + script hôm nay (kèm kiểm tra trùng, không lưu, không render)
python -m src.topic_bank         # xem chủ đề kế tiếp mà `auto` sẽ chọn (tự tạo lô mới nếu cần)
python -m src.music_composer     # xem mood được chọn cho vài chủ đề mẫu + sinh thử 1 track
python -m src.pipeline           # chạy full 1 job mẫu end-to-end
```
