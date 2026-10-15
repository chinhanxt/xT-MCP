# Đóng góp cho xT

xT đang phát triển và chưa có mã nguồn sản phẩm. Trao đổi lỗi hoặc đề xuất qua GitHub Issues trước thay đổi lớn; gửi PR với phạm vi rõ ràng và cách kiểm chứng.

## Giấy phép và nguồn gốc

Mọi file mã nguồn phải có header `SPDX-License-Identifier: Apache-2.0` bằng cú pháp comment phù hợp trong 5 dòng đầu. Với script có shebang, đặt header ngay sau shebang. Mọi file `.yml`/`.yaml` phải có dòng đầu:

```yaml
# SPDX-License-Identifier: Apache-2.0
```

Chỉ thêm phụ thuộc có giấy phép OSI đã rà soát tương thích. Cập nhật [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) khi thêm/đổi phụ thuộc; không vendor code dự án khác.

## Kiểm tra

Từ thư mục gốc repo, chạy:

```sh
scripts/check_spdx.sh .
```

Script chỉ kiểm các loại file được khai báo trong script; vẫn phải kiểm header mọi file mã khác và dòng đầu YAML. Chạy kiểm thử liên quan khi có mã nguồn, ghi kết quả và giới hạn kiểm chứng trong PR. Hướng dẫn build/test sản phẩm sẽ cập nhật khi triển khai.

## DCO sign-off

Đọc [Developer Certificate of Origin 1.1](https://developercertificate.org/) trước khi ký. Mỗi commit phải có `Signed-off-by: Họ tên <email>` xác nhận quyền đóng góp theo DCO. Thêm sign-off bằng:

```sh
git commit -s
```

Dùng danh tính/email của chính người đóng góp, không ký thay người khác. Sign-off không phải chữ ký mật mã. Đóng góp được cung cấp theo Apache-2.0.

Cập nhật tài liệu và CHANGELOG khi phù hợp, tuân thủ [quy tắc ứng xử](CODE_OF_CONDUCT.md) và báo lỗ hổng theo [SECURITY.md](SECURITY.md).
