import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pymupdf
from pptx import Presentation

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def convert_to_pdf(
    source: Path,
    output_dir: Path,
    soffice: Path,
    timeout_seconds: int,
) -> Path:
    # 包括隐藏幻灯片，保持 PDF 页码与 PPTX 幻灯片序号对应。
    # 不导出备注页，避免备注额外占用 PDF 页码。
    options = {
        "ExportHiddenSlides": {
            "type": "boolean",
            "value": "true",
        },
        "ExportNotesPages": {
            "type": "boolean",
            "value": "false",
        },
        "ExportOnlyNotesPages": {
            "type": "boolean",
            "value": "false",
        },
    }

    export_filter = "pdf:impress_pdf_Export:" + json.dumps(options, separators=(",", ":"))

    # 每次转换使用独立配置目录，避免占用正在打开的
    # LibreOffice 桌面程序的配置和进程。
    with TemporaryDirectory(prefix="rag-lo-profile-") as temp_dir:
        profile_uri = Path(temp_dir).resolve().as_uri()

        command = [
            str(soffice),
            f"-env:UserInstallation={profile_uri}",
            "--headless",
            "--norestore",
            "--convert-to",
            export_filter,
            "--outdir",
            str(output_dir),
            str(source),
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"LibreOffice 转换超过 {timeout_seconds} 秒。"
                "请检查课件是否能正常打开，并查看是否存在残留转换进程。"
            ) from exc

        # 用字节写日志，避免不同 Windows 编码导致日志解码失败。
        (output_dir / "libreoffice.stdout.log").write_bytes(result.stdout)
        (output_dir / "libreoffice.stderr.log").write_bytes(result.stderr)

        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice 转换失败，退出码：{result.returncode}。请查看日志目录：{output_dir}"
            )

    pdf_path = output_dir / f"{source.stem}.pdf"

    # 某些转换失败未必表现为非零退出码，所以还要检查产物。
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"LibreOffice 未生成有效 PDF，请查看日志目录：{output_dir}")

    return pdf_path


def render_pdf(
    pdf_path: Path,
    output_dir: Path,
    expected_pages: int,
    dpi: int,
) -> list[dict]:
    pages_dir = output_dir / "pages"
    pages_dir.mkdir()

    page_records = []

    with pymupdf.open(pdf_path) as pdf:
        if pdf.needs_pass:
            raise RuntimeError("生成的 PDF 需要密码，无法渲染。")

        if pdf.page_count != expected_pages:
            raise RuntimeError(
                "页数校验失败："
                f"PPTX 有 {expected_pages} 页，"
                f"PDF 有 {pdf.page_count} 页。"
                "请检查隐藏幻灯片和导出设置。"
            )

        for page_index in range(pdf.page_count):
            page = pdf.load_page(page_index)

            # 一次只处理一页，避免把整份课件图片同时放进内存。
            pixmap = page.get_pixmap(
                dpi=dpi,
                colorspace=pymupdf.csRGB,
                alpha=False,
            )

            filename = f"page-{page_index + 1:04d}.png"
            image_path = pages_dir / filename
            pixmap.save(str(image_path))

            page_records.append(
                {
                    "page_number": page_index + 1,
                    "image_path": f"pages/{filename}",
                    "width_px": pixmap.width,
                    "height_px": pixmap.height,
                    "dpi": dpi,
                    "sha256": sha256_file(image_path),
                }
            )

            del pixmap

            print(
                f"已渲染第 {page_index + 1}/{pdf.page_count} 页",
                flush=True,
            )

    return page_records


def main() -> None:
    parser = argparse.ArgumentParser(description="将 PPTX 转换为 PDF 和逐页 PNG 图片。")
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="本地 PPTX 文件的完整路径",
    )
    parser.add_argument(
        "--soffice",
        type=Path,
        default=Path(r"C:\Program Files\LibreOffice\program\soffice.com"),
        help="LibreOffice soffice.com 的完整路径",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="图片分辨率，默认 200",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="PPTX 转 PDF 超时时间，单位秒",
    )
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    soffice = args.soffice.expanduser().resolve()

    if not source.is_file():
        parser.error(f"找不到源文件：{source}")

    if source.suffix.lower() != ".pptx":
        parser.error("本脚本只处理 .pptx 文件。")

    if not soffice.is_file():
        parser.error(f"找不到 LibreOffice：{soffice}。如果安装位置不同，请使用 --soffice 指定。")

    if not 72 <= args.dpi <= 300:
        parser.error("--dpi 必须在 72 到 300 之间。")

    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0。")

    source_hash = sha256_file(source)
    presentation = Presentation(str(source))
    expected_pages = len(presentation.slides)
    del presentation

    if expected_pages == 0:
        parser.error("这份 PPTX 没有幻灯片。")

    # 每次使用新目录，防止失败后混入上一次生成的图片。
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output_dir = PROJECT_ROOT / "storage" / "parsed" / "render-checks" / source_hash / run_id
    output_dir.mkdir(parents=True)

    print(f"源文件：{source}")
    print(f"PPTX 页数：{expected_pages}")
    print(f"输出目录：{output_dir}")
    print("正在转换 PDF，请稍候……", flush=True)

    try:
        pdf_path = convert_to_pdf(
            source=source,
            output_dir=output_dir,
            soffice=soffice,
            timeout_seconds=args.timeout,
        )

        pages = render_pdf(
            pdf_path=pdf_path,
            output_dir=output_dir,
            expected_pages=expected_pages,
            dpi=args.dpi,
        )

        # 防止转换期间源文件被其他程序修改。
        if sha256_file(source) != source_hash:
            raise RuntimeError("转换期间源文件发生变化，请重新运行。")

        manifest = {
            "schema_version": 1,
            "stage": "rendered",
            "source_filename": source.name,
            "source_sha256": source_hash,
            "created_at": datetime.now(UTC).isoformat(),
            "pdf_path": pdf_path.name,
            "pdf_sha256": sha256_file(pdf_path),
            "page_count": len(pages),
            "dpi": args.dpi,
            "pages": pages,
        }

        manifest_path = output_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    except Exception as exc:
        (output_dir / "FAILED.txt").write_text(
            f"{type(exc).__name__}: {exc}",
            encoding="utf-8",
        )
        raise

    print()
    print("渲染完成。")
    print(f"PDF：{pdf_path}")
    print(f"图片目录：{output_dir / 'pages'}")
    print(f"清单：{manifest_path}")


if __name__ == "__main__":
    main()
