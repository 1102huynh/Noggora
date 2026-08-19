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
| `ANTHROPIC_API_KEY` | Không — thiếu thì pipeline chạy ở **manual script mode** | [console.anthropic.com](https://console.anthropic.com/) |
| `PEXELS_API_KEY` | Không — thiếu thì dùng riêng `PIXABAY_API_KEY` (nếu có), hoặc ảnh/video dự phòng trong `data/assets_local/` | Free tại [pexels.com/api](https://www.pexels.com/api/) (đăng ký tài khoản → tạo API key, có quyền thương mại) |
| `PIXABAY_API_KEY` | Không — có cả 2 key thì mỗi video chia đều clip từ Pexels + Pixabay (đa dạng hơn), không bắt buộc phải có key này | Free tại [pixabay.com/api/docs](https://pixabay.com/api/docs/) (đăng ký tài khoản → lấy API key, license cho phép dùng thương mại, không cần credit) |
| `ELEVENLABS_API_KEY` | Không — chỉ dùng khi đổi `voice.provider: elevenlabs` trong config | [elevenlabs.io](https://elevenlabs.io/) |

**Vì sao không lấy ảnh từ Pinterest:** Pinterest không có API để tải ảnh/video của người khác về dùng lại (API của họ chỉ để *đăng* pin lên tài khoản, không phải để *tìm & tải*), và phần lớn nội dung trên đó là người dùng ghim lại từ nơi khác, không rõ bản quyền gốc — dùng để đăng lên kênh kiếm tiền có rủi ro copyright strike thật. Pexels/Pixabay được chọn vì cả hai đều cấp license miễn phí, rõ ràng, dùng thương mại không cần credit.

Không key nào là bắt buộc để chạy thử pipeline lần đầu — mọi bước đều có
fallback (xem mục 5).

---

## 2. Chế độ tự động (zero-argument, mỗi ngày 1 clip khác nhau)

**Đây là cách chạy đơn giản nhất — không cần `ANTHROPIC_API_KEY`, không cần
nhập topic:**

```bash
python main.py auto
```

Lệnh này tự lấy **chủ đề + script kế tiếp chưa dùng** từ
[data/topic_bank.csv](data/topic_bank.csv) (25 chủ đề tâm lý học tiếng Anh đã
viết sẵn script, xáo trộn thứ tự sẵn) và chạy hết pipeline ra `final.mp4`.
Chạy lại lần sau sẽ tự lấy chủ đề *khác*, không lặp, cho đến khi dùng hết cả
25 thì tự quay vòng lại từ đầu (có log cảnh báo để bạn biết lúc đó nên bổ sung
thêm chủ đề mới vào file CSV).

- Nếu bạn **có** `ANTHROPIC_API_KEY` trong `.env`: `auto` vẫn lấy chủ đề từ
  bank, nhưng để Anthropic sinh script mới thay vì dùng script viết sẵn —
  chất lượng/đa dạng cao hơn.
- `data/topic_bank.csv` hiện chỉ có chủ đề tiếng Anh (kênh chưa đăng tiếng
  Việt) nên `--lang vi` sẽ không tìm thấy gì để chọn; muốn video tiếng Việt,
  dùng `single --topic "..." --lang vi` (mục 3) thay vì `auto`.
- Thêm chủ đề mới vào bank: mở `data/topic_bank.csv`, thêm dòng mới với cột
  `used_at` để trống (id chỉ cần là số chưa dùng).

### 2.1 Không lặp lại chủ đề — tự tạo lô mới mỗi tháng, đúng theo số ngày thật

- **Chủ đề đã dùng thì không bao giờ được chọn lại**, cho tới khi lô hiện tại
  hết sạch. `used_at` trong `data/topic_bank.csv` đánh dấu điều đó — `auto`
  không bao giờ chọn lại 1 dòng đã có `used_at`.
- Trước mỗi lần chạy, `auto` tự kiểm tra (`topic_bank.ensure_fresh_batch`,
  theo dõi qua `data/topic_bank_meta.json`): nếu lô hiện tại **hết chủ đề**
  HOẶC đã **quá số ngày của tháng lúc lô đó bắt đầu** (28/29/30/31 ngày —
  tính bằng `calendar.monthrange`, không còn cứng "30 ngày" nữa) kể từ lần
  tạo lô gần nhất (tuỳ điều kiện nào đến trước) → tự thêm 1 lô chủ đề mới vào
  cuối `topic_bank.csv`, không cần bạn làm gì.
- **Số chủ đề mỗi lô = số ngày của tháng lúc lô đó được tạo** (vd. lô tạo
  trong tháng 2 → 28 hoặc 29 chủ đề; tháng có 31 ngày → 31 chủ đề), thay vì
  cố định 30 như trước — để đúng 1 video/ngày không bị thiếu (tháng 2 dùng
  hết 30 sẽ thiếu ~2 ngày) hay dư (tháng 31 ngày dùng 30 sẽ refresh sớm 1
  ngày dù vẫn còn topic cũ).
- Lô mới lấy từ đâu:
  - **Có `ANTHROPIC_API_KEY`:** gọi Anthropic 1 lần, xin đủ số chủ đề+script
    theo đúng số ngày của tháng đó, hoàn toàn mới (kèm danh sách chủ đề đã
    dùng để tránh trùng) — chất lượng cao nhất.
  - **Không có key (mặc định hiện tại):** tự ghép từ
    [data/effects_pool.csv](data/effects_pool.csv) — kho ~60 hiệu ứng tâm lý
    học có sẵn (tên + cơ chế + ví dụ) chưa dùng ở lô hand-written đầu tiên,
    ghép qua vài mẫu câu (hook/giải thích/hành động) thành script hoàn chỉnh.
    Văn phong sẽ khuôn mẫu hơn 25 script tôi viết tay ban đầu, nhưng vẫn đúng
    cấu trúc hook→insight→hành động, đủ dùng để đăng.
- 25 chủ đề viết tay (lô 1, tiếng Anh) + 60 hiệu ứng trong effects_pool.csv
  (đủ cho ~2 lô nữa, cũng tiếng Anh) cho khoảng **3 tháng** không lặp, không
  cần đụng gì cả. Sau đó, nếu vẫn chưa có `ANTHROPIC_API_KEY`, cần bổ sung
  thêm dòng vào `effects_pool.csv` (hoặc nhờ tôi viết thêm) — nếu không, hệ
  thống sẽ quay vòng lại từ đầu (có log cảnh báo rõ ràng khi việc này xảy ra)
  thay vì dừng hẳn.
- **Giới hạn hiện tại:** kho tự sinh theo template (`effects_pool.csv`) chỉ
  hỗ trợ tiếng Anh, và `data/topic_bank.csv` hiện không còn chủ đề tiếng Việt
  nào (đã gỡ bỏ vì kênh chưa đăng tiếng Việt). Muốn có lại chủ đề tiếng Việt
  trong bank, cần `ANTHROPIC_API_KEY` (batch tự sinh chỉ tạo tiếng Anh, phải
  gọi thủ công theo hướng khác) hoặc nhờ tôi viết tay bổ sung.

### 2.2 Tự động chạy mỗi ngày, không cần bấm gì (Windows Task Scheduler)

Đã thiết lập sẵn 1 scheduled task tên **"Noggora Daily Video"**, chạy
`python main.py auto` mỗi ngày lúc **08:00** (chỉ khi máy đang đăng nhập —
không cần lưu mật khẩu, không chạy khi máy tắt/khoá màn hình lâu).

```powershell
# Kiểm tra task
Get-ScheduledTask -TaskName "Noggora Daily Video" | Get-ScheduledTaskInfo

# Chạy thử ngay (không cần chờ tới giờ)
Start-ScheduledTask -TaskName "Noggora Daily Video"

# Đổi giờ chạy / cài lại (idempotent, chạy lại là ghi đè giờ mới)
powershell -ExecutionPolicy Bypass -File setup_scheduled_task.ps1 -Time "20:00"

# Xoá task (không tự động sinh video nữa)
Unregister-ScheduledTask -TaskName "Noggora Daily Video" -Confirm:$false
```

Log mỗi lần chạy (thành công hay lỗi) được ghi vào
[logs/auto.log](logs/auto.log) (file này tự tạo, append theo thời gian) —
mở file này để xem video hôm đó đã ra chưa, hoặc lỗi ở bước nào nếu có.

Video mới luôn nằm trong `output/<slug>-<timestamp>/final.mp4` — không có
thư mục "video hôm nay" cố định, hãy sort `output/` theo thời gian sửa đổi để
lấy video mới nhất.

---

## 3. Các chế độ chạy thủ công khác (khi cần chỉ định chủ đề)

```bash
# 1 video từ 1 ý tưởng do bạn tự chọn
python main.py single --topic "Why does silence after a question make people confess more?" --lang en

# Batch từ data/topics.csv (chỉ chạy các dòng status=pending)
python main.py batch --file data/topics.csv --limit 10
```

Nếu không có `ANTHROPIC_API_KEY`, lệnh trên sẽ dừng ở bước script và in ra:

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
├── script.txt       # lời thoại (do Anthropic sinh hoặc bạn dán tay)
├── voice.mp3         # giọng đọc edge-tts
├── voice.srt          # phụ đề gốc, timestamp theo từng nhóm ~4 từ
├── voice.ass           # phụ đề đã style (font/màu/vị trí theo config/settings.yaml)
├── clips/                # B-roll đã tải (Pexels) hoặc copy từ data/assets_local/
├── final.mp4              # ✅ video hoàn chỉnh, sẵn sàng đăng
└── job_log.json            # log từng bước (ok/failed/awaiting_manual_script) + số liệu ước tính
```

Mỗi lần chạy tạo 1 thư mục riêng theo slug + timestamp — không bao giờ ghi đè
job cũ, nên bạn luôn có thể debug/tái sử dụng asset của 1 job cụ thể.

`data/topics.csv` được pipeline tự thêm cột `job_dir` (ghi lại thư mục output
của mỗi dòng) để `--resume` biết chính xác job nào cần tiếp tục.

---

## 5. Cơ chế fallback của từng bước

| Bước | Không có key/mạng lỗi | Kết quả |
|---|---|---|
| Script (Anthropic) | Không có `ANTHROPIC_API_KEY` | `single`/`batch`: dừng job, yêu cầu dán script tay. `auto`: tự lấy script viết sẵn từ `data/topic_bank.csv`, không dừng |
| Giọng đọc (edge-tts) | Lỗi mạng thoáng qua (`NoAudioReceived`, ...) | Tự retry tối đa 3 lần (exponential backoff) |
| Visual (Pexels + Pixabay) | Không có `PEXELS_API_KEY`/`PIXABAY_API_KEY` hoặc không đủ kết quả | Chia đều số clip cần cho mọi nguồn đang có key (vd. 5 clip + cả 2 key → 3 Pexels + 2 Pixabay trong cùng 1 video); nguồn nào thiếu quota thì nguồn còn lại bù; vẫn thiếu thì lấy ngẫu nhiên từ `data/assets_local/videos/`; nếu thư mục đó cũng trống, tự sinh ảnh nền màu trơn bằng ffmpeg — **không bao giờ trả về danh sách rỗng** |
| Nhạc nền | Luôn tự tổng hợp, không phụ thuộc key/mạng | Xem mục 5.1 |

`data/assets_local/videos/*.mp4` (gradient tối chuyển động chậm + grain/vignette)
hiện là **placeholder tự sinh bằng ffmpeg** — không lấy từ nguồn nào khác nên
không dính bản quyền, đủ đẹp để đăng thật, không chỉ để test. Muốn B-roll là
cảnh quay/người thật, đăng ký `PEXELS_API_KEY` và/hoặc `PIXABAY_API_KEY` (mục
1.3) — `visual_fetcher.py` tự ưu tiên dùng ngay khi có key, không cần đổi code.

### 5.1 Nhạc nền — tự chọn theo "tâm trạng" chủ đề, tự sinh 100% (không bản quyền)

`src/music_composer.py` **không tải nhạc từ đâu cả** — nó tự tổng hợp nhạc
bằng ffmpeg (3 tần số hợp âm + tremolo/lowpass/echo) nên **chắc chắn không
dính bản quyền** (không có cách nào "tìm nhạc free trên mạng khớp chủ đề" mà
đảm bảo an toàn bản quyền 100% — kể cả nhạc gắn nhãn "free" vẫn có thể bị
Content ID nhầm hoặc đổi điều khoản).

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
`data/assets_local/music/generated/mood_<tên>.mp3` để không phải sinh lại mỗi
lần chạy trùng mood. Muốn nhạc "riêng biệt" cho từng video (không chỉ theo
mood) thì cần 1 dịch vụ AI sinh nhạc trả phí (vd. Suno) — ngoài phạm vi hiện
tại vì phải cân nhắc thêm chi phí + điều khoản bản quyền của dịch vụ đó.

---

## 6. Cấu hình (`config/settings.yaml`)

Chỉnh trực tiếp file này để đổi: độ phân giải/fps, giới hạn thời lượng, voice
(`edge_voice_en` / `edge_voice_vi`), font/màu/vị trí phụ đề, số clip mỗi
video, độ dài tối thiểu mỗi clip, thư mục nhạc + volume (dB), model Anthropic
dùng để sinh script.

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
- ElevenLabs **không** dùng làm mặc định (free tier ~10.000 ký tự/tháng,
  không có quyền thương mại rõ ràng ở free tier) — chỉ nên dùng optional cho
  video "hero" nếu đổi `voice.provider: elevenlabs` và có `ELEVENLABS_API_KEY`
  trả phí.
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
python -m src.script_generator   # test sinh script qua Anthropic (hoặc manual mode nếu thiếu key)
python -m src.topic_bank         # xem chủ đề kế tiếp mà `auto` sẽ chọn (tự tạo lô mới nếu cần)
python -m src.music_composer     # xem mood được chọn cho vài chủ đề mẫu + sinh thử 1 track
python -m src.pipeline           # chạy full 1 job mẫu end-to-end
```
