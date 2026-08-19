# PLAN TRIỂN KHAI: Pipeline tự động hóa kênh "Noggora"
> File này dùng để đưa cho Claude Code (hoặc Claude extension khác) implement trực tiếp. Viết theo dạng spec kỹ thuật + checklist theo thứ tự thực hiện. Không cần hỏi lại các quyết định chiến lược (niche, tên kênh, tool stack) — các quyết định đó đã chốt, chỉ cần build đúng theo spec dưới đây.

---

## 0. Bối cảnh & Mục tiêu

Xây dựng pipeline Python chạy local (CLI), input là **1 chủ đề (topic string)**, output là **1 file video .mp4 dọc (1080x1920) hoàn chỉnh** sẵn sàng đăng lên TikTok/YouTube Shorts/Instagram Reels/Facebook Reels — gồm: script → giọng đọc AI → phụ đề burn-in → hình nền/B-roll → nhạc nền → ghép video.

Kênh: **Noggora** — niche tâm lý học/hành vi con người, video 30–45 giây, faceless, hook 3 giây đầu, giọng tiếng Anh là chính (có thể tạo bản tiếng Việt song song bằng cách đổi voice + dịch script).

Pipeline phải chạy được **hàng loạt** (batch mode: đưa vào danh sách N chủ đề, ra N video) để phục vụ sản xuất số lượng lớn.

---

## 1. Ràng buộc kỹ thuật quan trọng (đọc trước khi code)

- **edge-tts** gọi tới server TTS của Microsoft (không phải API chính thức công khai) — cần internet bình thường, không hoạt động trong môi trường sandbox bị chặn egress. Chạy tốt trên máy cá nhân/VPS có internet đầy đủ.
- Không dùng ElevenLabs làm mặc định (free tier giới hạn ~10.000 ký tự/tháng và không có quyền thương mại) — chỉ để optional/fallback cho video "hero".
- Ảnh/video nền: ưu tiên **Pexels API** (free, cần API key miễn phí tại pexels.com/api, có quyền thương mại) làm nguồn B-roll theo từ khóa. Có fallback dùng ảnh AI tạo sẵn (đường dẫn local) nếu không có kết quả phù hợp hoặc không có API key.
- Sinh script: dùng **Anthropic API** (model `claude-sonnet-4-6` hoặc theo config) nếu có `ANTHROPIC_API_KEY` trong biến môi trường; nếu không có key, pipeline vẫn phải chạy được ở "manual mode" — đọc script có sẵn từ file text do người dùng tự viết/dán vào.
- Toàn bộ pipeline phải **idempotent theo từng job**: mỗi lần chạy tạo 1 thư mục output riêng theo timestamp/slug, không ghi đè lẫn nhau, để dễ debug và tái sử dụng asset.
- Subtitle phải **burn cứng vào video** (không phải file .srt rời) vì đây là yếu tố quan trọng nhất cho retention theo research đã có.
- Output đúng chuẩn: 1080x1920, H.264, AAC audio, ≤ 45s (cắt/cảnh báo nếu script sinh ra audio dài hơn 45s).

---

## 2. Cấu trúc thư mục dự án

```
noggora-pipeline/
├── README.md
├── requirements.txt
├── .env.example
├── config/
│   └── settings.yaml          # voice mặc định, brand style, đường dẫn asset, giới hạn thời lượng
├── data/
│   ├── topics.csv              # danh sách chủ đề để chạy batch (topic, language, status)
│   └── assets_local/           # ảnh/video nền dự phòng khi không có Pexels/không tìm thấy kết quả
├── src/
│   ├── __init__.py
│   ├── script_generator.py     # sinh script từ topic (Anthropic API hoặc manual mode)
│   ├── voice_generator.py      # edge-tts: script -> mp3 + srt
│   ├── visual_fetcher.py       # Pexels API: keyword -> danh sách clip/ảnh local đã tải
│   ├── subtitle_burner.py      # srt -> ffmpeg subtitle filter (ASS style cho đẹp)
│   ├── video_assembler.py      # ghép audio + visual + subtitle + nhạc nền -> mp4 cuối
│   ├── pipeline.py             # orchestrator: chạy toàn bộ 5 bước theo thứ tự cho 1 topic
│   └── utils.py                # logging, slugify, kiểm tra ffmpeg, retry decorator
├── main.py                     # CLI: single mode + batch mode
└── output/
    └── <job_slug>/              # script.txt, voice.mp3, voice.srt, clips/, final.mp4
```

---

## 3. File cấu hình

### 3.1 `.env.example`
```
ANTHROPIC_API_KEY=            # optional — nếu trống, dùng manual script mode
PEXELS_API_KEY=                # optional — nếu trống, dùng data/assets_local/
ELEVENLABS_API_KEY=            # optional — chỉ dùng nếu VOICE_PROVIDER=elevenlabs
```

### 3.2 `config/settings.yaml`
```yaml
video:
  width: 1080
  height: 1920
  fps: 30
  max_duration_sec: 45
  min_duration_sec: 25

voice:
  provider: edge_tts            # edge_tts | elevenlabs
  edge_voice_en: en-US-AriaNeural
  edge_voice_vi: vi-VN-HoaiMyNeural
  rate: "+0%"

subtitle:
  font: "Montserrat-Bold"
  font_size: 72
  color: "&H00FFFFFF"           # trắng, định dạng ASS
  outline_color: "&H00000000"
  position: "bottom_center"     # bottom_center | middle_center

visuals:
  clips_per_video: 5
  min_clip_sec: 6
  source: pexels                # pexels | local
  fallback_dir: data/assets_local

music:
  dir: data/assets_local/music
  volume_db: -22

branding:
  channel_name: "Noggora"
  watermark: false               # true nếu muốn overlay logo góc màn hình
```

### 3.3 `data/topics.csv`
```csv
topic,language,status
"Tại sao não bộ khiến bạn nhớ rõ những lời chê hơn 10 lời khen?",vi,pending
"Why does silence after a question make people confess more?",en,pending
```
Cột `status`: `pending` → `done` / `failed` (pipeline tự cập nhật sau khi chạy, để resume batch job dở dang).

---

## 4. Spec chi tiết từng module

### 4.1 `script_generator.py`
**Hàm chính:**
```python
def generate_script(topic: str, language: str = "en", max_words: int = 110) -> str:
    """
    - Nếu có ANTHROPIC_API_KEY: gọi Anthropic API với system prompt cố định
      (hook 1 câu -> sự thật tâm lý -> giải thích ngắn -> câu chốt áp dụng được),
      trả về script thuần text, không markdown, không tiêu đề.
    - Nếu KHÔNG có key: raise ManualModeRequired exception, pipeline.py sẽ
      dừng job này lại và ghi log yêu cầu người dùng dán script tay vào
      output/<job_slug>/script.txt rồi chạy lại với flag --resume.
    - Validate: số từ <= max_words (nếu vượt, gọi lại 1 lần với instruction rút gọn).
    """
```
System prompt cố định (đưa thẳng vào code, không để người dùng phải tự viết lại mỗi lần):
> "Bạn là copywriter cho kênh short-video tâm lý học tên Noggora. Viết script {max_words} từ, ngôn ngữ {language}, giọng gần gũi không hàn lâm. Cấu trúc bắt buộc: câu 1 là hook gây tò mò hoặc nghịch lý; đoạn giữa là 1 sự thật/insight tâm lý học có căn cứ; câu cuối là 1 hành động/góc nhìn người xem áp dụng được ngay. Không thêm tiêu đề, không thêm hashtag, không markdown, chỉ trả về đúng phần lời thoại sẽ được đọc."

**Acceptance:** function trả về string sạch, không có ký tự markdown, độ dài từ trong khoảng cho phép, retry tối đa 2 lần nếu lỗi API.

---

### 4.2 `voice_generator.py`
**Hàm chính:**
```python
async def generate_voice(script: str, voice: str, out_dir: Path) -> tuple[Path, Path]:
    """
    Dùng thư viện edge-tts (edge_tts.Communicate).
    - Xuất out_dir/voice.mp3
    - Xuất out_dir/voice.srt (dùng edge_tts SubMaker để có timestamp chính xác)
    - Trả về (đường dẫn mp3, đường dẫn srt)
    - Đo thời lượng audio bằng ffprobe; nếu > max_duration_sec trong config,
      log warning (không tự cắt script, để người dùng biết mà rút gọn cho lần sau).
    """
```
**Acceptance:** file mp3 phát được, file srt có timestamp hợp lệ khớp với độ dài audio (kiểm tra bằng ffprobe duration so với timestamp cuối trong srt, sai lệch < 1s).

---

### 4.3 `visual_fetcher.py`
**Hàm chính:**
```python
def fetch_visuals(topic: str, script: str, out_dir: Path, n_clips: int) -> list[Path]:
    """
    - Sinh 3-5 từ khóa tiếng Anh từ topic/script (rule-based đơn giản: lấy noun
      chính + từ khóa niche cố định "psychology, abstract, brain, mind" ghép vào
      để kết quả Pexels luôn liên quan chủ đề tâm lý/trừu tượng).
    - Gọi Pexels API (videos search), lấy clip orientation=portrait,
      min_duration=6s, tải về out_dir/clips/.
    - Nếu PEXELS_API_KEY trống hoặc không đủ n_clips kết quả phù hợp:
      lấy ngẫu nhiên đủ số lượng còn thiếu từ data/assets_local/ (không lỗi pipeline).
    - Trả về danh sách đường dẫn clip theo đúng thứ tự sẽ dùng trong video.
    """
```
**Acceptance:** luôn trả về đúng `n_clips` file hợp lệ (video hoặc ảnh), không bao giờ trả về danh sách rỗng — đây là bước dễ fail nhất nên phải có fallback chắc chắn.

---

### 4.4 `subtitle_burner.py`
**Hàm chính:**
```python
def srt_to_ass(srt_path: Path, style_cfg: dict, out_path: Path) -> Path:
    """
    Convert srt -> ass với style lấy từ config/settings.yaml (font, size, màu,
    outline, vị trí). Dùng ass để kiểm soát style đẹp hơn srt mặc định của ffmpeg.
    Có thể dùng thư viện `pysubs2` để convert + style nhanh gọn.
    """
```
**Acceptance:** file .ass hợp lệ, mở review bằng ffmpeg preview không lỗi filter.

---

### 4.5 `video_assembler.py`
**Hàm chính:**
```python
def assemble_video(
    clips: list[Path], audio_path: Path, ass_subtitle_path: Path,
    music_path: Path | None, out_path: Path, cfg: dict
) -> Path:
    """
    Dùng ffmpeg (qua subprocess, KHÔNG dùng moviepy để tránh overhead/dependency
    nặng) để:
    1. Concat các clip nền (crop/scale to 1080x1920, cắt mỗi clip theo
       audio_duration / n_clips để tổng thời lượng visual == thời lượng voice).
    2. Overlay subtitle bằng filter `ass=ass_subtitle_path`.
    3. Mix audio: voice track (volume gốc) + music track (volume theo
       music.volume_db trong config), music tự động fade-in/out.
    4. Xuất H.264 + AAC, đúng width/height/fps trong config.
    Trả về out_path (final.mp4).
    """
```
**Acceptance:** file mp4 xuất ra mở được, đúng độ phân giải, có phụ đề hiển thị, có tiếng nói + nhạc nền, tổng thời lượng khớp ± 0.5s với audio gốc.

---

### 4.6 `pipeline.py` (orchestrator)
```python
def run_job(topic: str, language: str, cfg: dict) -> Path:
    """
    1. slug = utils.slugify(topic)[:50] + timestamp
    2. out_dir = output/<slug>/  -> tạo thư mục
    3. script = script_generator.generate_script(...)  -> lưu script.txt
       (nếu ManualModeRequired: ghi placeholder + dừng, trả status="awaiting_manual_script")
    4. voice_path, srt_path = voice_generator.generate_voice(...)
    5. clips = visual_fetcher.fetch_visuals(...)
    6. ass_path = subtitle_burner.srt_to_ass(...)
    7. final = video_assembler.assemble_video(...)
    8. Ghi log job (json) vào out_dir/job_log.json: thời gian, chi phí API
       ước tính (số token/ký tự dùng), trạng thái từng bước.
    9. Trả về đường dẫn final.mp4
    Toàn bộ 6 bước đều wrap try/except riêng, lỗi ở bước nào log rõ bước đó,
    không để 1 lỗi làm crash cả batch job (xem main.py batch mode).
    """
```

### 4.7 `main.py` (CLI)
Yêu cầu 2 mode:
```bash
# Single video từ 1 câu ý tưởng
python main.py single --topic "Vì sao im lặng khiến người khác tự thú?" --lang vi

# Batch từ data/topics.csv, chỉ chạy các dòng status=pending, tự cập nhật status
python main.py batch --file data/topics.csv --limit 10
```
- `batch` mode phải chạy tuần tự (không song song) để không vượt rate-limit của API/Pexels, có `--limit` giới hạn số video/lần chạy, có cờ `--resume` để tiếp tục job bị dừng ở bước "awaiting_manual_script".
- In ra console tiến độ dạng: `[3/10] "Vì sao im lặng..." -> output/vi-sao-im-lang-20260818-1/final.mp4 ✅`

---

## 5. `requirements.txt` (đề xuất)
```
edge-tts>=6.1
requests
pyyaml
pysubs2
python-dotenv
anthropic
tenacity        # cho retry decorator
```

---

## 6. Thứ tự implement (checklist theo phase — làm tuần tự, mỗi phase phải chạy test được độc lập trước khi sang phase sau)

- [ ] **Phase 0 — Bootstrap:** tạo cấu trúc thư mục, requirements.txt, .env.example, settings.yaml mẫu, utils.py (logging + slugify + ffmpeg check).
- [ ] **Phase 1 — Voice module trước tiên** (module ít phụ thuộc nhất, dễ test độc lập): implement `voice_generator.py`, viết test chạy với 1 đoạn text mẫu cố định (không qua script_generator), xác nhận ra đúng mp3 + srt khớp timestamp.
- [ ] **Phase 2 — Subtitle burn + Video assembler (test bằng asset giả):** dùng 2-3 ảnh tĩnh trong `data/assets_local/` làm visual giả, ghép thử với voice.mp3 ở Phase 1 để ra final.mp4 đầu tiên — xác nhận toàn bộ chain kỹ thuật (audio+subtitle+visual) hoạt động trước khi động tới API bên ngoài.
- [ ] **Phase 3 — Visual fetcher (Pexels):** implement, test với PEXELS_API_KEY thật, có fallback local đã kiểm chứng ở Phase 2.
- [ ] **Phase 4 — Script generator (Anthropic API):** implement manual-mode trước (đọc từ file text), sau đó thêm nhánh gọi API thật.
- [ ] **Phase 5 — Orchestrator `pipeline.py` + `main.py single`:** nối toàn bộ 4 module lại, chạy end-to-end với 1 topic thật.
- [ ] **Phase 6 — Batch mode:** đọc `topics.csv`, chạy tuần tự nhiều topic, cập nhật status, xử lý lỗi từng dòng không crash cả batch.
- [ ] **Phase 7 — README.md hoàn chỉnh:** hướng dẫn cài đặt (pip install -r requirements.txt, cài ffmpeg nếu chưa có), cách lấy Pexels API key free, cách set ANTHROPIC_API_KEY, ví dụ lệnh chạy single/batch, giải thích cấu trúc output/, lưu ý pháp lý về edge-tts khi kênh bắt đầu kiếm tiền ổn định (khuyến nghị chuyển sang Azure TTS chính thức).

---

## 7. Ghi chú giao cho Claude Code

- Ưu tiên code Python 3.11+, type hints đầy đủ, docstring theo đúng spec ở mục 4.
- Không cần viết unit test framework phức tạp (pytest) trừ khi được yêu cầu thêm — nhưng mỗi module ở Phase 1-4 cần có 1 đoạn `if __name__ == "__main__":` để chạy thử độc lập nhanh.
- Toàn bộ API key đọc qua `python-dotenv` từ file `.env` (không hardcode, không log ra giá trị key).
- Khi implement xong, chạy thử toàn bộ pipeline với ít nhất 1 topic tiếng Anh và 1 topic tiếng Việt (đổi `voice.edge_voice_vi`) để xác nhận cả 2 luồng ngôn ngữ đều hoạt động đúng theo chiến lược đã chốt (kênh chính tiếng Anh, kênh phụ tiếng Việt).
