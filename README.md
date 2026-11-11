# Tách Âm Thanh

Tool Windows tách giọng hát hoặc lời nói khỏi nhạc nền bằng Demucs. Có thể chọn file trên máy hoặc dán link YouTube; đầu ra là MP3 320 kbps. Tool có ba lựa chọn: chỉ nhạc, chỉ lời, hoặc xuất cả hai, và có thể cắt file kết quả thành nhiều đoạn.

## Cài trên máy Windows khác

1. Đăng nhập GitHub bằng tài khoản được cấp quyền vào kho này. Bấm **Code → Download ZIP**, rồi giải nén toàn bộ vào một thư mục trên máy (ví dụ ổ E nếu có).
2. Cài **Python 3.13 64-bit** từ [python.org](https://www.python.org/downloads/) và bảo đảm có Python Launcher (`py`) cùng Tkinter. Nếu muốn, chọn nơi cài Python ngoài ổ C ngay trong trình cài của Python.
3. Mở thư mục vừa giải nén, bấm đúp **CAI_DAT.bat**. Chờ cửa sổ báo cài đặt thành công. Máy cần Internet và đủ dung lượng để tải PyTorch cùng các thư viện (vài GB).
4. Bấm đúp **MO_TACH_AM_THANH.bat** để mở tool. Lần đầu tách, Demucs cần Internet để tải model `htdemucs` khoảng 84 MB; các lần sau dùng model đã lưu.

Mã nguồn, thư viện và model là các phần riêng: kho GitHub chỉ chứa mã và file cài đặt; thư viện và model được tải về thư mục `.runtime` của tool trên máy mới. Không sao chép môi trường `.runtime` từ máy khác vì môi trường Python có thể chứa đường dẫn của máy cũ.

Khi chạy bằng CPU, thời gian xử lý có thể dài. Tool tự chọn CUDA nếu bản PyTorch và GPU trên máy nhận được CUDA; cài đặt mặc định chưa bảo đảm bật GPU.

## Sử dụng

1. Chọn file hoặc dán link YouTube vào ô **Nguồn đầu vào**.
2. Chọn thư mục lưu, chọn loại âm thanh cần xuất và tùy chọn cắt đoạn nếu muốn.
3. Bấm **Bắt đầu tách âm thanh**, chờ đến khi tool thông báo hoàn tất.

Chất lượng tách tùy từng bản thu; MP3 320 kbps là định dạng nén, không phải lossless.

## Kiểm tra và xử lý sự cố

- Nếu chưa có Python 3.13 64-bit hoặc thiếu Tkinter, cài lại Python với các thành phần đó rồi chạy lại `CAI_DAT.bat`.
- Nếu quá trình tải thư viện thất bại, kiểm tra Internet và dung lượng, sau đó chạy lại `CAI_DAT.bat`.
- Nếu Python 3.13 không được Python Launcher nhận ra, có thể đặt biến môi trường `PYTHON_EXE` trỏ đến file `python.exe` 3.13 64-bit trước khi chạy `CAI_DAT.bat`.
- Khi link YouTube không tải được, có thể cập nhật yt-dlp bằng lệnh `.runtime\venv\Scripts\python.exe -m pip install -U yt-dlp`.

Chạy kiểm tra mã nguồn: `py -3.13 -m unittest discover -s tests -v`.

## Thành phần

- Tách nguồn âm thanh: [Demucs](https://github.com/facebookresearch/demucs), model `htdemucs`.
- Đọc và chuyển đổi âm thanh: [FFmpeg](https://ffmpeg.org/) thông qua `imageio-ffmpeg`.
- Lấy nguồn từ link: [yt-dlp](https://github.com/yt-dlp/yt-dlp).
