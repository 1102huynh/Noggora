# Noggora Pipeline

CLI pipeline chạy local: nhận vào **1 chủ đề** (topic string), xuất ra **1 file
video dọc .mp4 (1080x1920)** sẵn sàng đăng TikTok/YouTube Shorts/IG Reels/FB
Reels — script → giọng đọc AI → phụ đề burn-in → hình nền/B-roll → nhạc nền →
ghép video.

Kênh: **Noggora** — *"One question. One amazing answer."* Mỗi video là **một câu
hỏi** và **một câu trả lời** khiến người xem bất ngờ, thuộc 6 chuyên mục:
**Psychology, Science, Space, World, Technology, What if?** (xoay vòng đều). Video
faceless, hook 3 giây đầu, **độ dài do nội dung script quyết định nhưng tối đa 1
phút** (thường 30–55 giây). **Giọng đọc luôn là tiếng Anh; chữ trên video là tiếng
Việt** (`subtitle.language: vi`) cho người xem Việt, người nước ngoài nghe tiếng Anh. Pipeline hỗ trợ cả tiếng Anh và tiếng Việt (đổi `--lang` +
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

1. **Chọn chuyên mục** theo vòng xoay đều (mục 2.0), rồi **Claude viết một câu
   hỏi + một câu trả lời (script) + mô tả bài đăng + từ khoá tìm hình mới**
   (`src/daily_topic.py`) — không sinh cả lô trước. Dùng lệnh `claude -p`
   (Claude Code CLI) bằng **tài khoản Claude Code bạn đang đăng nhập**, không cần
   `ANTHROPIC_API_KEY`; tốn hạn mức của gói, mỗi lần khoảng 1–2 phút (viết + rà
   soát sự thật).
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

Nội dung do AI viết — nên đọc lướt script + mô tả trước khi đăng. Bước rà soát sự
thật giảm rủi ro nhưng vẫn là Claude tự kiểm tra lại chính nó; hình B-roll là clip
stock nên có thể lệch nhẹ hoặc lộ logo/biển hiệu mà bộ lọc không nhận ra được.

### 2.0a Đăng lên YouTube: `post.youtube.txt`

Mỗi video có sẵn file `output/<job>/post.youtube.txt` xếp theo từng khối để copy-dán (đường dẫn
cũng được in cuối lệnh `auto`):

- **Tiêu đề:** câu hỏi tiếng Việt (kèm số ký tự, YouTube tối đa 100; quá dài thì có cảnh báo).
- **Mô tả:** các dòng **tiếng Việt trước** (chỉ ~2 dòng đầu hiện ở màn hình đề xuất nên câu hỏi trùng
  tiêu đề được bỏ đi để không phí chỗ), dòng "Vietsub — giọng đọc tiếng Anh, phụ đề tiếng Việt.",
  rồi **bản tiếng Anh** (giúp người tìm bằng tiếng Anh và người Việt đang luyện nghe), rồi **một dòng
  hashtag**: hashtag tiếng Việt lên trước (3 cái đầu hiện phía trên tiêu đề), có `#vietsub`, luôn có
  `#shorts`, tối đa 12 cái (YouTube bỏ qua TẤT CẢ nếu quá 15).
- **Bản tiếng Anh** để dán vào YouTube Studio → Phụ đề → Thêm ngôn ngữ → English (người xem quốc tế
  thấy đúng ngôn ngữ của họ), và các ghi chú cài đặt (ngôn ngữ video = English).
- Chữ "Vietsub" đặt ở đâu: `post.youtube.vietsub` = `description` (mặc định) | `title` (thêm
  "(Vietsub)" vào tiêu đề, tự bỏ nếu vượt 100 ký tự) | `both` | `none`. Chưa có bản dịch tiếng Việt thì
  file là bản tiếng Anh và có ghi chú.

### 2.0 "One question. One amazing answer." — chuyên mục, độ dài, chất lượng

- **6 chuyên mục** (`content.categories` trong config): Psychology, Science, Space,
  World, Technology, What if? Mỗi lần `auto` chọn chuyên mục **lâu chưa dùng nhất**
  (theo cột `category` trong `data/topic_bank_used.csv`), nên bỏ lỡ vài ngày không
  làm lệch vòng; ép một chuyên mục bằng `python main.py auto --category space`.
  Bỏ một chuyên mục khỏi config là không làm nó nữa. "What if?" luôn có tiêu đề bắt
  đầu bằng "What if" và câu trả lời là điều vật lý/hoá/sinh *thực sự* dự đoán.
- **Một câu hỏi, một câu trả lời:** tiêu đề là câu hỏi; script là câu trả lời, theo
  cấu trúc *hook → câu trả lời (nói sớm, nói thẳng) → một bằng chứng/ví dụ → một câu
  chốt đổi cách nhìn*, không lan man, không kể lể nhiều lớp giải thích.
- **Độ dài theo nội dung, tối đa 1 phút** (`video.max_duration_sec: 60`). Claude viết
  `script.min_words` 60 → `max_words` 120 từ (giọng ElevenLabs 0.85 đọc ~2.25–2.5
  từ/giây: 105 từ = 44s, 113 từ = 45s; câu hỏi được đọc trước thêm ~10 từ nên script tối đa 120 từ ≈ 52–58s cả video)
  và được dặn rõ đây là trần cứng. **Lưới an toàn:** nếu giọng đọc vẫn vượt 60s thì tự **tăng tốc nhẹ** (giữ
  cao độ, tối đa +25%, không tốn thêm ký tự ElevenLabs) cho vừa, timing caption tự
  co theo; cần tăng tốc hơn mức đó thì để nguyên và chỉ cảnh báo. Tắt bằng
  `video.enforce_max_duration: false`. Muốn cho phép video dài hơn: nâng
  `max_duration_sec` và `max_words` (hệ thống đã chạy thử được video 2 phút 46 giây).
- **Chọn chủ đề có câu trả lời đã được xác lập *và* gây bất ngờ.** Bước rà soát sự
  thật giờ có thể **từ chối cả chủ đề** (không chỉ hạ giọng câu chữ): nếu câu trả lời
  cốt lõi còn tranh cãi giữa các chuyên gia, hoặc chấm "wow" dưới `script.min_wow`
  (mặc định 3/5), Claude được báo lý do và phải chọn chủ đề khác (tối đa 3 lần).
- **Nhận diện:** nhãn chuyên mục (chữ + gạch vàng) trên title card và ảnh bìa, màu
  vàng-cam #FAB032 lấy từ banner kênh cho từ đang đọc/CTA, CTA cuối video là khẩu hiệu.

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
`data/topic_bank_used.csv`: chủ đề, script, từ khoá hình, chủ thể của video (cột
`effect`: hiệu ứng tâm lý, hành tinh, công nghệ, kịch bản...), chuyên mục (cột
`category`) và giờ `used_at`. Script cũng nằm trong `output/<job>/script.txt`
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
├── voice.words.json     # timing từng từ (tiếng Anh) của mỗi cụm (để tô từ đang đọc)
├── voice.intro.json       # câu hỏi được đọc trước script + thời điểm kết thúc (title card bám theo)
├── voice.vi.srt           # phụ đề TIẾNG VIỆT (dùng làm phụ đề rời trên YouTube nếu muốn)
├── voice.vi.words.json    # timing của caption tiếng Việt
├── translation.vi.json    # bản dịch đã lưu (render lại cùng script không hỏi Claude lại)
├── voice.ass           # phụ đề đã style + title card mở đầu + CTA cuối (theo config/settings.yaml)
├── clips/                # B-roll đã tải (Pexels/Pixabay) hoặc copy từ data/assets_local/
├── final.mp4              # ✅ video hoàn chỉnh, sẵn sàng đăng
├── post.youtube.txt       # ✅ ĐÃ XẾP SẴN để dán vào YouTube (tiêu đề Việt, mô tả Việt + Anh, hashtag, bản dịch Anh)
├── post.txt                # tiêu đề + mô tả thô (tiếng Anh, rồi tiếng Việt)
├── title.txt               # riêng tiêu đề
├── description.txt         # riêng mô tả
├── title.vi.txt / description.vi.txt   # bản tiếng Việt (nếu có)
├── cover.png               # ảnh bìa 1080x1920 (khung mở đầu + tên kênh + tiêu đề)
├── factcheck.txt           # Claude đã sửa/ghi chú gì ở bước kiểm tra sự thật
├── entry.json              # dữ liệu lần sinh (từ khoá hình, mô tả...) để `single --resume` render lại y hệt
└── job_log.json            # log từng bước + danh sách cảnh (khoảng thời gian → clip nào)
```

### 4.1 Video được dựng như thế nào

- **Cảnh theo câu:** `src/scenes.py` chia giọng đọc thành các cảnh dài ~8 giây (tối
  thiểu 5 cảnh, tối đa `visuals.max_clips` = 40; video 1 phút ≈ 7–8 cảnh), cắt ở
  khoảng lặng giữa các câu (câu quá dài mới cắt giữa câu), độ dài các cảnh gần bằng
  nhau. Mỗi cảnh có 1 clip B-roll riêng, tìm theo nội dung cảnh đó.
- **Từ khoá tìm hình**, theo thứ tự ưu tiên: (1) **1 cụm cho mỗi cảnh do Claude viết
  sau khi có giọng đọc**, từ đúng lời đang đọc trong cảnh đó (`visuals.plan_keywords`);
  (2) các cụm Claude viết cùng script (9–30 cụm, rải đều theo số cảnh; lưu ở cột
  `visual_keywords`); (3) vật cụ thể được nhắc trong câu (movie, moon, satellite,
  clock... — bảng `_CONCEPT_MAP`); (4) chủ đề dự phòng theo chuyên mục (Space →
  "galaxy stars"...). Cảnh lẻ ưu tiên Pexels, cảnh chẵn ưu tiên Pixabay (nguồn kia
  bù nếu thiếu); các cảnh được tải **song song** (`visuals.fetch_workers`).
- **Chọn clip theo độ khớp:** mỗi kết quả được chấm theo số từ khoá trùng với tags
  của Pixabay / phần mô tả trong URL của Pexels (bỏ qua các từ chung như "hand",
  "holding"); clip đủ khớp mới được lấy, không thì thử từ khoá tiếp theo, cuối cùng
  mới lấy clip khớp nhất tìm được. **Danh sách chặn** (`_BLOCKED_TERMS`): không bao
  giờ dùng clip có tags thuốc lá, ma tuý, vũ khí, máu, khoả thân, hay bò sát/nhện/
  côn trùng. Bộ lọc chỉ đọc tags nên **không nhận ra logo/thương hiệu trong hình**.
- **Nhìn thống nhất:** cùng 1 bộ chỉnh màu (hơi tối, ngả tím) + vignette cho mọi
  clip; clip dọc pan chậm; clip ngang hiện trong khung vuông trên nền mờ; các clip
  crossfade 0.25s.
- **Hook — câu hỏi được đọc trước:** giọng đọc **câu hỏi (tiêu đề) trước, rồi mới vào script**, trong cùng một lần đọc (`voice.read_title`). Title card (nhãn chuyên mục + câu hỏi, chữ Việt nếu `subtitle.language: vi`) hiện **đúng suốt lúc câu hỏi được đọc** rồi mờ đi; caption chỉ bắt đầu sau đó, không lặp lại câu hỏi. Claude được dặn không viết lại câu hỏi ở đầu script (script lặp lại sẽ bị từ chối và viết lại); script tự viết/cũ mà đã mở đầu bằng chính câu hỏi đó thì tự bỏ qua, không đọc hai lần. Thời điểm câu hỏi kết thúc lưu ở `voice.intro.json`. 2.5s cuối video có dòng CTA "One question. One amazing answer."
- **Caption:** cụm ≤ 6 từ (tiếng Việt ≤ 7), cỡ chữ 68 (tiếng Việt 62); cả cụm luôn hiện và **từ đang được đọc đổi màu vàng** (karaoke, tắt bằng `subtitle.karaoke: false`).
- **Chữ trên video là tiếng Việt** (`subtitle.language: vi`, mặc định; `en` để quay về tiếng Anh): sau khi có giọng đọc, Claude dịch tiêu đề + mô tả + **từng câu** trong 1 lần gọi (dịch theo câu chứ không theo cụm để câu tiếng Việt tự nhiên), rồi cắt lại thành caption và rải thời gian theo đúng khoảng câu tiếng Anh đang được đọc (`src/translate.py`). Trong 1 câu thời gian chỉ xấp xỉ (lệch tối đa khoảng 1 giây), giữa các câu thì khớp chính xác. Title card, ảnh bìa và `post.txt` đều có bản tiếng Việt; dịch lỗi thì tự dùng caption tiếng Anh. **Lưu ý:** người xem tắt tiếng mà không biết tiếng Việt sẽ không đọc được gì; nếu cần cả hai, đặt `language: en` cho bản đăng quốc tế.
- **Âm thanh:** stereo; nhạc nền tự hạ khi có giọng đọc (sidechain ducking); có tiếng `hit` trầm khi title card hiện (`src/sfx.py`, tự tổng hợp; bỏ `hit.*` của bạn vào `data/assets_local/sfx/` để dùng thay). Tiếng `whoosh` ở chỗ chuyển clip đã **tắt** vì nghe chói tai — bật lại bằng `sfx.transitions: true`.
- **Ảnh bìa:** `cover.png` để chọn làm cover trên TikTok/Shorts/Reels (mục `cover:` trong config).
- **Kiểm tra sự thật + chấm "wow":** sau khi viết, Claude rà soát script + caption thêm 1 lượt (`script.fact_check`): chi tiết sai lệch nhỏ thì sửa câu đó, số liệu/nghiên cứu không kiểm chứng được thì bỏ; nếu **câu trả lời cốt lõi** còn tranh cãi hoặc điểm "wow" dưới `script.min_wow` thì **từ chối cả chủ đề** để chọn cái khác. Ghi chú ở `factcheck.txt`. Thêm khoảng 20 giây mỗi lần chạy.
- **Nếu nâng trần lên vài phút:** nhạc ngắn hơn video được nối các bản sao **crossfade 3 giây** thay vì lặp cứng; filter graph dài (>8.000 ký tự) được ghi ra file rồi đưa cho ffmpeg (`-/filter_complex`, cần ffmpeg ≥ 7) vì Windows giới hạn ~32.000 ký tự cho cả dòng lệnh. Đã chạy thật với script 377 từ: video 2:46, 21 cảnh, render ~5 phút, file ~106 MB, `clips/` ~320 MB.

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

**Nhạc của bạn, theo thể loại và theo thứ tự.** Bỏ file (`.mp3/.wav/.m4a/.ogg/.flac`)
vào thư mục con của `data/assets_local/music/`:

| Thư mục | Chủ đề kiểu |
|---|---|
| `mysterious/` (mặc định khi không rõ) | thao túng, bí mật, ảo giác, điều ta không để ý |
| `tense/` | sợ hãi, áp lực, mất mát, bị phán xét, stress |
| `curious/` | "vì sao…", trí nhớ, nhận thức, những điều lạ trong đời thường |
| `warm/` | tin tưởng, quan hệ, tử tế, kết nối, hy vọng |
| `playful/` | nhẹ nhàng, vui, thói quen ít quan trọng |

- **Thể loại theo chủ đề:** Claude tự chọn thể loại khi viết bài (trường `mood`, lưu
  trong `entry.json`); chủ đề bạn tự đưa vào (`single`) hoặc lấy từ bank thì đoán theo
  từ khoá trong câu chủ đề (`_MOOD_KEYWORDS`).
- **Theo thứ tự tên file:** trong mỗi thư mục, video đầu dùng bài đầu, video sau dùng
  bài kế tiếp, hết thì quay lại bài đầu. Đánh số ở đầu tên file để điều khiển thứ tự
  (`01 - …`, `02 - …`; `2` đứng trước `10`). Bài đã dùng lần cuối được nhớ trong
  `data/music_state.json` (xoá file để bắt đầu lại từ bài đầu); chỉ ghi nhớ khi video
  làm xong, nên video lỗi không làm mất lượt.
- **Thư mục trống** thì thể loại đó dùng nhạc tự tổng hợp (bên dưới). Còn file nhạc để
  thẳng trong `music/` (không nằm thư mục nào) chỉ được dùng khi thư mục thể loại trống.
- **Âm lượng tự cân:** nhạc luôn được đặt nhỏ hơn giọng đọc đúng `music.below_voice_db`
  (mặc định 12 dB LUFS ≈ 15 dB khi nghe thực tế ở lúc nghỉ giữa các câu), theo độ to đo
  được của cả giọng lẫn bài nhạc, nên bài nhạc to hay nhỏ, giọng edge hay ElevenLabs đều
  đúng; nhạc còn tự hạ thêm khi có tiếng nói. Không cần chỉnh âm lượng file trước.
- Tên bài đã dùng cho mỗi video ghi trong `job_log.json` (`music_used`, `music_mood`,
  `music_source`). Trong `data/assets_local/music/` có file `_DOC_TOI_DAY.txt` và các file
  `_GOI_Y_TEN_BAI.txt` (gợi ý bài trên Pixabay Music cho từng thể loại).
- Chỉ dùng nhạc bạn có quyền dùng thương mại, và lưu lại link/giấy phép từng bài.

Nếu không có file nào, `src/music_composer.py` **không tải nhạc từ đâu cả** —
nó tự tổng hợp bằng ffmpeg 1 bản ambient pad (chuỗi 4 hợp âm, stereo, nhiều
giọng lệch nhẹ + hài âm, nền noise, echo/reverb) nên **chắc chắn không dính
bản quyền** (không có cách nào "tìm nhạc free trên mạng khớp chủ đề" mà đảm
bảo an toàn bản quyền 100% — kể cả nhạc gắn nhãn "free" vẫn có thể bị Content
ID nhầm hoặc đổi điều khoản). Chất lượng của bản tự tổng hợp chỉ ở mức nền
ambient — nhạc thật sẽ hay hơn.

Nhạc tự tổng hợp cũng chia theo 5 thể loại trên; phân loại theo từ khoá trong
câu chủ đề (`_MOOD_KEYWORDS` trong `music_composer.py`) khi chưa có `mood` từ Claude:

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
  `402 paid_plan_required`) và có ~10.000 ký tự/tháng. **Mỗi video tốn ~6 ký tự/từ:** 113 từ ≈ 700 ký tự, nên gói free (10.000/tháng) đủ khoảng 12–14 video 1 phút; xem đã dùng bao nhiêu: `GET /v1/user/subscription`. **Trước khi đăng
  lên kênh kiếm tiền, tự kiểm tra điều khoản của ElevenLabs:** theo hiểu biết của
  tôi gói free không có quyền thương mại (và yêu cầu ghi nguồn) — tôi chưa xác
  minh lại điều khoản hiện hành. Nếu hết hạn mức, lỗi, hoặc không muốn dùng: đặt
  `voice.provider: edge_tts` là quay về như cũ, không phải đổi gì khác.
- Mỗi lần render lại (kể cả `single --resume`) là **gọi lại ElevenLabs và tốn thêm
  ký tự**. Muốn chỉ ghép lại hình/âm mà không tốn ký tự thì dùng lại `voice.mp3` có sẵn.
- Âm lượng cuối mỗi video được chuẩn hoá về `audio.target_lufs` (mặc định -14 LUFS)
  vì các giọng đọc khác nhau ra mức to nhỏ rất khác nhau (ElevenLabs nhỏ hơn
  edge-tts ~4 dB).
- Nếu audio sinh ra dài hơn `video.max_duration_sec` (mặc định 60s), pipeline
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

### 8.1 Bộ test tự động (`tests/`)

```bash
pip install -r requirements-dev.txt     # thêm pytest
python -m pytest                        # ~260 test, ~30 giây, không cần mạng
```

Không gọi mạng, không đụng `data/` hay `.env`: Claude/CLI được giả lập, video và âm thanh
test được tạo bằng ffmpeg ở kích thước rất nhỏ trong thư mục tạm. Test cần ffmpeg (đánh dấu
`ffmpeg`) tự bỏ qua nếu máy không có.

| File | Bảo vệ điều gì |
|---|---|
| `test_scenes.py` | chia cảnh liền mạch, đều nhau, ưu tiên cắt ở khoảng lặng giữa câu |
| `test_daily_topic.py` | phát hiện chủ đề/hiệu ứng/nội dung trùng, đọc JSON của Claude, vòng sinh lại có phản hồi, khi nào trả `None` để `auto` dừng |
| `test_music_composer.py` | thư mục theo thể loại, phát theo thứ tự tên file + quay vòng, không "ăn" lượt khi video lỗi, mood theo chủ đề |
| `test_captions.py` | chia cụm caption, tô tên hiệu ứng, karaoke từng từ, title card + CTA |
| `test_visual_fetcher.py` | rải từ khoá theo cảnh, chấm độ khớp clip, chọn nguồn/clip, chịu lỗi tìm kiếm |
| `test_topic_bank_llm_utils.py` | lịch sử chủ đề + nâng cấp header CSV, chọn nguồn viết script, gọi CLI đúng cách, đo LUFS |
| `test_video_assembler.py` | **ghép video thật bằng ffmpeg** với mọi tổ hợp giọng/nhạc/SFX (stereo, đúng độ dài, cân mức nhạc, ảnh bìa) |
| `test_intro.py` | đọc câu hỏi trước script (không đọc hai lần nếu script đã mở đầu bằng nó), tách câu hỏi khỏi caption, `generate_voice` với TTS giả, script lặp lại tiêu đề bị từ chối |
| `test_post_text.py` | `post.youtube.txt`: tách hashtag, thứ tự hashtag Việt trước, `#shorts` luôn có, giới hạn 100 ký tự / 12 hashtag, các chỗ đặt "Vietsub", bỏ dòng trùng tiêu đề, không có bản dịch |
| `test_translate.py` | gom câu, gọi dịch Claude (thiếu câu/tiêu đề thì từ chối để dùng tiếng Anh), rải thời gian cho caption tiếng Việt, lưu và tái dùng bản dịch, dấu tiếng Việt trên ASS |
| `test_categories_and_length.py` | xoay vòng 6 chuyên mục, luật tiêu đề (câu hỏi / "What if"), từ chối chủ đề (tranh cãi, "wow" thấp), nhãn chuyên mục, từ khoá theo cảnh, tải song song 23 cảnh, nhạc ngắn nối crossfade, filter 30 clip qua file |

Nhiều test là lỗi đã từng xảy ra thật (filter thiếu dấu phẩy, mix bị mono, mood `warm` không
có nhạc, nhạc to hơn giọng...). Đã kiểm tra ngược: đưa lại từng lỗi vào một bản sao của code
thì test tương ứng báo đỏ. **Giới hạn đã biết:** lỗi true-peak vượt +0.1 dBFS trên video
40 giây không tái hiện được ở kích thước nhỏ, nên chỉ có test cấu trúc (bộ giới hạn phải
đứng sau `loudnorm`) chứ không có test hành vi cho đúng lỗi đó.
